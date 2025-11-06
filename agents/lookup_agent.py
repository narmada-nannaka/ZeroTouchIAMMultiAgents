# agents/lookup_agent.py - REVISED (Final Correction)

from google.cloud import firestore
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any

class ApproverLookupAgent(LlmAgent):
    """
    A specialized agent that retrieves required human approvers and compliance 
    policy details from Cloud Firestore.
    """
    def __init__(self, project_id: str, **kwargs):
        # Create a wrapper function that FunctionTool can use
        # The project_id is captured in the closure
        def lookup_approvers_policy(user_id: str, role_id: str, project_scope: str):
            """Looks up the list of required human approvers and compliance rules for a given role and project scope."""
            return self._perform_lookup(user_id, role_id, project_scope, project_id)

        # Define the tool - FunctionTool automatically extracts name from function __name__ and description from docstring
        lookup_tool = FunctionTool(func=lookup_approvers_policy)

        super().__init__(
            name="ApproverLookupAgent",
            description="Retrieves required approvers and policy context from Firestore.",
            tools=[lookup_tool],
            **kwargs
        )

    def _perform_lookup(self, user_id: str, role_id: str, project_scope: str, project_id: str) -> Dict[str, Any]:
        """Queries Firestore for the approval context based on the request context."""
        
        # Initialize the Firestore client here to avoid the Pydantic error in __init__
        firestore_db = firestore.Client(project=project_id, database="approver-store")
        
        # Query Firestore based on the requested role and project scope
        try:
            # We look for a document where both 'role_id' and 'gcp_project_scope' match the request
            docs = firestore_db.collection('role_approvals').where(filter=firestore.FieldFilter('role_id', '==', role_id)).where(filter=firestore.FieldFilter('gcp_project_scope', '==', project_scope)).limit(1).stream()
            
            # Fetch the first matching document
            for doc in docs:
                result = doc.to_dict()
                result['firestore_document_id'] = doc.id
                # Add the request parameters to the result for audit trail
                result['user_id'] = user_id
                result['role_id'] = role_id
                result['gcp_project_scope'] = project_scope
                return result

            # If no document is found
            return {"error": f"No approval map found for role {role_id} in scope {project_scope}"}
        
        except Exception as e:
            # Catch exceptions like connection errors
            return {"error": f"Database query failed: {str(e)}"}