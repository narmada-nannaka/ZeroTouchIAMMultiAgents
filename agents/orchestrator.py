# agents/orchestrator.py (ADK Multi-Tool Pattern)

from google.adk.agents import LlmAgent
from agents.lookup_agent import ApproverLookupAgent
from agents.context_agent import PolicyContextAgent
from agents.nlu_classifier_agent import NLUClassifierAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.tools import FunctionTool
from google.adk.sessions.base_session_service import BaseSessionService
from google.genai import types 
import os
import logging
import json
from typing import Dict, Any, Callable
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
    def create(cls, project_id: str, provisioning_service_url: str, gcp_location: str, rag_engine_id: str, rag_data_store_id: str, session_service: BaseSessionService):
        """
        Factory method to create IAMOrchestrator with external dependencies.
        This avoids Pydantic validation errors when passing non-field parameters.
        """
        # Build sub-agents
        lookup_agent = ApproverLookupAgent(project_id=project_id)
        context_agent = PolicyContextAgent(project_id=project_id, location=gcp_location, engine_id=rag_engine_id, data_store_id=rag_data_store_id)
        nlu_agent = NLUClassifierAgent(project_id=project_id)

        # Remote A2A client
        # RemoteA2aAgent will fetch the agent_card from the provisioning service URL automatically
        agent_card_url = f"{provisioning_service_url}/.well-known/agent.json"

        logging.info(f"Initializing RemoteA2aAgent with agent card URL: {agent_card_url}")

        
        remote_provisioning_agent = RemoteA2aAgent(
            name="iam_provisioner_client",
            description="Remote agent that handles secure IAM provisioning operations",
            agent_card=agent_card_url,  # Pass URL as string - RemoteA2aAgent will fetch agent_card
        )

       # 4. Instantiate the Agent (Pydantic Validation happens here)
        instance = cls(
            name="IAMOrchestrator",
            description="Manages the entire asynchronous IAM provisioning session.",
            sub_agents=[lookup_agent, context_agent, nlu_agent, remote_provisioning_agent],
            tools=[],  # No local tools needed - remote agent provides the execution capability
        )

        # 5. Attach session service after instantiation by *bypassing* Pydantic's setter
        # FIX: Use __dict__ to assign directly to the instance object.
        instance.__dict__['session_service'] = session_service # <-- THIS IS THE CRITICAL CHANGE

        return instance
    
    async def start_provisioning(self, session_id: str, user_id: str, requested_role: str, project_scope: str, user_timezone: str = 'UTC') -> Dict[str, Any]:
        """
        Initial method called by the Pub/Sub trigger. Initiates the workflow.
        """
        
        session = await self.session_service.create_session(session_id=session_id, app_name=self.name, user_id=user_id)
        session.state['request'] = { "user_id": user_id, "role": requested_role, "scope": project_scope, "user_timezone": user_timezone, "status": "LOOKUP_INITIATED" }
        logging.info(f"[{session_id}] Provisioning started. Delegating lookup...")
        
        # --- 2. Delegate Lookup  ---
        lookup_agent = self.find_agent("ApproverLookupAgent")
        lookup_tool_func = _get_tool_func(lookup_agent, "lookup_approvers_policy")
        
        lookup_result = lookup_tool_func(user_id=user_id, role_id=requested_role, project_scope=project_scope)
        if "error" in lookup_result:
            return lookup_result 

        # --- 3. Delegate RAG Retrieval & Compliance Check ---
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

        # --- 4. Enforce Compliance Guardrail ---
        if not is_compliant:
            logging.error(f"GUARDRAIL FAIL: Request rejected: {compliance_reason}")
            session.state['status'] = "POLICY_VIOLATION_REJECTED"
            return {"status": "REJECTED", "reason": compliance_reason}
        
        # --- 5. Simulate Communication Loop Entry ---
        
        # For local testing, we skip email send/wait and simulate the inbound response trigger
        
        # SIMULATION INPUT: Approver sends a message back after receiving the justification email
        simulated_approver_email = lookup_result.get("required_approvers") # Use the first approver
        #simulated_response_text = "I'm travelling this week, so I'll review this next Monday."
        simulated_response_text = "Yes, please approve this request."
        
        logging.info(f"[{session_id}] Simulating inbound email from {simulated_approver_email}")

        # --- 6. Delegate to NLU Classifier Agent ---
        nlu_agent = self.find_agent("NLUClassifierAgent")
        nlu_tool_func = _get_tool_func(nlu_agent, "classify_intent")

        nlu_result = nlu_tool_func(
            email_body=simulated_response_text,
            sender_email=simulated_approver_email
        )

        # --- 7. Delegate to Secure Execution Agent ---

        if nlu_result.get("status") == "APPROVED":
            
            # --- EXECUTION DELEGATION (A2A Agent Pattern) ---
            
            # Find the remote provisioning agent
            provisioning_agent = self.find_agent("iam_provisioner_client")
            
            if not provisioning_agent:
                 raise ValueError("IAMProvisioningAgent not found.")
            
            logging.info("DELEGATION: NLU Approved. Calling isolated Provisioning Agent via A2A pattern.")

            a2a_session_id = f"{session_id}_a2a_execution"
            a2a_app_name = "IAM_A2A_Provisioner"
            await self.session_service.create_session(
                session_id=a2a_session_id,
                app_name=a2a_app_name,
                user_id=user_id,
                state={"parent_session": session_id, "phase": "provisioning_execution"}
            )
            logging.info(f"Created A2A session: {a2a_session_id} for remote provisioning agent")

            # Create the Runner for the remote provisioning agent
            remote_runner = Runner(
                agent=provisioning_agent,
                app_name=a2a_app_name,
                session_service=self.session_service
            )

            # build the argument bag
            logging.info(f"[DEBUG] Building args - project_scope value: {project_scope}")
            logging.info(f"[DEBUG] policy_context: {policy_context}")
            args = {
                "requested_role": requested_role,
                "user_id": user_id,
                "justification": policy_context.get("justification_summary", "No justification provided"),
                "gcp_project_scope": project_scope
            }
            logging.info(f"[DEBUG] Final args dict: {args}")

            # A2A-compliant envelope
            tool_call_envelope = {
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "execute_iam_set_tool",
                            "arguments": json.dumps(args)  # arguments must be a JSON string
                        }
                    }
                ]
            }
            logging.info(f"[DEBUG] tool_call_envelope: {json.dumps(tool_call_envelope, indent=2)}")
            
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
                        for tool_result in event.tool_results:
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
                # Clean up session even on error
                try:
                    await self.session_service.delete_session(a2a_session_id)
                except:
                    pass

            
            # --- 7. Final Audit and Response (Task 2) ---
            
            # Combine all results for the final audit trail
            final_audit_data = {
                "lookup": lookup_result,
                "context": policy_context,
                "nlu": nlu_result,
                "execution": execution_result
            }

            # Generate Audit Narrative (Task 2.2)
            audit_narrative = self._generate_audit_narrative(session_id, final_audit_data)
            logging.info(audit_narrative) # Simulates logging to BigQuery Sink [7]
            
            session.state['status'] = execution_result.get("status", "EXECUTION_UNKNOWN")
            session.state['final_audit'] = final_audit_data

            return {"status": execution_result.get("status"), "audit_summary": audit_narrative}

        else:
            session.state['status'] = "NLU_REJECTED"
            return {"status": "REJECTED", "reason": f"NLU classified response as {nlu_result.get('status')}"}
        
    def _generate_audit_narrative(self, session_id: str, data: Dict[str, Any]) -> str:
        """Generates a human-readable summary for audit and XAI purposes."""

        # This function synthesizes the entire decision process for compliance officers.
        summary = [
            f"--- AUDIT TRACE: SESSION {session_id} ---",
            f"IAM REQUEST: Assigned role {data['lookup']['role_id']} to {data['lookup']['user_id']} in {data['lookup']['gcp_project_scope']}.",
            f"COMPLIANCE CHECK: Status: {'PASSED' if data['context'].get('compliance_check_passed') else 'FAILED'}. Constraint: {data['context'].get('policy_constraint_type')}.",
            f"JUSTIFICATION: {data['context'].get('justification_summary')}",
            f"APPROVAL: Status: {data['nlu'].get('status')}. Approver: {data['nlu'].get('approver_id')}.",
            f"EXECUTION STATUS: {data['execution'].get('status', 'FAILED')}.",
            f"JIT ACCESS: {data['execution'].get('jip_status', 'N/A')}.",
            f"IAM POLICY APPLIED: {data['execution'].get('policy_details', {}).get('binding', 'N/A')}",
            f"--- END OF TRACE ---"
        ]
        return "\n".join(summary)