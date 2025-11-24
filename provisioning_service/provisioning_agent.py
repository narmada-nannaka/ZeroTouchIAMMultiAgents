# agents/provisioning_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any, Optional
import logging
import random
import datetime
from pydantic import BaseModel, Field

# Set logging to match the level used in app.py for consistency
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# NEW: DEFINE EXPLICIT A2A RESPONSE SCHEMA (Pydantic BaseModel)
# This model guarantees the structure the Orchestrator expects.
# ============================================================================

class PolicyDetails(BaseModel):
    """Details of the applied IAM policy."""
    role: str
    member: str
    resource: str

class AuditTrail(BaseModel):
    """Audit-related data."""
    jit_token_id: str = Field(..., description="The ID for the Just-in-Time elevation token.")
    justification: str
    audit_data: str = Field(..., description="Link or reference to the Cloud Audit Log entry.")

class IAMProvisioningResponse(BaseModel):
    """The unified schema for the Provisioning Agent's response."""
    status: str = Field(..., description="POLICY_APPLIED, EXECUTION_FAILURE, or JIT_FAILURE.")
    timestamp: str
    reason: Optional[str] = None # <-- This now correctly references typing.Optional
    applied_policy: Optional[PolicyDetails] = None
    audit_trail: Optional[AuditTrail] = None

class IAMProvisioningAgent(LlmAgent):
    """
    Isolated, highly privileged agent responsible only for executing the final IAM policy set.
    This runs on its own secure Cloud Run service (A2A Server).
    """

    def __init__(self, **kwargs):
        # Define the privileged tool using a closure that captures project_id
        # The closure captures project_id directly from the parameter
        def execute_iam_set_tool(requested_role: str, user_id: str, justification: str, gcp_project_scope: str = None, **extra_kwargs) -> IAMProvisioningResponse:
            """Simulates the JIT elevation and the immutable policy application."""
            # Ignore extra parameters like 'configuration', 'context', etc.
            if extra_kwargs:
                logging.info(f"Received extra parameters (ignoring): {list(extra_kwargs.keys())}")

            safe_justification = justification or "No justification provided"

            # Validate that gcp_project_scope is provided
            if not gcp_project_scope:
                logger.error("Missing required parameter: gcp_project_scope")
                return IAMProvisioningResponse(
                    status="PARAMETER_ERROR",
                    timestamp=datetime.datetime.now().isoformat(),
                    reason="Missing required parameter: gcp_project_scope",
                    applied_policy=None,
                    audit_trail=None
                )

            return self._perform_iam_set(requested_role, user_id, safe_justification, gcp_project_scope)

        iam_tool = FunctionTool(
            func=execute_iam_set_tool
        )

        super().__init__(
            name="IAMProvisioningAgent",
            description="Executes policy changes using ephemeral Just-in-Time credentials.",
            tools=[iam_tool],
            **kwargs
        )

    # The most sensitive tool: Executes policy change
    def _perform_iam_set(self, requested_role: str, user_id: str, justification: str, project_id: str) -> Dict[str, Any]:
        """
        Simulates the JIT elevation and the immutable policy application.
        Returns a strictly shaped dictionary to ensure Pydantic response validation passes.
        """
        logger.info(f"EXECUTION: Initiating JIT access request for {user_id} in {project_id}...")
        
        timestamp = datetime.datetime.now().isoformat()

        # --- JIT SIMULATION ---
        if random.random() < 0.05: 
            logger.error("EXECUTION: PAM JIT request failed.")
            # ARCHITECTURAL FIX: Unified response schema for failure path.
            # Returning the same keys (even as None) prevents Pydantic validation errors 
            # if it inferred a strict schema from the success path.
            return IAMProvisioningResponse(
                    status="EXECUTION_FAILURE",
                    timestamp=timestamp,
                    reason="PAM rejected temporary elevation request.",
                    applied_policy=None,
                    audit_trail=None
                )
        
        # --- EXECUTION SIMULATION ---
        logger.info(f"EXECUTION: Policy change successfully applied for {requested_role}.")
        
        # ARCHITECTURAL FIX: Unified response schema for success path.
        return IAMProvisioningResponse(
                status="POLICY_APPLIED",
                timestamp=timestamp,
                reason=None,
                applied_policy=PolicyDetails(
                    role=requested_role,
                    member=f"user:{user_id}",
                    resource=f"projects/{project_id}" # Use the provided scope
                ),
                audit_trail=AuditTrail(
                    jit_token_id=f"jit-{random.randint(1000,9999)}",
                    justification=justification,
                    audit_data=f"GCP Cloud Audit log correlation link for {timestamp}"
                )
            )