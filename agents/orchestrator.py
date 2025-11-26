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
        # RemoteA2aAgent will fetch the agent_card from the provisioning service URL automatically
        agent_card_url = f"{base_url}/.well-known/agent.json"
        logging.info(f"Initializing RemoteA2aAgent with agent card URL: {agent_card_url}")

        
        remote_provisioning_agent = RemoteA2aAgent(
            name="iam_provisioner_client",
            description="Remote agent that handles secure IAM provisioning operations",
            agent_card=agent_card_url,  # Pass URL as string - RemoteA2aAgent will fetch agent_card
            httpx_client=authenticated_client,
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
        
        session = await self.session_service.create_session(session_id=session_id, app_name=self.name, user_id=user_id)
        session.state['request'] = { "user_id": user_id, "role": requested_role, "scope": project_scope, "user_timezone": user_timezone, "status": "LOOKUP_INITIATED" }
        logging.info(f"[{session_id}] Phase 1: Lookup & Context...")
        
        # --- 1. Delegate Lookup  ---
        lookup_agent = self.find_agent("ApproverLookupAgent")
        lookup_tool_func = _get_tool_func(lookup_agent, "lookup_approvers_policy")
        
        lookup_result = lookup_tool_func(user_id=user_id, role_id=requested_role, project_scope=project_scope)
        if "error" in lookup_result:
            session.state['status'] = "LOOKUP_FAILED"
            return lookup_result 
        
        session.state['lookup_result'] = lookup_result

        # --- 2. Delegate RAG Retrieval & Compliance Check ---
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
            return {"status": "REJECTED", "reason": compliance_reason}
        
        # --- 3. Delegate Communication (SEND EMAIL) ---
        
        required_approvers = lookup_result.get("required_approvers", [])
        if not required_approvers:
            return {"status": "ERROR", "reason": "No approvers found configuration."}

        justification_text = policy_context.get("justification_summary", "No justification.")
        
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

            return {
                "status": "WAITING_FOR_APPROVAL", 
                "message": f"Email sent to {required_approvers}. Workflow paused.",
                "session_id": session_id
            }
                
        except Exception as e:
            logging.error(f"Email failed: {e}")
            return {"status": "EMAIL_FAILED", "error": str(e)}
        
     # --- PHASE 2: EXECUTE ON APPROVAL ---
    async def resume_with_approval(self, session_id: str, raw_response_text: str, approver_email: str) -> Dict[str, Any]:
        """
        Called by the Webhook when a human clicks 'Approve' or 'Deny'.
        """
        logging.info(f"[{session_id}] Resuming session. Decision: {raw_response_text}")
        
        # 1. Rehydrate State
        # Since we store session_id in Firestore, we pull the session object back.
        session = await self.session_service.get_session(
            app_name=self.name, 
            user_id="system", # System is resuming it
            session_id=session_id
        )

        if not session:
            return {"status": "ERROR", "reason": "Session not found or expired."}
        
        if session.state.get('status') != "WAITING_FOR_APPROVAL":
             logging.warning(f"Session {session_id} is in state {session.state.get('status')}, not WAITING.")
             # Determine if we should proceed or not. For safety, we might allow re-approvals in a prototype.

        # 2. CALL NLU CLASSIFIER AGENT
        # We delegate the "understanding" of the human input to the NLU Agent.
        nlu_agent = self.find_agent("NLUClassifierAgent")
        if not nlu_agent:
             raise ValueError("NLUClassifierAgent not found in orchestrator.")

        nlu_tool = _get_tool_func(nlu_agent, "classify_intent")
        
        logging.info(f"[{session_id}] Delegating to NLU Classifier...")
        nlu_result = nlu_tool(
            email_body=raw_response_text,
            sender_email=approver_email
        )

        # Extract the structured decision from NLU
        decision_status = nlu_result.get("status", "REJECTED_CONTEXT_MISSING")
        decision_reason = nlu_result.get("reason_summary", "No reason provided.")
        
        logging.info(f"[{session_id}] NLU Result: {decision_status} | Reason: {decision_reason}")

        # 3. Process Decision based on NLU Output
        if decision_status != "APPROVED":
            session.state['status'] = f"REJECTED_NLU_{decision_status}"
            await self.session_service.update_session(session)
            return {
                "status": "REJECTED", 
                "reason": f"NLU classified response as {decision_status}. Reason: {decision_reason}"
            }
        
        # 4. Execution (A2A)
        req = session.state.get('request', {})
        lookup = session.state.get('lookup_result', {})
        provisioning_agent = self.find_agent("iam_provisioner_client")
        logging.info("DELEGATION: NLU Approved. Calling Provisioning Agent...")

        args = {
            "requested_role": req.get("role"),
            "user_id": req.get("user_id"),
            "justification": f"Approved by {approver_email}. NLU Summary: {decision_reason}",
            "gcp_project_scope": req.get("scope")
        }
        
        # Call the Robust Helper
        execution_result = await self._run_remote_provisioning(provisioning_agent, args, session_id, req.get("user_id"))
        
        # 5. Audit
        final_audit_data = {
            "lookup": lookup,
            "context": session.state.get('policy_context'),
            "email": session.state.get('email_metadata'),
            "nlu_classification": nlu_result,
            "execution": execution_result
        }
        
        audit_narrative = self._generate_audit_narrative(session_id, final_audit_data)
        logging.info(audit_narrative)
        
        session.state['status'] = execution_result.get("status", "DONE")
        session.state['final_audit'] = final_audit_data # Persist the full audit trail
        await self.session_service.update_session(session)

        return {"status": execution_result.get("status"), "audit_summary": audit_narrative}

    # --- FULL ROBUST A2A LOGIC RESTORED ---
    async def _run_remote_provisioning(self, agent, args, parent_session_id, user_id):
        """
        Encapsulates the A2A execution logic with full SDK compatibility and fallback checks.
        """
        a2a_session_id = f"{parent_session_id}_a2a_execution"
        a2a_app_name = "IAM_A2A_Provisioner"
        
        # Create temp session for the remote call
        await self.session_service.create_session(
            session_id=a2a_session_id,
            app_name=a2a_app_name,
            user_id=user_id,
            state={"parent_session": parent_session_id, "phase": "provisioning_execution"}
        )

        logging.info(f"Created A2A session: {a2a_session_id} for remote provisioning agent")

        # Create the Runner for the remote provisioning agent
        remote_runner = Runner(agent=agent, app_name=a2a_app_name, session_service=self.session_service)

        # A2A-compliant envelope
        tool_call_envelope = {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_iam_set_tool",
                        "arguments": json.dumps(args)
                    }
                }
            ]
        }
        # Standardize on application/json part for A2A
        # We send it as a simple text message containing the JSON.
        # Prefer an application/json part first, then a human-readable text part
        try:
            # If your google.genai types supports inline_data Blob (newer SDKs)
            from google.genai import types as genai_types
            json_part = types.Part(
                inline_data=genai_types.Blob(
                    mime_type="application/json",
                    data=json.dumps(tool_call_envelope).encode("utf-8"),
                )
            )
        except Exception:
            # Fallback for older SDKs that accept mime_type + data directly
            json_part = types.Part(
                mime_type="application/json",
                data=tool_call_envelope  # SDK will JSON-serialize
            )

        text_part = types.Part(text=(
            "Execute IAM provisioning using execute_iam_set_tool; JSON args are included above."
        ))

        task_message = types.Content(role="user", parts=[json_part, text_part])

        logging.info("A2A task_message parts: %s",
             [getattr(p, "mime_type", "text") for p in task_message.parts])
        
        # Delegate to the remote agent by calling it through ADK's agent delegation mechanism
        # The orchestrator's LLM will transfer control to the remote agent
        # This happens automatically through ADK's agent orchestration
        # We must iterate it to get the final result dictionary.
        execution_result = {}
        result_found = False
        try:
            logging.info(f"Starting A2A runner for session: {a2a_session_id}")

            async for event in remote_runner.run_async(
                user_id=user_id,
                session_id=a2a_session_id,
                new_message=task_message
            ):
                logging.info(f"Received event type: {type(event).__name__}")

                # check if this is the final response
                if hasattr(event, 'tool_results') and event.tool_results:
                    logging.info(f"✓ Found {len(event.tool_results)} tool_results in event")
                    for idx, tool_result in enumerate(event.tool_results):
                        logging.info(f"Processing tool_result #{idx}")
                        if hasattr(tool_result, 'output'):
                            output = tool_result.output
                            logging.info(f"Tool result output type: {type(output)}")
                            # Convert Pydantic models to dict
                            if hasattr(output, 'model_dump'):
                                execution_result = output.model_dump()
                            elif hasattr(output, 'dict'):
                                execution_result = output.dict()
                            elif isinstance(output, dict):
                                execution_result = output
                            elif isinstance(output, str):
                                # Try to parse as JSON
                                try:
                                    execution_result = json.loads(output)
                                except json.JSONDecodeError:
                                    execution_result = {"raw_output": output}
                            else:
                                execution_result = {"raw_output": str(output)}

                            logging.info(f"✓ Extracted execution_result from tool_results: {execution_result}")
                            result_found = True
                            break
                    
                # Check for final response content
                if not result_found and hasattr(event, 'is_final_response') and event.is_final_response():
                    logging.info("Checking final_response event")
                    if hasattr(event, 'content') and event.content:
                        if hasattr(event.content, 'parts') and event.content.parts:
                            logging.info(f"Final response has {len(event.content.parts)} parts")
                            for idx, part in enumerate(event.content.parts):
                                logging.info(f"Processing part #{idx}, type: {type(part)}")
                                
                                # Check for text content
                                if hasattr(part, 'text') and part.text:
                                    text_content = part.text
                                    logging.info(f"Found text content (first 200 chars): {text_content[:200]}")
                                    
                                    # Try to parse as JSON
                                    try:
                                        parsed = json.loads(text_content)
                                        if isinstance(parsed, dict):
                                            execution_result = parsed
                                            logging.info(f"✓ Parsed JSON from text part: {execution_result}")
                                            result_found = True
                                            break
                                    except json.JSONDecodeError as e:
                                        logging.warning(f"Could not parse text as JSON: {e}")
                                        # Store as raw text if it looks like a response
                                        if "status" in text_content.lower():
                                            execution_result = {"raw_text": text_content}
                                            result_found = True
                
                # Break out of the loop if we found a result
                if result_found:
                    logging.info("✓ Result found, breaking out of event loop")
                    break
                
            # If we didn't get any result, set a default error
            if not execution_result:
                logging.error("❌ No execution result received from remote agent")
                execution_result = {
                    "status": "NO_RESPONSE",
                    "error": "Remote agent did not return any result"
                }
            else:
                logging.info(f"✓ Final execution_result status: {execution_result.get('status', 'UNKNOWN')}")
                # Clean up the temporary A2A session
                try:
                    await self.session_service.delete_session(a2a_session_id)
                    logging.info(f"✓ Cleaned up A2A session: {a2a_session_id}")
                except Exception as cleanup_error:
                    logging.warning(f"Could not clean up A2A session: {cleanup_error}")
                                
        except Exception as e:
            logging.error(f"Error during A2A delegation: {e}", exc_info=True)
            execution_result = {"status": "DELEGATION_ERROR", "error": str(e), "error_type": type(e).__name__}
        finally:
            # --- CLEANUP BLOCK ---
            try:
                await self.session_service.delete_session(a2a_session_id)
                logging.info(f"✓ Cleaned up A2A session: {a2a_session_id}")
            except Exception as cleanup_error:
                logging.warning(f"Cleanup failed: {cleanup_error}")
            
        return execution_result
            
    def _generate_audit_narrative(self, session_id: str, data: Dict[str, Any]) -> str:
        """Generates a human-readable summary for audit and XAI purposes."""

        # Safely access keys with defaults to prevent runtime errors during partial runs
        lookup = data.get('lookup', {})
        context = data.get('context', {})
        nlu = data.get('nlu_classification', {})
        execution = data.get('execution', {})
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