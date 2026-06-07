"""
Creates a BARE Vertex AI Agent Engine instance (no agent code, no staging bucket).

A bare instance provisions in seconds and provides Sessions + Memory Bank out of
the box -- exactly what VertexAiSessionService and VertexAiMemoryBankService attach
to. Deploying actual agent code (and the staging bucket it requires) is a separate,
later step when we publish the real orchestrator into Agent Runtime (Pattern B).

The Agent Engine SDK surface moved during the Gemini Enterprise rebrand, so this
script tries the known client forms in order and reports which one worked.

Usage:
    python -m deployment.create_engine
"""
import os
from dotenv import load_dotenv

load_dotenv()
PROJECT = os.environ["PROJECT_ID"]
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
DISPLAY_NAME = os.environ.get("AGENT_ENGINE_DISPLAY_NAME", "zerotouch-iam-engine")


def _extract_name(engine) -> str:
    """Pull the resource name out of whatever object create() returns."""
    api_resource = getattr(engine, "api_resource", None)
    if api_resource is not None and getattr(api_resource, "name", None):
        return api_resource.name
    if getattr(engine, "resource_name", None):
        return engine.resource_name
    if getattr(engine, "name", None):
        return engine.name
    return str(engine)


def _create(client):
    """Create a bare engine with display_name; fall back to no-arg if the
    SDK version doesn't accept display_name as a kwarg."""
    try:
        return client.agent_engines.create(display_name=DISPLAY_NAME)
    except TypeError:
        return client.agent_engines.create()


def via_genai_client():
    from google import genai
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _extract_name(_create(client))


def via_vertexai_client():
    import vertexai
    client = vertexai.Client(project=PROJECT, location=LOCATION)
    return _extract_name(_create(client))


print(f"Creating bare Agent Engine in {PROJECT}/{LOCATION} ...")
print()

resource_name = None
for label, fn in [("google.genai Client", via_genai_client),
                  ("vertexai.Client", via_vertexai_client)]:
    try:
        print(f"Trying: {label}")
        resource_name = fn()
        print(f"  SUCCESS via {label}")
        break
    except Exception as e:
        print(f"  Failed: {type(e).__name__}: {e}")
        print()

if resource_name:
    agent_engine_id = resource_name.split("/")[-1]
    print()
    print("=" * 70)
    print("AGENT ENGINE CREATED")
    print("=" * 70)
    print(f"Resource name:   {resource_name}")
    print(f"Agent Engine ID: {agent_engine_id}")
    print()
    print("Set this in your .env:")
    print(f"  AGENT_ENGINE_ID={agent_engine_id}")
    print("=" * 70)
else:
    print()
    print("All approaches failed. Paste the errors above and we'll adjust the API call.")
