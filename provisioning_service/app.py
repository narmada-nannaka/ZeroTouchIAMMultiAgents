# provisioning_service/app.py
# Exposes IAMProvisioningAgent as an A2A server via ADK's to_a2a() helper.
#
# Auth: Cloud Run IAM (deploy --no-allow-unauthenticated; grant orchestrator-sa
#   roles/run.invoker). No custom middleware.
# Determinism: IAMProvisioningAgent's before_model_callback executes the IAM
#   mutation deterministically and bypasses the LLM (fails closed).
# Card URL: to_a2a bakes the card's rpc_url from host/port/protocol. On Cloud Run
#   the card must advertise the PUBLIC https URL (from SERVICE_URL), NOT localhost
#   -- uvicorn still binds the internal $PORT separately.

import os
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from provisioning_agent import IAMProvisioningAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)

PORT = int(os.environ.get("PORT", 8080))
SERVICE_URL = os.environ.get("SERVICE_URL", "")

provisioning_agent = IAMProvisioningAgent()

# These params feed ONLY the agent card's advertised rpc_url. When deployed,
# derive the public host from SERVICE_URL; locally fall back to localhost:PORT.
if SERVICE_URL:
    parsed = urlparse(SERVICE_URL)
    card_host = parsed.hostname
    card_protocol = parsed.scheme or "https"
    card_port = parsed.port or (443 if card_protocol == "https" else 80)
else:
    card_host, card_protocol, card_port = "localhost", "http", PORT

app = to_a2a(provisioning_agent, host=card_host, port=card_port, protocol=card_protocol)
logging.info(
    f"IAM Provisioning A2A service ready. "
    f"Card rpc_url={card_protocol}://{card_host}:{card_port}/ ; uvicorn binds :{PORT}"
)
