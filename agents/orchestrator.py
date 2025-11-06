# agents/orchestrator.py (ADK Multi-Tool Pattern)

from google.adk.agents import LlmAgent
from agents.lookup_agent import ApproverLookupAgent
# NEW IMPORT: Policy Context Agent
from agents.context_agent import PolicyContextAgent
# NEW IMPORT: NLU Classifier Agent
from agents.nlu_classifier_agent import NLUClassifierAgent
# NEW IMPORT: Consuming the agent service through RemoteA2Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.tools import FunctionTool
from google.adk.sessions.base_session_service import BaseSessionService
import os
import logging
import json
from typing import Dict, Any, Callable

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
    def create(cls, project_id: str, provisioning_service_url: str, session_service: BaseSessionService):
        """
        Factory method to create IAMOrchestrator with external dependencies.
        This avoids Pydantic validation errors when passing non-field parameters.
        """

        RAG_CORPUS_ID = "IAM-Policy-Corpus"  # Confirmed Corpus ID

        # Build sub-agents
        lookup_agent = ApproverLookupAgent(project_id=project_id)
        context_agent = PolicyContextAgent(project_id=project_id, corpus_id=RAG_CORPUS_ID)
        nlu_agent = NLUClassifierAgent(project_id=project_id)

        # Remote A2A client
        # RemoteA2aAgent will fetch the agent_card from the provisioning service URL automatically
        # Pass the agent card URL directly - RemoteA2aAgent will fetch it internally
        agent_card_url = f"{provisioning_service_url}/.well-known/agent-card"

        # The to_a2a() function on the provisioning service exposes the agent_card at a standard endpoint
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
    
    async def start_provisioning(self, session_id: str, user_id: str, requested_role: str, project_scope: str) -> Dict[str, Any]:
        """
        Initial method called by the Pub/Sub trigger. Initiates the workflow.
        """
        
        session = await self.session_service.create_session(session_id=session_id, app_name=self.name, user_id=user_id)
        session.state['request'] = { "user_id": user_id, "role": requested_role, "scope": project_scope, "status": "LOOKUP_INITIATED" }
        logging.info(f"[{session_id}] Provisioning started. Delegating lookup...")
        
        # --- 2. Delegate Lookup (Week 1) ---
        lookup_agent = self.find_agent("ApproverLookupAgent")
        lookup_tool_func = _get_tool_func(lookup_agent, "lookup_approvers_policy")
        
        lookup_result = lookup_tool_func(user_id=user_id, role_id=requested_role, project_scope=project_scope)
        if "error" in lookup_result:
            return lookup_result 

        # --- 3. Delegate RAG Retrieval & Compliance Check (Task 1) ---
        context_agent = self.find_agent("PolicyContextAgent")
        doc_id = lookup_result.get("baseline_policy_doc_id", "NOT_FOUND")

        # Execute RAG Retrieval (Tool 1: _retrieve_policy_text)
        rag_tool_func = _get_tool_func(context_agent, "_retrieve_policy_text")
        policy_context = rag_tool_func(doc_id=doc_id)

        # Execute Compliance Check (Tool 2: _check_temporal_compliance)
        check_tool_func = _get_tool_func(context_agent, "_check_temporal_compliance")
        is_compliant, compliance_reason = check_tool_func(constraint_type=policy_context.get("constraint_type"))

        # --- 4. Enforce Compliance Guardrail (Improvement 2) ---
        if not is_compliant:
            logging.error(f"GUARDRAIL FAIL: Request rejected: {compliance_reason}")
            session.state['status'] = "POLICY_VIOLATION_REJECTED"
            return {"status": "REJECTED", "reason": compliance_reason}
        
        # --- 5. Simulate Communication Loop Entry ---
        
        # For local testing, we skip email send/wait and simulate the inbound response trigger
        
        # SIMULATION INPUT: Approver sends a message back after receiving the justification email
        simulated_approver_email = lookup_result.get("required_approvers") # Use the first approver
        simulated_response_text = "Yes, I approve this elevation request immediately."
        
        logging.info(f"[{session_id}] Simulating inbound email from {simulated_approver_email}")

        # --- 6. Delegate to NLU Classifier Agent ---
        nlu_agent = self.find_agent("NLUClassifierAgent")
        nlu_tool_func = _get_tool_func(nlu_agent, "classify_intent")

        nlu_result = nlu_tool_func(
            email_body=simulated_response_text,
            sender_email=simulated_approver_email
        )

        # --- 7. Delegate to Secure Execution Agent (Task 1) ---

        if nlu_result.get("status") == "APPROVED":
            
            # --- EXECUTION DELEGATION (A2A Agent Pattern) ---
            
            # Find the remote provisioning agent
            provisioning_agent = self.find_agent("iam_provisioner_client")
            
            if not provisioning_agent:
                 raise ValueError("IAMProvisioningAgent not found.")
            
            logging.info("DELEGATION: NLU Approved. Calling isolated Provisioning Agent via A2A pattern.")

            # Create a task message for the remote agent
            # The remote agent's LLM will understand this request and call the appropriate tool
            task_prompt = (
                f"Execute IAM provisioning with these parameters:\n"
                f"- requested_role: {requested_role}\n"
                f"- user_id: {user_id}\n"
                f"- justification: {policy_context.get('justification_summary', 'No justification provided')}\n"
                f"\nPlease call the appropriate tool to execute the IAM role assignment."
            )
            
            # Delegate to the remote agent by calling it through ADK's agent delegation mechanism
            # The orchestrator's LLM will transfer control to the remote agent
            # This happens automatically through ADK's agent orchestration
            # We must iterate it to get the final result dictionary.
            execution_result = {}
            try:
                async for event in provisioning_agent.run_async(task_prompt):
                    if hasattr(event, 'content') and event.content:
                        # Try to extract the tool result from the event
                        if hasattr(event.content, 'text') and event.content.text:
                            try:
                                # The tool output should be in the text as JSON
                                execution_result = json.loads(event.content.text)
                            except json.JSONDecodeError:
                                # If not JSON, try to extract from the raw text
                                execution_result = {"status": "PARSE_ERROR", "raw_output": event.content.text}
                        elif hasattr(event.content, 'to_dict'):
                            execution_result = event.content.to_dict()
                        
                        # Check if this event contains tool results
                        if hasattr(event, 'tool_results') and event.tool_results:
                            # Extract the actual tool return value
                            for tool_result in event.tool_results:
                                if hasattr(tool_result, 'output'):
                                    execution_result = tool_result.output
                                    break
                                    
            except Exception as e:
                logging.error(f"Error during A2A delegation: {e}")
                execution_result = {"status": "DELEGATION_ERROR", "error": str(e)}

            
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
        """Generates a human-readable summary for audit and XAI purposes. [8]"""

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