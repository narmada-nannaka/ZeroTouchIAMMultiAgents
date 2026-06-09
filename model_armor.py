"""
Model Armor inline screening (sanitizeUserPrompt) for untrusted text that reaches
an LLM -- the conversational request and the approver's free-text reply.

Scoped to our agent via a Model Armor template (not project-wide floor settings,
which would screen the whole shared Accenture project). Fails OPEN (allow + log)
so a screening hiccup never stalls the booth demo.
"""
import os
import logging
import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = os.environ.get("PROJECT_ID")
MA_LOCATION = os.environ.get("MODEL_ARMOR_LOCATION", "us-central1")
MA_TEMPLATE = os.environ.get("MODEL_ARMOR_TEMPLATE", "agbg_anz_zerotouch_armortemp")
_ENDPOINT = (
    f"https://modelarmor.{MA_LOCATION}.rep.googleapis.com/v1"
    f"/projects/{PROJECT}/locations/{MA_LOCATION}/templates/{MA_TEMPLATE}:sanitizeUserPrompt"
)

_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
_session = AuthorizedSession(_creds)


def screen_prompt(text: str):
    """Return (blocked: bool, reason: str). Fails open on any error."""
    try:
        resp = _session.post(_ENDPOINT, json={"userPromptData": {"text": text}}, timeout=10)
        resp.raise_for_status()
        result = (resp.json() or {}).get("sanitizationResult", {})
        if result.get("filterMatchState") == "MATCH_FOUND":
            return True, "Prompt injection / policy violation blocked"
        return False, "No threats detected"
    except Exception as e:
        logging.warning(f"Model Armor screen failed (failing open): {e}")
        return False, "Model Armor unavailable"
