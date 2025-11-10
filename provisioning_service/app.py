import os
import logging
# Use absolute import since both files are copied to /app in the container
from provisioning_agent import IAMProvisioningAgent
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.requests import Request
import base64, json
from datetime import datetime

# Import ADK A2A utility to create A2A server
from google.adk.a2a.utils.agent_to_a2a import to_a2a

# Set logging to DEBUG level for more details
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.environ.get("PROJECT_ID")
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

REQUIRED = ("requested_role", "user_id", "justification")

# NEW: Get the service URL from environment (we'll set this during deployment)
SERVICE_URL = os.environ.get("SERVICE_URL", "")

if not PROJECT_ID:
    # A Cloud Run service must always have a Project ID, or it cannot initialize GCP services.
    logger.error("PROJECT_ID environment variable is missing!")
    raise EnvironmentError("PROJECT_ID not set")


logger.info("="*60)
logger.info(f"Starting IAM Provisioning A2A Service")
logger.info(f"PROJECT_ID: {PROJECT_ID}")
logger.info(f"HOST: {HOST}, PORT: {PORT}")
logger.info(f"SERVICE_URL: {SERVICE_URL}")
logger.info("="*60)

def _extract_tool_from_json_dict(d: dict):
    """
    Given a JSON dict potentially containing a tool_calls envelope, return (name, args).
    """
    if not isinstance(d, dict):
        return None, {}
    calls = d.get("tool_calls") or d.get("toolCalls")
    if not isinstance(calls, list) or not calls:
        return None, {}
    fn = calls[0].get("function", {}) if isinstance(calls[0], dict) else {}
    name = fn.get("name")
    raw_args = fn.get("arguments") or fn.get("args") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except Exception:
        logging.warning("Could not JSON-decode 'arguments'; using empty args")
        args = {}
    return name, args

def _parse_tool_from_parts(parts: list):
    """
    Iterate through all known shapes the SDK emits and return (tool_name, tool_input) or (None, {}).
    Handles:
      1) Top-level {mimeType:'application/json', data:{...}}
      2) {file:{mimeType:'application/json', bytes:BASE64}}
      3) {inlineData:{mimeType:'application/json', data:BASE64}}
      4) Ignore plain text for structured tool_calls.
    """
    if not parts:
        return None, {}

    for part in parts:
        # Shape 1: top-level mimeType + data
        if part.get("mimeType") == "application/json" and isinstance(part.get("data"), dict):
            logging.info("JSON part (top-level data) detected")
            name, args = _extract_tool_from_json_dict(part["data"])
            if name and args:
                return name, args

        # Shape 2: file.bytes (base64)
        if isinstance(part.get("file"), dict):
            f = part["file"]
            if f.get("mimeType") == "application/json" and f.get("bytes"):
                logging.info("JSON part (file.bytes) detected")
                try:
                    decoded = base64.b64decode(f["bytes"]).decode("utf-8")
                    payload = json.loads(decoded)
                    name, args = _extract_tool_from_json_dict(payload)
                    if name and args:
                        return name, args
                except Exception as e:
                    logging.warning(f"Failed to parse file.bytes JSON: {e}")

        # Shape 3: inlineData.data (base64)
        if isinstance(part.get("inlineData"), dict):
            idata = part["inlineData"]
            if idata.get("mimeType") == "application/json" and idata.get("data"):
                logging.info("JSON part (inlineData.data) detected")
                try:
                    decoded = base64.b64decode(idata["data"]).decode("utf-8")
                    payload = json.loads(decoded)
                    name, args = _extract_tool_from_json_dict(payload)
                    if name and args:
                        return name, args
                except Exception as e:
                    logging.warning(f"Failed to parse inlineData JSON: {e}")

        # Shape 4: text — log and skip for structured extraction
        if part.get("mimeType") == "text/plain" or part.get("kind") == "text":
            txt = part.get("text", "")
            logging.info("Text part encountered (ignored for tool_calls): %s", txt[:80])

    return None, {}

# 1. Initialize the Isolated Agent
logging.info(f"Initializing IAMProvisioningAgent for Project: {PROJECT_ID}")
provisioning_agent = IAMProvisioningAgent(project_id=PROJECT_ID)
logger.info(f"Agent initialized: {provisioning_agent.name}")
logger.info(f"Agent tools: {[tool.name for tool in provisioning_agent.tools]}")

# 2. Expose the Agent as an A2A Server
# The to_a2a() function creates a Starlette app with A2A protocol support
# In ADK 1.17.0, to_a2a() only takes the agent parameter
# ============================================================================
# A2A Protocol Endpoints (Correct paths per official ADK docs)
# ============================================================================

async def agent_card_handler(request: Request):
    """
    Serves the A2A agent card at /.well-known/agent.json
    Per official ADK docs: https://google.github.io/adk-docs/a2a/quickstart-exposing/
    """
    logger.info(f"Agent card requested from: {request.client.host if request.client else 'unknown'}")
    
    # Build the agent card following A2A protocol specification
    # Extract tool information
    tools_info = []
    for tool in provisioning_agent.tools:
        tool_info = {
            "id": f"{provisioning_agent.name}-{tool.name}",
            "name": tool.name,
            "description": "Executes IAM policy changes with JIT elevated credentials",
            "tags": ["llm", "tools"]
        }
        tools_info.append(tool_info)
    
    # Add the agent's main skill
    agent_skill = {
        "id": provisioning_agent.name,
        "name": "model",
        "description": provisioning_agent.description,
        "tags": ["llm"]
    }
    
    #Use SERVICE_URL from environment if set, otherwise construct from headers
    if SERVICE_URL:
        base_url = SERVICE_URL
        logger.info(f"Using SERVICE_URL from environment: {base_url}")
    else:
        # Fallback: construct from request headers
        # Get host from X-Forwarded-Host or Host header
        host = request.headers.get('x-forwarded-host') or request.headers.get('host') or request.url.netloc
        
        # Cloud Run always uses HTTPS externally
        base_url = f"https://{host}"
        logger.info(f"Constructed base_url from headers: {base_url}")
    
    # Construct full agent card following A2A 0.2.6 specification
    agent_card = {
        "name": provisioning_agent.name,
        "description": provisioning_agent.description,
        "version": "1.0.0",
        "protocolVersion": "0.2.6",
        "url": base_url,
        "endpoint": f"{base_url}/tasks/send",
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "supportsAuthenticatedExtendedCard": False,
        "skills": [agent_skill] + tools_info
    }
    
    logger.info(f"Agent card URL field: {agent_card['url']}")
    logger.info(f"Agent card 'endpoint' field: {agent_card.get('endpoint')}")  # ADD THIS LINE
    logger.info(f"Agent card has {len(tools_info)} tool(s)")
    logger.debug(f"Agent card: {json.dumps(agent_card, indent=2)}")
    
    return JSONResponse(agent_card, headers={
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    })

async def tasks_send_handler(request: Request):
    """
    Handles A2A task requests (JSON-RPC format)
    This is the standard A2A endpoint for sending tasks
    """
    try:
        body = await request.json()
        logger.info(f"Task request received: {json.dumps(body, indent=2)}")
        
        # Extract message from A2A JSON-RPC format
        # A2A uses JSON-RPC with params containing the message
        params = body.get("params", {})
        message = params.get("message", {})
        parts = message.get("parts", [])
        
        # Extract tool call information from the message
        # The LLM's tool call will be in the message parts
        tool_name, tool_input = _parse_tool_from_parts(parts)

        # Fallback: accept direct params if tool_calls not present
        if not tool_input and params:
            logging.info("Checking top-level params fallback")
            if all(k in params for k in REQUIRED):
                tool_name = tool_name or "execute_iam_set_tool"
                tool_input = {
                    "requested_role": params["requested_role"],
                    "user_id": params["user_id"],
                    "justification": params["justification"],
                }
        # Validate
        missing = [k for k in REQUIRED if k not in (tool_input or {})]
        if missing:
            logging.error("Missing required tool parameters")
            logging.error("tool_input: %s", tool_input)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {
                    "code": -32602,
                    "message": "Missing required parameters: " + ", ".join(missing),
                    "data": {"received_params": tool_input}
                }
            }, status_code=400)

        # Default tool name if the envelope didn’t specify it
        tool_name = tool_name or "execute_iam_set_tool"
        
        # Execute the tool
        for tool in provisioning_agent.tools:
            if tool.name == tool_name or tool.name == "execute_iam_set_tool":
                result = tool.func(**tool_input)
                logger.info(f"Tool execution result: {result.get('status')}")
                
                # CORRECT A2A RESPONSE FORMAT with Task structure
                task_id = str(body.get("id"))
                message_id = f"msg-{task_id}"
                
                response = {
                    "jsonrpc": "2.0",
                    "id": task_id,
                    "result": {
                        "task": {
                            "contextId": task_id,
                            "id": task_id,
                            "status": "completed"  # Required field
                        },
                        "message": {
                            "messageId": message_id,
                            "role": "agent",  # Must be "agent" or "user"
                            "parts": [
                                {
                                    "text": json.dumps(result)  # Tool result as JSON string
                                }
                            ]
                        }
                    }
                }
                
                logger.info(f"Returning response with task status: completed")
                return JSONResponse(response, status_code=200)
        
        # Tool not found
        logger.error(f"Tool not found: {tool_name}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {
                "code": -32601,
                "message": f"Tool '{tool_name}' not found"
            }
        }, status_code=404)
        
    except Exception as e:
        logger.error(f"Error processing task: {e}", exc_info=True)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": body.get("id", None),
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }, status_code=500)

async def health_handler(request: Request):
    """Health check"""
    return JSONResponse({
        "status": "healthy",
        "service": "iam-provisioning-a2a-service",
        "agent": provisioning_agent.name,
        "project_id": PROJECT_ID
    })

async def root_handler(request: Request):
    """Root endpoint - handle both GET and POST"""
    if request.method == "GET":
        return JSONResponse({
            "service": "IAM Provisioning A2A Service",
            "agent": provisioning_agent.name,
            "endpoints": {
                "agent_card": "/.well-known/agent.json",
                "tasks": "/tasks/send",
                "health": "/health"
            }
        })
    elif request.method == "POST":
        # Some A2A clients might POST to root - redirect to tasks handler
        logger.info("Received POST to root endpoint, delegating to tasks/send handler")
        return await tasks_send_handler(request)

# ============================================================================
# Create Starlette App with CORRECT A2A paths
# ============================================================================

app = Starlette(
    debug=False,
    routes=[
        Route('/', root_handler, methods=['GET', 'POST']),
        # CRITICAL: Use agent.json not agent-card (per ADK docs)
        Route('/.well-known/agent.json', agent_card_handler, methods=['GET']),
        Route('/tasks/send', tasks_send_handler, methods=['POST']),
        Route('/health', health_handler, methods=['GET']),
    ]
)

logger.info("="*60)
logger.info("Routes registered:")
logger.info(f"  GET  /")
logger.info(f"  GET  /.well-known/agent.json (A2A agent card)")
logger.info(f"  POST /tasks/send (A2A task execution)")
logger.info(f"  GET  /health")
logger.info("="*60)
logger.info(f"Service ready on {HOST}:{PORT}")
logger.info("="*60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")