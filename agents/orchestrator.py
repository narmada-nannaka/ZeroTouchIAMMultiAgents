# agents/orchestrator.py (ADK Multi-Tool Pattern)

from google.adk.agents import LlmAgent
from agents.lookup_agent import ApproverLookupAgent
from agents.context_agent import PolicyContextAgent
from agents.nlu_classifier_agent import NLUClassifierAgent
from agents.communication_agent import CommunicationAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.sessions.base_session_service import BaseSessionService
from google.genai import types 
import logging
import json
import httpx
import google.auth.transport.requests
import google.oauth2.id_token
from typing import Dict, Any, Callable, Optional
from google.adk.runners import Runner
import trace_events as trace
import memory_service as membank
import model_armor
import datetime
from zoneinfo import ZoneInfo

# Set up logging for visibility
logging.basicConfig(level=logging.INFO)

# HELPER FUNCTION (ADK Best Practice): Safely retrieve the tool function by name
def _get_tool_func(agent, tool_name: str) -> Callable:
    """Finds and returns the executable function for a tool by name."""
    if not hasattr(agent, 'tools') or not agent.tools:
        # NOTE: For Pydantic-based agents, 'tools' is often a property; this check covers both cases.
        raise ValueError(f"Agent {agent.name} has no tools defined.")
    
    # Iterate through the list of tools (which are FunctionTool objects)
    for tool in agent.tools:
        # Check if the tool object has a 'name' attribute matching the target
        if hasattr(tool, 'name') and tool.name == tool_name:
            # Return the underlying Python function (the 'func' attribute)
            return tool.func
    raise ValueError(f"Tool '{tool_name}' not found in Agent {agent.name}.")

class IAMOrchestrator(LlmAgent):
    """
    The orchestrator manages the 9-step IAM provisioning workflow.
    It is the only agent that holds a reference to the A2A Provisioning Agent.
    """

    @classmethod
    def create(cls, project_id: str, provisioning_service_url: str, gcp_location: str, 
               rag_engine_id: str, rag_data_store_id: str, session_service: BaseSessionService,
               sender_email: Optional[str] = None, approval_callback_url: Optional[str] = None):
        """
        Factory method to create IAMOrchestrator with external dependencies.
        This avoids Pydantic validation errors when passing non-field parameters.
        """
        # Build sub-agents
        lookup_agent = ApproverLookupAgent(project_id=project_id)
        context_agent = PolicyContextAgent(project_id=project_id, location=gcp_location, engine_id=rag_engine_id, data_store_id=rag_data_store_id)
        nlu_agent = NLUClassifierAgent(project_id=project_id)

        # Normalise to base URL - handle potential empty or None string
        base_url = (provisioning_service_url or "").rstrip("/")
        if not base_url:
            logging.warning("⚠️ provisioning_service_url is empty! Remote agent calls will fail.")

        # Create custom auth class that refreshes tokens automatically
        class GCPAuth(httpx.Auth):
            """Custom httpx Auth class for GCP OIDC tokens"""
            def __init__(self, audience: str):
                self.audience = audience
                
            def auth_flow(self, request):
                """Add Authorization header with fresh OIDC token to each request"""
                try:
                    # Skip token generation if audience is missing (prevents crash on local dev)
                    if not self.audience:
                        logging.warning("⚠️ No audience set for GCPAuth - skipping token generation")
                        yield request
                        return
                    auth_req = google.auth.transport.requests.Request()
                    token = google.oauth2.id_token.fetch_id_token(auth_req, self.audience)
                    request.headers['Authorization'] = f'Bearer {token}'
                    logging.info(f"🔐 Added auth token to request: {request.url}")
                except Exception as e:
                    logging.error(f"❌ Failed to add auth token: {e}")
                    # Continue anyway - let the server reject it with proper error
                yield request

        # Create authenticated httpx client
        authenticated_client = httpx.AsyncClient(
            auth=GCPAuth(audience=base_url),
            timeout=60.0,  # Increase timeout for IAM operations
            follow_redirects=True
        )

        # Remote A2A client
        # to_a2a() serves the provisioner card at /.well-known/agent-card.json
        agent_card_url = f"{base_url}/.well-known/agent-card.json"
        logging.info(f"Initializing RemoteA2aAgent with agent card URL: {agent_card_url}")

        remote_provisioning_agent = RemoteA2aAgent(
            name="iam_provisioner_client",
            description="Remote agent that handles secure IAM provisioning operations",
            agent_card=agent_card_url,
            httpx_client=authenticated_client,
            use_legacy=False,
        )

       # 4. Instantiate the Agent (Pydantic Validation happens here)
        instance = cls(
            name="IAMOrchestrator",
            description="Manages the entire asynchronous IAM provisioning session.",
            sub_agents=[lookup_agent, context_agent, nlu_agent, remote_provisioning_agent],
            tools=[],  # No local tools needed - remote agent provides the execution capability
        )

        # 5. Attach session service after instantiation by *bypassing* Pydantic's setter
        instance.__dict__['session_service'] = session_service 

        # We treat CommunicationAgent as a helper/service library rather than a full ADK sub-agent
        # because its logic (sending email) is a side-effect, not a reasoning loop.
        instance.__dict__['comm_agent'] = CommunicationAgent(
            sender_email=sender_email,
            approval_callback_url=approval_callback_url
        )

        return instance
    
    async def start_provisioning(self, session_id: str, user_id: str, requested_role: str, project_scope: str, user_timezone: str = 'UTC') -> Dict[str, Any]:
        """
        Initial method called by the Pub/Sub trigger. Initiates the workflow.
        """
        
        session = await self.session_service.create_session(session_id=session_id, app_name=self.name, user_id="system")
        session.state['request'] = { "user_id": user_id, "role": requested_role, "scope": project_scope, "user_timezone": user_timezone, "status": "LOOKUP_INITIATED" }
        logging.info(f"[{session_id}] Phase 1: Lookup & Context...")
        trace.record_event(session_id, "Orchestrator", "running", "Coordinating request")

        # --- Memory Bank: short-circuit if an active (unexpired) grant already exists ---
        trace.record_event(session_id, "Memory Bank", "running", "Recalling prior grants")
        _mem_count, _active_exp = membank.check_grants(user_id, requested_role, project_scope)
        if _active_exp is not None:
            try:
                _exp_str = _active_exp.astimezone(ZoneInfo(user_timezone)).strftime('%H:%M %Z')
            except Exception:
                _exp_str = _active_exp.isoformat()
            trace.record_event(session_id, "Memory Bank", "completed", f"Active grant found - valid until {_exp_str}")
            _already_msg = f"You already have {requested_role} on {project_scope} from a recent request, active until {_exp_str}. No new request needed."
            trace.record_event(session_id, "__chat__", "info", _already_msg)
            session.state['status'] = "ALREADY_ACTIVE"
            await self.session_service.update_session(session)
            trace.record_event(session_id, "Orchestrator", "info", "Access already active - no new grant needed")
            trace.done(session_id)
            return {"status": "ALREADY_ACTIVE", "reason": _already_msg}
        trace.record_event(session_id, "Memory Bank", "completed", f"Recalled {_mem_count} prior grant(s); none active")

        # --- 1. Delegate Lookup  ---
        trace.record_event(session_id, "Lookup", "running", "Looking up approval policy")
        lookup_agent = self.find_agent("ApproverLookupAgent")
        lookup_tool_func = _get_tool_func(lookup_agent, "lookup_approvers_policy")

        lookup_result = lookup_tool_func(user_id=user_id, role_id=requested_role, project_scope=project_scope)
        if "error" in lookup_result:
            session.state['status'] = "LOOKUP_FAILED"
            trace.record_event(session_id, "Lookup", "rejected", lookup_result.get("error", "Lookup failed"))
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)
            return lookup_result

        session.state['lookup_result'] = lookup_result
        _approvers = lookup_result.get("required_approvers", [])
        trace.record_event(session_id, "Lookup", "completed", f"Approver(s): {', '.join(_approvers) if _approvers else 'none configured'}")

        # --- 2. Delegate RAG Retrieval & Compliance Check ---
        trace.record_event(session_id, "Context", "running", "Retrieving policy (RAG) and checking compliance")
        context_agent = self.find_agent("PolicyContextAgent")
        doc_id = lookup_result.get("baseline_policy_doc_id", "NOT_FOUND")
        role_id = lookup_result.get("role_id", "NOT_FOUND")

        # Execute RAG Retrieval ( _retrieve_policy_text)
        rag_tool_func = _get_tool_func(context_agent, "_retrieve_policy_text")
        policy_context = rag_tool_func(doc_type=doc_id, requested_role=role_id)

        # Execute Compliance Check (_check_temporal_compliance) using detected constraint type
        check_tool_func = _get_tool_func(context_agent, "_check_temporal_compliance")
        constraint_type = policy_context.get("constraint_type", "guardrail_deny")  # Fail-safe default
        is_compliant, compliance_reason = check_tool_func(constraint_type=constraint_type, user_timezone=user_timezone)

        session.state['policy_context'] = policy_context

        # --- Enforce Compliance Guardrail ---
        if not is_compliant:
            logging.error(f"GUARDRAIL FAIL: Request rejected: {compliance_reason}")
            session.state['status'] = "POLICY_VIOLATION_REJECTED"
            trace.record_event(session_id, "Context", "rejected", f"{constraint_type}: {compliance_reason}")
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)
            return {"status": "REJECTED", "reason": compliance_reason}

        trace.record_event(session_id, "Context", "completed", f"Constraint: {constraint_type}; {compliance_reason}")
        
        # --- 3. Delegate Communication (SEND EMAIL) ---
        
        required_approvers = lookup_result.get("required_approvers", [])
        if not required_approvers:
            trace.record_event(session_id, "Communication", "rejected", "No approvers configured")
            trace.done(session_id)
            return {"status": "ERROR", "reason": "No approvers found configuration."}

        justification_text = policy_context.get("justification_summary", "No justification.")

        trace.record_event(session_id, "Communication", "running", f"Issuing approval request to {', '.join(required_approvers)}")
        try:
            logging.info(f"[{session_id}] Sending REAL email to {required_approvers}")
            email_result = await self.comm_agent.send_approval_email(
                session_id=session_id,
                requester_email=user_id,
                requested_role=requested_role,
                project_scope=project_scope,
                approvers=required_approvers,
                justification=justification_text
            )
            session.state['email_metadata'] = email_result
            session.state['status'] = "WAITING_FOR_APPROVAL"

            # UPDATE SESSION IN DB
            await self.session_service.update_session(session)

            trace.record_event(session_id, "Communication", "completed", "Approval request issued")
            trace.record_event(session_id, "Orchestrator", "info", "Awaiting approver decision")

            return {
                "status": "WAITING_FOR_APPROVAL",
                "message": f"Email sent to {required_approvers}. Workflow paused.",
                "session_id": session_id
            }

        except Exception as e:
            logging.error(f"Email failed: {e}")
            trace.record_event(session_id, "Communication", "rejected", f"Email error: {e}")
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)
            return {"status": "EMAIL_FAILED", "error": str(e)}
        
     # --- 2. CHECK NLU AND DECIDE ---
    async def check_nlu_and_decide(self, session_id: str, raw_response_text: str, approver_email: str) -> Dict[str, Any]:
        """
        FAST PATH: Only runs NLU classification and returns decision.
        Used to quickly ACK Pub/Sub messages.
        Returns immediately after NLU, before provisioning.
        """
        logging.info(f"[{session_id}] Fast NLU check for approval decision...")
        
        # 1. Rehydrate State
        session = await self.session_service.get_session(
            app_name=self.name, 
            user_id="system",
            session_id=session_id
        )

        if not session:
            return {"status": "ERROR", "reason": "Session not found or expired."}
    
        if session.state.get('status') != "WAITING_FOR_APPROVAL":
            logging.warning(f"Session {session_id} is in state {session.state.get('status')}, not WAITING.")

        trace.record_event(session_id, "Orchestrator", "info", "Processing approver decision")

        # --- Model Armor: screen the untrusted approver reply before it reaches the LLM ---
        trace.record_event(session_id, "Model Armor", "running", "Screening approver reply")
        _ma_blocked, _ma_reason = model_armor.screen_prompt(raw_response_text)
        if _ma_blocked:
            trace.record_event(session_id, "Model Armor", "rejected", _ma_reason)
            session.state['status'] = "REJECTED_MODEL_ARMOR"
            await self.session_service.update_session(session)
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)
            return {"status": "REJECTED", "reason": _ma_reason, "should_provision": False}
        trace.record_event(session_id, "Model Armor", "completed", _ma_reason)

        # 2. CALL NLU CLASSIFIER (Fast - typically <500ms)
        nlu_agent = self.find_agent("NLUClassifierAgent")
        if not nlu_agent:
            raise ValueError("NLUClassifierAgent not found in orchestrator.")

        nlu_tool = _get_tool_func(nlu_agent, "classify_intent")
        
        logging.info(f"[{session_id}] Delegating to NLU Classifier...")
        trace.record_event(session_id, "NLU", "running", "Classifying approver response")
        nlu_result = nlu_tool(
            email_body=raw_response_text,
            sender_email=approver_email
        )

        decision_status = nlu_result.get("status", "REJECTED_CONTEXT_MISSING")
        decision_reason = nlu_result.get("reason_summary", "No reason provided.")

        logging.info(f"[{session_id}] NLU Result: {decision_status} | Reason: {decision_reason}")

        # Store NLU result immediately
        session.state['nlu_classification'] = nlu_result

        # 3. Handle Rejection (Quick path)
        if decision_status != "APPROVED":
            session.state['status'] = f"REJECTED_NLU_{decision_status}"
            await self.session_service.update_session(session)
            trace.record_event(session_id, "NLU", "rejected", f"{decision_status}: {decision_reason}")
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)
            return {
                "status": "REJECTED",
                "reason": f"NLU classified response as {decision_status}. Reason: {decision_reason}",
                "should_provision": False
            }

        trace.record_event(session_id, "NLU", "completed", f"APPROVED: {decision_reason}")
        
        # 4. APPROVED: Update status but DON'T execute yet
        session.state['status'] = "APPROVED_QUEUED_FOR_EXECUTION"
        await self.session_service.update_session(session)
        
        return {
            "status": "APPROVED",
            "reason": decision_reason,
            "should_provision": True,
            "nlu_result": nlu_result
        }

      
     # --- 3. EXECUTE ON APPROVAL ---
    async def execute_approved_provisioning(self, session_id: str, approver_email: str, nlu_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        BACKGROUND TASK: Executes provisioning after NLU approval.
        This runs asynchronously after Pub/Sub ACK is sent.
        """
        logging.info(f"[{session_id}] Starting background provisioning execution...")
        
        try:
            # 1. Reload session
            session = await self.session_service.get_session(
                app_name=self.name,
                user_id="system",
                session_id=session_id
            )
            
            if not session:
                logging.error(f"[{session_id}] Session disappeared during background execution!")
                return {"status": "ERROR", "reason": "Session not found"}
        
            req = session.state.get('request', {})
            lookup = session.state.get('lookup_result', {})
            
            # 2. Update status to executing
            session.state['status'] = "EXECUTING_PROVISIONING"
            await self.session_service.update_session(session)
            
            # 3. Execute provisioning (A2A call - this is the slow part)
            trace.record_event(session_id, "Orchestrator", "info", "Provisioning Access")
            provisioning_agent = self.find_agent("iam_provisioner_client")
            args = {
                "requested_role": req.get("role"),
                "user_id": req.get("user_id"),
                "justification": f"Approved by {approver_email}. NLU Summary: {nlu_result.get('reason_summary')}",
                "gcp_project_scope": req.get("scope"),
                "user_timezone": req.get("user_timezone", "UTC")
            }

            logging.info(f"[{session_id}] Calling remote provisioning service...")
            trace.record_event(session_id, "Provisioning", "running", "Calling secure provisioner via A2A")
            execution_result = await self._run_remote_provisioning(
                provisioning_agent, args, session_id, req.get("user_id")
            )

            if execution_result.get("status") == "POLICY_APPLIED":
                trace.record_event(session_id, "Provisioning", "completed", execution_result.get("reason", "JIT IAM policy applied"))
                _expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
                membank.record_grant(req.get("user_id"), req.get("role"), req.get("scope"), _expiry)
                trace.record_event(session_id, "Memory Bank", "completed", "Stored this grant for future recall")
            else:
                trace.record_event(session_id, "Provisioning", "rejected", execution_result.get("reason", f"Provisioning {execution_result.get('status')}"))

            # 4. Generate audit
            final_audit_data = {
                "lookup": lookup,
                "context": session.state.get('policy_context'),
                "email": session.state.get('email_metadata'),
                "nlu_classification": nlu_result,
                "execution": execution_result
            }

            audit_narrative = self._generate_audit_narrative(session_id, final_audit_data)
            logging.info(audit_narrative)
            
            # 5. Update final state
            final_status = execution_result.get("status", "DONE")
            session.state['status'] = final_status
            session.state['final_audit'] = final_audit_data
            await self.session_service.update_session(session)
            
            logging.info(f"✅ [{session_id}] Background provisioning completed: {final_status}")
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)

            return {
                "status": final_status,
                "audit_summary": audit_narrative
            }

        except Exception as e:
            logging.error(f"❌ [{session_id}] Background provisioning failed: {e}", exc_info=True)
            trace.record_event(session_id, "Provisioning", "rejected", f"Execution error: {e}")
            trace.record_event(session_id, "Orchestrator", "info", "Completed the access request flow")
            trace.done(session_id)

            # Update session with error
            try:
                session = await self.session_service.get_session(
                    app_name=self.name, user_id="system", session_id=session_id
                )
                if session:
                    session.state['status'] = "EXECUTION_ERROR"
                    session.state['error'] = str(e)
                    await self.session_service.update_session(session)
            except Exception:
                pass  # Best effort
            
            return {
                "status": "EXECUTION_ERROR",
                "error": str(e),
                "error_type": type(e).__name__
            }
        
        

    async def _run_remote_provisioning(self, agent, args, parent_session_id, user_id):
        """
        Delegate IAM execution to the remote provisioner via A2A.

        Sends the args as a pure-JSON message; the provisioner's deterministic
        before_model_callback parses it, executes setIamPolicy, and returns a
        JSON IAMProvisioningResponse. (Pattern validated end-to-end in Phase 1.)
        """
        a2a_session_id = f"{parent_session_id}-a2a-execution"
        a2a_app_name = "IAM_A2A_Provisioner"

        await self.session_service.create_session(
            session_id=a2a_session_id,
            app_name=a2a_app_name,
            user_id=user_id,
            state={"parent_session": parent_session_id, "phase": "provisioning_execution"},
        )

        remote_runner = Runner(agent=agent, app_name=a2a_app_name, session_service=self.session_service)

        # Pure-JSON message body -- the provisioner's deterministic callback expects this exact shape.
        message = types.Content(role="user", parts=[types.Part(text=json.dumps(args))])

        execution_result = {}
        final_text = None
        try:
            async for event in remote_runner.run_async(
                user_id=user_id,
                session_id=a2a_session_id,
                new_message=message,
            ):
                content = getattr(event, "content", None)
                if content and getattr(content, "parts", None):
                    for part in content.parts:
                        if getattr(part, "text", None):
                            final_text = part.text

            if final_text:
                try:
                    execution_result = json.loads(final_text)
                except json.JSONDecodeError:
                    execution_result = {"status": "PARSE_ERROR", "raw": final_text}
            else:
                execution_result = {"status": "NO_RESPONSE", "error": "Remote agent returned no text"}

        except Exception as e:
            logging.error(f"A2A delegation error: {e}", exc_info=True)
            execution_result = {"status": "DELEGATION_ERROR", "error": str(e), "error_type": type(e).__name__}
        finally:
            try:
                await self.session_service.delete_session(a2a_session_id)
            except Exception as cleanup_error:
                logging.warning(f"A2A session cleanup failed: {cleanup_error}")

        return execution_result
            
    def _generate_audit_narrative(self, session_id: str, data: Dict[str, Any]) -> str:
        """Generates a human-readable summary for audit and XAI purposes."""

        # Safely access keys with defaults to prevent runtime errors during partial runs
        lookup = data.get('lookup') or {}
        context = data.get('context') or {}
        nlu = data.get('nlu_classification') or {}
        execution = data.get('execution') or {}
       # Extract Execution Details (handling the Pydantic structure from provisioning_agent)
        # ProvisioningAgent returns keys: 'status', 'timestamp', 'reason', 'applied_policy', 'audit_trail'
        audit_trail = execution.get('audit_trail', {}) or {}
        applied_policy = execution.get('applied_policy', {}) or {}
        
        jit_info = audit_trail.get('jit_token_id', 'N/A')
        
        # Construct string for applied policy
        if applied_policy:
            policy_binding = f"{applied_policy.get('role', 'N/A')} -> {applied_policy.get('member', 'N/A')} (Scope: {applied_policy.get('resource', 'N/A')})"
        else:
            policy_binding = "N/A"

        # Add a clear final outcome line
        final_status = execution.get('status', 'FAILED')
        if final_status == "POLICY_APPLIED":
            outcome_line = f"FINAL OUTCOME: SUCCESS. {execution.get('reason', 'Policy was applied.')}"
        else:
            outcome_line = f"FINAL OUTCOME: FAILURE. Reason: {execution.get('reason', 'An unknown error occurred.')}"

        # This function synthesizes the entire decision process for compliance officers.
        summary = [
            f"--- AUDIT TRACE: SESSION {session_id} ---",
            f"IAM REQUEST: Assigned role {lookup.get('role_id', 'N/A')} to {lookup.get('user_id', 'N/A')} in {lookup.get('gcp_project_scope', 'N/A')}.",
            f"COMPLIANCE CHECK: Constraint: {context.get('constraint_type', 'UNKNOWN')}.",
            f"JUSTIFICATION: {context.get('justification_summary', 'N/A')}",
            f"APPROVAL: Status: {nlu.get('status', 'UNKNOWN')}. Approver: {nlu.get('approver_id', 'UNKNOWN')}. Summary: {nlu.get('reason_summary', 'N/A')}",
            f"EXECUTION STATUS: {execution.get('status', 'FAILED')}.",
            f"JIT ACCESS: {jit_info}.",
            f"IAM POLICY APPLIED: {policy_binding}",
            f"{outcome_line}",
            f"--- END OF TRACE ---",
        ]
        return "\n".join(summary)