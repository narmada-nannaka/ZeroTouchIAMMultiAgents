# agents/nlu_classifier_agent.py

import os
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google import genai
from google.genai import types
from typing import Dict, Any, ClassVar
import logging
import json
import time

# Set up logging for visibility
logging.basicConfig(level=logging.INFO)


class NLUClassifierAgent(LlmAgent):
    """
    Classifies human approval responses into structured JSON using Gemini via
    the google-genai SDK (the supported successor to vertexai.generative_models).
    """
    DEFAULT_REGION: ClassVar[str] = os.environ.get("NLU_REGION", "us-central1")
    DEFAULT_MODEL: ClassVar[str] = os.environ.get("NLU_MODEL", "gemini-2.5-flash")

    def __init__(self, project_id: str, **kwargs):

        def classify_intent(email_body: str, sender_email: str) -> Dict[str, Any]:
            """Classifies the raw email text and sender identity to return a structured JSON decision."""
            return self._perform_classification(email_body, sender_email)

        nlu_tool = FunctionTool(func=classify_intent)

        super().__init__(
            name="NLUClassifierAgent",
            description="Classifies human email responses (e.g., APPROVED/DENIED) using Gemini NLU.",
            tools=[nlu_tool],
            **kwargs
        )

        system_instruction = (
            "You are an impartial, highly accurate Natural Language Understanding (NLU) service for an "
            "automated IAM provisioning system. Analyze the 'Email Body', classify the human approver's final "
            "intent, and return a single valid JSON object only (no prose) with exactly these keys: "
            '"status" (one of "APPROVED", "DENIED", "REJECTED_CONTEXT_MISSING"), '
            '"approver_id" (string), "reason_summary" (a concise 1-2 sentence summary of the approver\'s reason). '
            "Use 'REJECTED_CONTEXT_MISSING' if the intent is ambiguous or contains no clear approval/denial language."
        )

        # Store config + a google-genai client (Vertex backend). Assigned via
        # __dict__ to bypass Pydantic field validation on the LlmAgent base.
        self.__dict__['project_id'] = project_id
        self.__dict__['_system_instruction'] = system_instruction
        self.__dict__['_client'] = genai.Client(
            vertexai=True, project=project_id, location=self.DEFAULT_REGION
        )

    def _perform_classification(self, email_body: str, sender_email: str) -> Dict[str, Any]:
        """Performs the classification using google-genai with structured JSON output."""
        max_retries = 3
        delay = 1  # Initial delay for exponential backoff

        user_query = f'Email Body to Classify: "{email_body}"'

        config = types.GenerateContentConfig(
            system_instruction=self._system_instruction,
            response_mime_type="application/json",
            temperature=0,
            max_output_tokens=256,
            # Disable thinking: this is a short structured classification and the
            # default thinking budget on 2.5 models adds seconds of latency.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        for attempt in range(max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self.DEFAULT_MODEL,
                    contents=user_query,
                    config=config,
                )

                json_text = response.text
                if json_text:
                    parsed_json = json.loads(json_text)
                    # Stamp the sender so the orchestrator can track the approver.
                    parsed_json['approver_id'] = sender_email
                    logging.info(f"NLU Classification: SUCCESS -> {parsed_json.get('status')} by {sender_email}")
                    return parsed_json

                logging.warning(f"Attempt {attempt+1}: NLU response was empty or malformed.")

            except Exception as e:
                logging.warning(f"Attempt {attempt+1}: genai SDK Error, Retrying in {delay}s. Error: {e}")
                time.sleep(delay)
                delay *= 2

        logging.error("NLU classification failed after all retries. Defaulting to REJECTED_CONTEXT_MISSING.")
        return {"status": "REJECTED_CONTEXT_MISSING", "approver_id": sender_email, "reason_summary": "NLU classification failed due to system error."}
