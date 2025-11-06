import os
import logging
# Use absolute import since both files are copied to /app in the container
from provisioning_agent import IAMProvisioningAgent


# Import ADK A2A utility to create A2A server
from google.adk.a2a.utils.agent_to_a2a import to_a2a

logging.basicConfig(level=logging.INFO)

# Configuration
PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    # A Cloud Run service must always have a Project ID, or it cannot initialize GCP services.
    logging.error("PROJECT_ID environment variable is missing!")
    exit(1)


# Get port from environment (Cloud Run sets this)
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

# 1. Initialize the Isolated Agent
logging.info(f"Initializing IAMProvisioningAgent for Project: {PROJECT_ID}")
provisioning_agent = IAMProvisioningAgent(project_id=PROJECT_ID)

# 2. Expose the Agent as an A2A Server
# The to_a2a() function creates a Starlette app with A2A protocol support
# In ADK 1.17.0, to_a2a() only takes the agent parameter
app = to_a2a(agent=provisioning_agent)

if __name__ == "__main__":
    import uvicorn
    # Local testing command
    print(f"Starting local Provisioning A2A Server on http://127.0.0.1:8001")
    uvicorn.run(app, host="127.0.0.1", port=8001)

# Cloud Run execution will use the 'app' object directly.