"""
Publish a test event to Pub/Sub for the deployed E2E demo.

Uses the Python client so the JSON payload is a dict -- avoids PowerShell's
native-argument quote-stripping that breaks `gcloud pubsub topics publish --message`.

Session handling: `request`/`request-standard` generate a fresh unique session id
and store it in .session_id; `approve`/`deny` read that file so they target the
same session. This avoids the "session already exists" error on re-runs
(VertexAiSessionService rejects duplicate session ids).

Usage (repo root):
    python publish_event.py request           # storage.admin -> temporal policy (rejects outside Mon-Fri 08:00-17:00 UTC)
    python publish_event.py request-standard  # storage.objectViewer -> standard policy (predefined role; gets 1h condition; no temporal restriction)
    python publish_event.py approve           # approver free-text -> NLU -> provision
    python publish_event.py deny              # denial, to show NLU the other way
"""
import os
import sys
import json
import uuid
from dotenv import load_dotenv
from google.cloud import pubsub_v1

load_dotenv()
PROJECT = os.environ["PROJECT_ID"]
SESSION_FILE = ".session_id"

publisher = pubsub_v1.PublisherClient()


def publish(topic_id: str, payload: dict):
    topic = publisher.topic_path(PROJECT, topic_id)
    msg_id = publisher.publish(topic, json.dumps(payload).encode("utf-8")).result()
    print(f"Published to {topic_id} (msg {msg_id}):")
    print(f"  {json.dumps(payload)}")


def new_session_id() -> str:
    sid = f"session-{uuid.uuid4()}"
    with open(SESSION_FILE, "w") as f:
        f.write(sid)
    print(f"New session: {sid}")
    return sid


def current_session_id() -> str:
    try:
        with open(SESSION_FILE) as f:
            sid = f.read().strip()
        print(f"Using session: {sid}")
        return sid
    except FileNotFoundError:
        print("No .session_id found -- run 'python publish_event.py request' first.")
        sys.exit(1)


kind = sys.argv[1] if len(sys.argv) > 1 else "request"

if kind == "request":
    publish("iam-request-topic", {
        "session_id": new_session_id(),
        "user_id": "testengineer@narmadanannaka.com",
        "requested_role": "roles/storage.admin",
        "project_scope": "project-data-eng-479300",
        "user_timezone": "UTC",
    })
elif kind == "request-standard":
    publish("iam-request-topic", {
        "session_id": new_session_id(),
        "user_id": "testengineer@narmadanannaka.com",
        "requested_role": "roles/storage.objectViewer",
        "project_scope": "project-ops-dashboard-477606",
        "user_timezone": "UTC",
    })
elif kind == "approve":
    publish("iam-approvals-topic", {
        "session_id": current_session_id(),
        "approver_email": "manager@accenture.com",
        "raw_response_text": "Yes, I reviewed the justification and approve this access request.",
    })
elif kind == "deny":
    publish("iam-approvals-topic", {
        "session_id": current_session_id(),
        "approver_email": "manager@accenture.com",
        "raw_response_text": "No -- this looks excessive for the stated purpose, denying it.",
    })
else:
    print("Usage: python publish_event.py [request|request-standard|approve|deny]")
