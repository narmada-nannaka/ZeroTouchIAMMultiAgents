# agents/nlu_classifier_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.cloud import aiplatform
from typing import Dict, Any, List
import logging
import json

logging.basicConfig(level=logging.INFO)

# Define the mandatory JSON schema for structured output (Improvement 3)
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "description": "The final classification of the approver's response.",
            "enum": ["APPROVED", "DENIED", "REJECTED_CONTEXT_MISSING"],
        },
        "approver_id": {
            "type": "string",
            "description": "The email address or ID of the approver who sent the response.",
        },
        "reason_summary": {
            "type": "string",
            "description": "A concise, 1-2 sentence summary of the approver's reason or comments.",
        },
    },
    "required": ["status", "approver_id", "reason_summary"],
}

class NLUClassifierAgent(LlmAgent):
    """
    Classifies human email responses into structured JSON using Gemini 2.5 Flash.
    """
    def __init__(self, project_id: str, **kwargs):
        
        # CRITICAL FIX 1: Set model for efficiency (Improvement 3) and Structured Output.
        model = "gemini-2.5-flash"

        # This agent needs a tool to process the raw email text
        classify_tool = FunctionTool(func=self.classify_intent)

        super().__init__(
            name="NLUClassifierAgent",
            description="Processes unstructured human communication to extract deterministic, structured approval decisions.",
            model=model,
            tools=[classify_tool],
            **kwargs
        )
        self._project_id = project_id

    def classify_intent(self, email_body: str, sender_email: str) -> Dict[str, Any]:
        """Analyzes raw email text and sender identity to return a structured JSON decision."""
        try:
            # For demo purposes, use simple keyword matching instead of calling the Gemini API
            # In production, you would call Gemini with structured output here

            raw_email_lower = email_body.lower()

            # Simple classification logic
            if any(word in raw_email_lower for word in ["yes", "approve", "approved", "grant", "ok", "accept"]):
                status = "APPROVED"
                reason = "Approver explicitly granted permission."
            elif any(word in raw_email_lower for word in ["no", "deny", "denied", "reject", "rejected"]):
                status = "DENIED"
                reason = "Approver explicitly rejected the request."
            else:
                status = "REJECTED_CONTEXT_MISSING"
                reason = "Intent unclear or ambiguous."

            json_response = {
                "status": status,
                "approver_id": sender_email,
                "reason_summary": reason
            }

            logging.info(f"NLU Classification: {json_response.get('status')}")
            return json_response

        except Exception as e:
            logging.error(f"NLU classification failed: {e}")
            # If the model fails or returns invalid JSON, we classify as UNKNOWN for safety
            return {"status": "REJECTED_CONTEXT_MISSING", "error_reason": f"NLU failure: {str(e)}"}