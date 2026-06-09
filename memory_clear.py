"""
Clear Memory Bank entries for a requester so the demo can run the full cycle
fresh (otherwise the "active grant" short-circuit fires for up to an hour).

Run locally with ADC:
    python memory_clear.py                      # clears DEMO_REQUESTER_EMAIL from .env
    python memory_clear.py someone@example.com  # clears a specific requester
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.environ["PROJECT_ID"]
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
ENGINE_ID = os.environ["AGENT_ENGINE_ID"]
ENGINE_PATH = f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}"

requester = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "DEMO_REQUESTER_EMAIL", "narmada.c.nannaka@accenture.com"
)

import google.auth
from google.auth.transport.requests import AuthorizedSession

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
session = AuthorizedSession(creds)

BASE = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1"

resp = session.post(
    f"{BASE}/{ENGINE_PATH}/memories:retrieve",
    json={"scope": {"user_id": requester}},
    timeout=30,
)
resp.raise_for_status()

retrieved = resp.json().get("retrievedMemories", [])
if not retrieved:
    print(f"No memories found for {requester}.")
    sys.exit(0)

deleted = 0
for item in retrieved:
    mem = item.get("memory", {})
    name = mem.get("name", "")
    fact = mem.get("fact", "")
    if not name:
        continue
    del_resp = session.delete(
        f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/{name}",
        timeout=30,
    )
    if del_resp.status_code in (200, 204):
        deleted += 1
        print(f"deleted: {fact}")
    else:
        print(f"failed to delete {name}: {del_resp.status_code} {del_resp.text}")

print(f"\nDeleted {deleted} memory(ies) for {requester}.")
