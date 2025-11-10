# agents/context_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any, Tuple
import logging
import datetime
import random # Used for RAG simulation consistency

logging.basicConfig(level=logging.INFO)

class PolicyContextAgent(LlmAgent):
    """
    A multi-tool agent responsible for RAG retrieval and compliance enforcement.
    """
    def __init__(self, project_id: str, corpus_id: str, **kwargs):

        # 1. Define the RAG Tool (Simulated)
        rag_tool = FunctionTool(func=self._retrieve_policy_text)

        # 2. Define the Compliance Check Tool (Guardrail Logic)
        check_tool = FunctionTool(func=self._check_temporal_compliance)

        super().__init__(
            name="PolicyContextAgent",
            description="Manages policy grounding via RAG and executes compliance guardrails.",
            tools=[rag_tool, check_tool], # Registering both tools
            **kwargs
        )

        # Assign attributes *after* super().__init__ using __dict__ 
        # to prevent Pydantic initialization conflicts.
        self.__dict__['_project_id'] = project_id
        self.__dict__['_corpus_id'] = corpus_id

    # Tool 1: Retrieves Policy Text (Simulated RAG - **ACTIVATED**)
    def _retrieve_policy_text(self, doc_id: str) -> Dict[str, Any]:
        """Retrieves the full, current policy text and constraints from the configured RAG Corpus 
        using the policy document ID (e.g., IAM_POL_003)."""
        
        logging.info(f"RAG: Retrieving policy context for doc_id: {doc_id} from corpus {self._corpus_id}")

        # --- PRODUCTION ACTIVATION: Simulated RAG Result based on Policy ID ---
        # In a real environment, this would be a client call to Vertex AI Search or an internal
        # knowledge store, returning a structured summary of the policy.

        # Policy IAM_POL_001: Standard, always compliant.
        if doc_id == "IAM_POL_001":
            return {
                "policy_text_summary": "Standard access policy. Requires management approval. No temporal constraints.",
                "constraint_type": "none",
                "risk_score": 1
            }
        
        # Policy IAM_POL_002: Requires Temporal Check (Guardrail Activation)
        elif doc_id == "IAM_POL_002":
            return {
                "policy_text_summary": "Elevated access policy for emergency use. Requires temporal access check (business hours only).",
                "constraint_type": "temporal_access_only",
                "risk_score": 5
            }
        
        # Policy IAM_POL_003: High risk, always denied by compliance.
        elif doc_id == "IAM_POL_003":
            return {
                "policy_text_summary": "High-risk administrative role. Access is strictly forbidden by automated compliance guardrail.",
                "constraint_type": "guardrail_deny",
                "risk_score": 9
            }
        
        # Default fallback
        return {
            "policy_text_summary": "No specific policy found. Default to standard low-risk procedure.",
            "constraint_type": "none",
            "risk_score": 0
        }

    # Tool 2: Executes Compliance Check (Guardrail - **ACTIVATED**)
    def _check_temporal_compliance(self, constraint_type: str) -> Tuple[bool, str]:
        """
        Executes a context-aware security check (e.g., temporal restriction) against the 
        current time to enforce policy guardrails.
        """
        logging.info(f"Compliance Check: Executing guardrail for constraint_type: {constraint_type}")
        
        # 1. TEMPORAL CHECK
        if constraint_type == "temporal_access_only":
            current_hour = datetime.datetime.now().hour
            
            # Constraint is 08:00 to 17:00 (8 AM to 5 PM, non-inclusive of 17:00).
            if current_hour >= 8 and current_hour < 17:
                logging.info(f"Compliance Check: PASS (Hour {current_hour} is within 08:00-17:00)")
                return True, "Inside business hours."
            else:
                # Failure path
                logging.warning(f"Compliance Check: FAIL (Hour {current_hour} is outside 08:00-17:00)")
                return False, "Temporal access restricted to business hours (08:00-17:00)."
        
        # 2. IMMEDIATE DENY CHECK
        elif constraint_type == "guardrail_deny":
            logging.warning("Compliance Check: FAIL - Policy explicitly forbidden by automated guardrail.")
            return False, "Access explicitly forbidden by policy context guardrail."
            
        # 3. DEFAULT PASS
        return True, "No temporal or explicit compliance constraints applied."
