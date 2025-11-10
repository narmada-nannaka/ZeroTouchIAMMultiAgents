# agents/nlu_classifier_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from typing import Dict, Any, ClassVar
import logging
import json
import time

# Set up logging for visibility
logging.basicConfig(level=logging.INFO)

# Define the mandatory JSON schema for structured output
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "description": "The final classification of the approver's response. Must be one of the ENUM values.",
            "enum": ["APPROVED", "DENIED", "REJECTED_CONTEXT_MISSING"],
        },
        "approver_id": {
            "type": "string",
            "description": "The email address or ID of the approver who sent the response. This must be the value provided in the sender_email input.",
        },
        "reason_summary": {
            "type": "string",
            "description": "A concise, 1-2 sentence summary of the approver's reason or comments, extracted directly from the email body.",
        },
    },
    "required": ["status", "approver_id", "reason_summary"],
}

class NLUClassifierAgent(LlmAgent):
    """
    Classifies human email responses into structured JSON using Gemini 2.5 Flash
    via the Vertex AI SDK.
    """
    # CRITICAL: Use the official, stable model identifier for the Vertex AI API
    # Using us-central1 as the default for the model endpoint.
    DEFAULT_REGION: ClassVar[str] = "asia-southeast1"
    DEFAULT_MODEL: ClassVar[str] = "gemini-2.5-flash"

    def __init__(self, project_id: str, **kwargs):

        # Define the wrapper function.
        def classify_intent(email_body: str, sender_email: str) -> Dict[str, Any]:
            """Classifies the raw email text and sender identity to return a structured JSON decision."""
            return self._perform_classification(email_body, sender_email)

        # Define the tool.
        nlu_tool = FunctionTool(
            func=classify_intent
        )

        super().__init__(
            name="NLUClassifierAgent",
            description="Classifies human email responses (e.g., APPROVED/DENIED) using Gemini NLU.",
            tools=[nlu_tool],
            **kwargs
        )

        # Store configuration attributes and initialize the Vertex AI SDK
        self.__dict__['project_id'] = project_id
        # Initialize Vertex AI for the project and location
        vertexai.init(project=project_id, location=self.DEFAULT_REGION)

        # Define system instruction for the model
        system_instruction = (
            "You are an impartial, highly accurate Natural Language Understanding (NLU) service for an "
            "automated IAM provisioning system. Your sole task is to analyze the 'Email Body' provided by the user, "
            "classify the human approver's final intent, and return a single, valid JSON object that strictly conforms "
            "to the provided schema. Classify as 'REJECTED_CONTEXT_MISSING' if the intent is ambiguous or if the email "
            "contains no clear approval or denial language."
        )

        # Initialize the GenerativeModel with system instruction
        self.__dict__['model'] = GenerativeModel(
            self.DEFAULT_MODEL,
            system_instruction=system_instruction
        )


    def _perform_classification(self, email_body: str, sender_email: str) -> Dict[str, Any]:
        """Performs the classification using the Vertex AI SDK with structured JSON output."""
        max_retries = 3
        delay = 1 # Initial delay for exponential backoff

        user_query = f"Email Body to Classify: \"{email_body}\""

        # --- Structured Output Configuration ---
        generation_config = GenerationConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA
        )

        # --- SDK Call with Exponential Backoff ---
        for attempt in range(max_retries):
            try:
                # Use the GenerativeModel's generate_content method
                response = self.model.generate_content(
                    contents=[user_query],
                    generation_config=generation_config,
                )

                json_text = response.text
                if json_text:
                    parsed_json = json.loads(json_text)
                    # Add sender_email to the output for the Orchestrator to track
                    parsed_json['approver_id'] = sender_email
                    logging.info(f"NLU Classification: SUCCESS -> {parsed_json.get('status')} by {sender_email}")
                    return parsed_json

                logging.warning(f"Attempt {attempt+1}: NLU response was empty or malformed.")

            except Exception as e:
                # Catching general exceptions from the SDK (e.g., timeout, 5xx, or 404/403 related to model access)
                logging.warning(f"Attempt {attempt+1}: Vertex AI SDK Error, Retrying in {delay}s. Error: {e}")
                time.sleep(delay)
                delay *= 2 # Exponential backoff

        # Fallback if all retries fail
        logging.error("NLU classification failed after all retries. Defaulting to REJECTED_CONTEXT_MISSING.")
        return {"status": "REJECTED_CONTEXT_MISSING", "approver_id": sender_email, "reason_summary": "NLU classification failed due to system error."}
