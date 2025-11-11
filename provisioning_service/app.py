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

# Set logging to DEBUG level for more details
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
#PROJECT_ID = os.environ.get("PROJECT_ID")
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

REQUIRED = ("requested_role", "user_id", "justification", "gcp_project_scope")

# NEW: Get the service URL from environment (we'll set this during deployment)
SERVICE_URL = os.environ.get("SERVICE_URL", "")

# if not PROJECT_ID:
#     # A Cloud Run service must always have a Project ID, or it cannot initialize GCP services.
#     logger.error("PROJECT_ID environment variable is missing!")
#     raise EnvironmentError("PROJECT_ID not set")


logger.info("="*60)
logger.info(f"Starting IAM Provisioning A2A Service")
#logger.info(f"PROJECT_ID: {PROJECT_ID}")
logger.info(f"HOST: {HOST}, PORT: {PORT}")
logger.info(f"SERVICE_URL: {SERVICE_URL}")
logger.info("="*60)

def _extract_tool_from_json_dict(d: dict):
    """
    Given a JSON dict potentially containing a tool_calls envelope, return (name, args).
    """
    logging.info(f"[DEBUG] _extract_tool_from_json_dict input: {d}")
    if not isinstance(d, dict):
        return None, {}
    calls = d.get("tool_calls") or d.get("toolCalls")
    if not isinstance(calls, list) or not calls:
        logging.info("[DEBUG] No tool_calls found in dict")
        return None, {}
    fn = calls[0].get("function", {}) if isinstance(calls[0], dict) else {}
    name = fn.get("name")
    raw_args = fn.get("arguments") or fn.get("args") or "{}"
    logging.info(f"[DEBUG] Raw arguments string: {raw_args}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        logging.info(f"[DEBUG] Parsed arguments dict: {args}")
    except Exception as e:
        logging.warning(f"Could not JSON-decode 'arguments': {e}")
        args = {}
    return name, args

def _parse_tool_from_parts(parts: list):
    """
    Iterate through all known shapes the SDK emits and return (tool_name, tool_input) or (None, {}).
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
#logging.info(f"Initializing IAMProvisioningAgent for Project: {PROJECT_ID}")
provisioning_agent = IAMProvisioningAgent()
logger.info(f"Agent initialized: {provisioning_agent.name}")
logger.info(f"Agent tools: {[tool.name for tool in provisioning_agent.tools]}")

# 2. Expose the Agent as an A2A Server
# ============================================================================
# A2A Protocol Endpoints (Correct paths per official ADK docs)
# ============================================================================

async def agent_card_handler(request: Request):
    """
    Serves the A2A agent card at /.well-known/agent.json
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
        logger.info(f"[DEBUG] Raw task request body: {json.dumps(body, indent=2)}")
        
        # Extract message from A2A JSON-RPC format
        # A2A uses JSON-RPC with params containing the message
        params = body.get("params", {})
        message = params.get("message", {})
        parts = message.get("parts", [])
        
        # Extract tool call information from the message
        # The LLM's tool call will be in the message parts
        logger.info(f"[DEBUG] Message parts to parse: {json.dumps(parts, indent=2)}")
        tool_name, tool_input = _parse_tool_from_parts(parts)
        logger.info(f"[DEBUG] Parsed tool_name: {tool_name}")
        logger.info(f"[DEBUG] Parsed tool_input: {tool_input}")

        # Fallback: accept direct params if tool_calls not present
        if not tool_input and params:
            logging.info("Checking top-level params fallback")
            if all(k in params for k in REQUIRED):
                tool_name = tool_name or "execute_iam_set_tool"
                tool_input = {
                    "requested_role": params.get("requested_role"),
                    "user_id": params.get("user_id"),
                    "justification": params.get("justification"),
                    "gcp_project_scope": params.get("gcp_project_scope")
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
                logger.info(f"Tool execution result: {result.status}")
                
                # CORRECT A2A RESPONSE FORMAT with Task structure
                request_id = body.get("id", "unknown")
                task_id = str(request_id)
                timestamp_str = datetime.now().isoformat()
                message_id = f"msg-{task_id}"
                context_id = params.get("contextId", f"ctx-{task_id}")

                # Serialize the tool result
                result_data = result.model_dump() if hasattr(result, 'model_dump') else result
                result_json = json.dumps(result_data)

                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        # Task fields at result level
                        "contextId": context_id,
                        "id": task_id,
                        "status": "completed",
                        "createdAt": timestamp_str,
                        # Message fields at result level
                        "messageId": message_id,
                        "role": "agent",
                        "parts": [
                            {
                                "text": json.dumps(result_data)
                            }
                        ]
                    }
                }
                
                logger.info(f"Returning A2A response with task status: completed")
                logger.info(f"Full response to orchestrator: {json.dumps(response, indent=2)}")
                
                return JSONResponse(response, status_code=200, headers={
                    "Content-Type": "application/json"
                })
        
        # Tool not found
        logger.error(f"Tool not found: {tool_name}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {
                "code": -32601,
                "message": f"Tool '{tool_name}' not found in agent '{provisioning_agent.name}'",
                "data": {
                    "requested_tool": tool_name,
                    "available_tools": [t.name for t in provisioning_agent.tools]
                }
            }
        }, status_code=404)
        
    except Exception as e:
        logger.error(f"Error processing task: {e}", exc_info=True)
        request_id = None
        try:
            #If Body exists and is a dict, try to pull the id
            if 'body' in locals() and isinstance(body, dict):
                request_id = body.get("id")
        except Exception:
            # Swallow any secondary errors to preserve original exception context
            pass
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": f"Internal server error: {str(e)}",
                "data": {
                    "error_type": type(e).__name__
                }
            }
        }, status_code=500)

async def health_handler(request: Request):
    """Health check"""
    return JSONResponse({
        "status": "healthy",
        "service": "iam-provisioning-a2a-service",
        "agent": provisioning_agent.name,
        #"project_id": PROJECT_ID
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