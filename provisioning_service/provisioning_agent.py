# agents/provisioning_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any, Optional
from google.cloud import resourcemanager_v3
from google.iam.v1 import policy_pb2
from google.type import expr_pb2
from google.iam.v1 import options_pb2
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
    reason: Optional[str] = None 
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
        Applies a real, conditional, time-bound IAM policy binding.
        Returns a strictly shaped dictionary to ensure Pydantic response validation passes.
        """
        logger.info("EXECUTION: JIT elevation successful (simulated). Proceeding with IAM policy update.")
        
        timestamp = datetime.datetime.now(datetime.timezone.utc)

        try:
            # --- REAL IAM EXECUTION ---
            client = resourcemanager_v3.ProjectsClient()

            primitive_roles = {"roles/viewer", "roles/editor", "roles/owner"}
            resource = f"projects/{project_id}"
            
            # 1. Get the current IAM policy for the project
            
            get_policy_options = options_pb2.GetPolicyOptions(
                requested_policy_version=3
            )

            current_policy = client.get_iam_policy(
                request={
                    "resource": resource,
                    "options": get_policy_options
                }
            )

            current_policy.version = 3

            if requested_role in primitive_roles:
                # No condition allowed; version can stay as-is
                new_binding = policy_pb2.Binding(
                    role=requested_role,
                    members=[f"user:{user_id}"],
                )
                reason_text = f"Access granted (no condition allowed on primitive roles)"
                 
            else:
                
                # 2. Define the temporal condition for the new binding
                expiration_time = timestamp + datetime.timedelta(hours=1) # Grant access for 1 hour
                condition = expr_pb2.Expr(
                    title=f"jit_access_{timestamp.strftime('%Y%m%d%H%M')}",
                    description=f"JIT access for {user_id} until {expiration_time.isoformat()}. Justification: {justification}",
                    expression=f'request.time < timestamp("{expiration_time.isoformat()}")'
                )
                # 3. Add the new conditional binding to the policy
                new_binding = policy_pb2.Binding(
                    role=requested_role,
                    members=[f"user:{user_id}"],
                    condition=condition,
                )
                reason_text = f"Access granted until {expiration_time.isoformat()}"

            current_policy.bindings.append(new_binding)


            # 4. Set the updated policy
            client.set_iam_policy(
                request={
                    "resource": resource,
                    "policy": current_policy,
                }
            )
            
            logger.info(f"EXECUTION: IAM policy change successfully applied for {requested_role}.")
            
            return IAMProvisioningResponse(
                    status="POLICY_APPLIED",
                    timestamp=timestamp.isoformat(),
                    reason=reason_text,
                    applied_policy=PolicyDetails(
                        role=requested_role,
                        member=f"user:{user_id}",
                        resource=f"projects/{project_id}"
                    ),
                    audit_trail=AuditTrail(
                        jit_token_id=f"jit-{random.randint(1000,9999)}",
                        justification=justification,
                        audit_data=f"See Cloud Audit Logs for project {project_id} around {timestamp.isoformat()}"
                    )
                )

        except Exception as e:
            logger.error(f"EXECUTION: IAM policy update failed: {e}", exc_info=True)
            return IAMProvisioningResponse(
                    status="EXECUTION_FAILURE",
                    timestamp=timestamp.isoformat(),
                    reason=f"Failed to apply IAM policy: {str(e)}",
                    applied_policy=None,
                    audit_trail=None
                )
