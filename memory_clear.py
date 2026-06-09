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
import vertexai

load_dotenv()

PROJECT = os.environ["PROJECT_ID"]
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
ENGINE_ID = os.environ["AGENT_ENGINE_ID"]
ENGINE = f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}"

requester = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "DEMO_REQUESTER_EMAIL", "narmada.c.nannaka@accenture.com"
)

client = vertexai.Client(project=PROJECT, location=LOCATION)

deleted = 0
for item in client.agent_engines.memories.retrieve(name=ENGINE, scope={"user_id": requester}):
    mem = getattr(item, "memory", None) or item
    name = getattr(mem, "name", None)
    fact = getattr(mem, "fact", "")
    if not name:
        continue
    try:
        client.agent_engines.memories.delete(name=name)
        deleted += 1
        print(f"deleted: {fact}")
    except Exception as e:
        print(f"failed to delete {name}: {e}")

print(f"\nDeleted {deleted} memory(ies) for {requester}.")
