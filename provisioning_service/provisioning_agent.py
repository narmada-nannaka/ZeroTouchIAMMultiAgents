# agents/provisioning_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any
import logging
import random
import datetime

logging.basicConfig(level=logging.INFO)

class IAMProvisioningAgent(LlmAgent):
    """
    Isolated, highly privileged agent responsible only for executing the final IAM policy set.
    This runs on its own secure Cloud Run service (A2A Server).
    """

    def __init__(self, project_id: str, **kwargs):
        # Define the privileged tool using a closure that captures project_id
        # The closure captures project_id directly from the parameter
        def execute_iam_set_tool(requested_role: str, user_id: str, justification: str, **extra_kwargs) -> Dict[str, Any]:
            """Simulates the JIT elevation and the immutable policy application."""
            # Ignore extra parameters like 'configuration', 'context', etc.
            if extra_kwargs:
                logging.info(f"Received extra parameters (ignoring): {list(extra_kwargs.keys())}")
            
            return self._perform_iam_set(requested_role, user_id, justification, project_id)

        iam_tool = FunctionTool(
            func=execute_iam_set_tool
        )

        super().__init__(
            name="IAMProvisioningAgent",
            description="Executes policy changes using ephemeral Just-in-Time credentials.",
            tools=[iam_tool],
            **kwargs
        )

        # Store project_id after initialization to avoid Pydantic validation issues
        self.__dict__['project_id'] = project_id

    # The most sensitive tool: Executes policy change
    def _perform_iam_set(self, requested_role: str, user_id: str, justification: str, project_id: str) -> Dict[str, Any]:
        """
        Simulates the JIT elevation and the immutable policy application.
        """
        logging.info("EXECUTION: Initiating JIT access request...")
        
        # --- JIT SIMULATION (Improvement 1 / Security) ---
        # The service account SA_Provisioner requests temporary elevation from PAM [1]
        
        if random.random() < 0.05: # Simulate a small chance of PAM failure
            return {"status": "EXECUTION_FAILURE", "reason": "PAM rejected temporary elevation request."}

        jip_token_status = "JIT_ELEVATION_GRANTED"
        
        # --- EXECUTION SIMULATION (Least Privilege) ---
        # The agent now holds the short-lived Custom IAM Role (iam.policy.set) [5]
        logging.info(f"EXECUTION: {jip_token_status} for 5 minutes.")
        
        policy_applied = {
            "resource": f"projects/{project_id}",
            "binding": f"{requested_role} granted to {user_id}",
            "execution_time": datetime.datetime.now().isoformat(),
            "justification": justification
        }
        
        logging.info(f"EXECUTION: Policy change successful. Token revoked.")
        
        return {
            "status": "POLICY_APPLIED_SUCCESS", 
            "jip_status": jip_token_status,
            "policy_details": policy_applied
        }