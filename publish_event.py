"""
Publish a test event to Pub/Sub for the deployed E2E demo.

Uses the Python client so the JSON payload is a dict -- avoids PowerShell's
native-argument quote-stripping that breaks `gcloud pubsub topics publish --message`.

Usage (repo root):
    python publish_event.py request    # kicks off the provisioning flow
    python publish_event.py approve     # sends the approver's free-text -> NLU
    python publish_event.py deny        # a denial, to show NLU classifying the other way
"""
import os
import sys
import json
from dotenv import load_dotenv
from google.cloud import pubsub_v1

load_dotenv()
PROJECT = os.environ["PROJECT_ID"]
SESSION_ID = "session-demo-001"

publisher = pubsub_v1.PublisherClient()


def publish(topic_id: str, payload: dict):
    topic = publisher.topic_path(PROJECT, topic_id)
    msg_id = publisher.publish(topic, json.dumps(payload).encode("utf-8")).result()
    print(f"Published to {topic_id} (msg {msg_id}):")
    print(f"  {json.dumps(payload)}")


kind = sys.argv[1] if len(sys.argv) > 1 else "request"

if kind == "request":
    publish("iam-request-topic", {
        "session_id": SESSION_ID,
        "user_id": "narmada.c.nannaka@accenture.com",
        "requested_role": "roles/storage.admin",
        "project_scope": "zero-touch-iam-agent",
        "user_timezone": "UTC",
    })
elif kind == "approve":
    publish("iam-approvals-topic", {
        "session_id": SESSION_ID,
        "approver_email": "manager@accenture.com",
        "raw_response_text": "Yes, I reviewed the justification and approve this access request.",
    })
elif kind == "deny":
    publish("iam-approvals-topic", {
        "session_id": SESSION_ID,
        "approver_email": "manager@accenture.com",
        "raw_response_text": "No — this looks excessive for the stated purpose, denying it.",
    })
else:
    print("Usage: python publish_event.py [request|approve|deny]")
