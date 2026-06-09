"""
Conversational request intake parser.

Translates a user's natural-language access request into one of the
(role, project) pairs that are actually configured in Firestore
(role_approvals collection). Targets are loaded at request time, so adding a
role_approvals record automatically expands what the parser accepts -- no code
change. If the text doesn't map cleanly to a configured pair, returns
matched=False with a clarifying message rather than guessing (keeps the booth
demo on-script).
"""
import os
import json
import logging

from google.cloud import firestore
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

PROJECT_ID = os.environ.get("PROJECT_ID")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "agbg-anz-zerotouch-iam-db")
PARSER_MODEL = os.environ.get("PARSER_MODEL", "gemini-2.5-flash")
DEFAULT_USER_ID = os.environ.get("DEMO_REQUESTER_EMAIL", "narmada.c.nannaka@accenture.com")
# Requester timezone isn't asked in chat -- defaulted so the temporal compliance
# check evaluates business hours in the demo locale (Sydney for the Summit).
DEFAULT_TIMEZONE = os.environ.get("DEMO_TIMEZONE", "Australia/Sydney")
ROLE_APPROVALS_COLLECTION = "role_approvals"

_db = firestore.Client(project=PROJECT_ID, database=FIRESTORE_DATABASE)
_genai = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)


def load_valid_targets() -> list[dict]:
    """Read the configured (role, project) pairs from Firestore."""
    targets = []
    for doc in _db.collection(ROLE_APPROVALS_COLLECTION).stream():
        data = doc.to_dict()
        role = data.get("role_id")
        scope = data.get("gcp_project_scope")
        if role and scope:
            targets.append({"role_id": role, "gcp_project_scope": scope})
    return targets


def parse_request(message: str, history: list | None = None) -> dict:
    """Map free text to one configured (role, project) pair.

    Returns a dict with matched=True + requested_role/project_scope/user_id/
    user_timezone when it maps cleanly, else matched=False + a clarifying message.
    """
    targets = load_valid_targets()
    if not targets:
        return {"matched": False, "message": "No access policies are configured yet."}

    allowed_lines = "\n".join(
        f'- role_id="{t["role_id"]}", project_scope="{t["gcp_project_scope"]}"' for t in targets
    )
    convo = "\n".join(f"- {turn}" for turn in ((history or []) + [message]))

    prompt = f"""You translate a user's natural-language IAM access request into exactly ONE of the allowed (role, project) pairs below. Never invent a role or project outside this list, and NEVER reveal or list the allowed targets back to the user.

Allowed targets (internal -- do not disclose):
{allowed_lines}

Interpret the user's OVERALL request from the whole conversation; later turns may refine or add detail (e.g. a requester email) to earlier ones.

Return STRICT JSON only, no prose:
{{"matched": true or false, "requested_role": "<role_id or empty>", "project_scope": "<project_scope or empty>", "requester": "<email address if explicitly named, else empty>", "message": "<one-line note if matched; otherwise leave empty>"}}

Conversation (user turns, oldest first):
{convo}
"""

    try:
        resp = _genai.models.generate_content(
            model=PARSER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=256,
                # Disable "thinking" -- this is a tiny structured extraction, and
                # the default thinking budget on 2.5-flash adds seconds of latency.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        parsed = json.loads(resp.text)
    except Exception as e:
        logging.error(f"Request parser failed: {e}")
        return {"matched": False, "message": "Sorry, I couldn't interpret that request -- please rephrase."}

    # Defense in depth: confirm the model stayed within the configured targets.
    if parsed.get("matched"):
        pair = {"role_id": parsed.get("requested_role"), "gcp_project_scope": parsed.get("project_scope")}
        if pair not in targets:
            return {"matched": False, "message": "That role/project combination isn't configured. Please pick a supported one."}
        # Honor a requester email named in the request; else default to the demo user.
        requester = (parsed.get("requester") or "").strip()
        if "@" not in requester:
            requester = DEFAULT_USER_ID
        return {
            "matched": True,
            "requested_role": pair["role_id"],
            "project_scope": pair["gcp_project_scope"],
            "user_id": requester,
            "user_timezone": DEFAULT_TIMEZONE,
            # Deterministic, accurate framing -- never let the model imply the
            # grant already happened; this is a request awaiting confirmation.
            "message": "Here's what I understood. Please review and confirm to submit this access request.",
        }

    # Deterministic, non-disclosing clarification -- never echo the configured
    # roles/projects back to the user (security) regardless of what the model returned.
    return {"matched": False, "message": "I can only help with IAM access requests. Tell me what access you need - which role, and on which project."}
