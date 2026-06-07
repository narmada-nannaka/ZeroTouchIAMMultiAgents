# app.py (Entry point for Orchestrator Cloud Run Service)

import os
import logging
import datetime
import base64, json, uuid
import jwt
from google.cloud import firestore
from google.cloud import pubsub_v1
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse, HTMLResponse
from starlette.requests import Request
from agents.orchestrator import IAMOrchestrator
from google.adk.sessions import VertexAiSessionService, Session
from google.adk.events import Event, EventActions
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()  # load .env before any os.environ.get() calls below

# --- 1. CONFIGURATION ---
logging.basicConfig(level=logging.INFO)

# Retrieve configuration from environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
A2A_PROVISIONER_URL = os.environ.get("A2A_PROVISIONER_URL")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
RAG_ENGINE_ID = os.environ.get("RAG_ENGINE_ID")
RAG_DATA_STORE_ID = os.environ.get("RAG_DATA_STORE_ID")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") 
APPROVAL_CALLBACK_URL = os.environ.get("APPROVAL_CALLBACK_URL", "http://localhost:8080/approve")
IAM_TOPIC_ID = os.environ.get("IAM_TOPIC_ID", "iam-request-topic")
APPROVALS_TOPIC_ID = os.environ.get("APPROVALS_TOPIC_ID", "iam-approvals-topic")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "agbg-anz-zerotouch-iam-db")
AGENT_ENGINE_LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")

if not PROJECT_ID or not A2A_PROVISIONER_URL:
    logging.error("Missing required environment variables (PROJECT_ID or A2A_PROVISIONER_URL).")
    raise EnvironmentError("Deployment environment variables are not set correctly.")

if not RAG_ENGINE_ID or not RAG_DATA_STORE_ID:
    logging.error("Missing required RAG configuration (RAG_ENGINE_ID or RAG_DATA_STORE_ID).")
    raise EnvironmentError("RAG configuration environment variables are not set correctly.")

if not AGENT_ENGINE_ID:
    logging.error("Missing AGENT_ENGINE_ID — run 'python -m deployment.create_engine' and set it in .env.")
    raise EnvironmentError("AGENT_ENGINE_ID is not set.")

if not SENDER_EMAIL:
    logging.warning("SENDER_EMAIL not set. Communication Agent will run in simulation mode (no real emails sent).")

# Normalize to https and strip trailing slash
if A2A_PROVISIONER_URL.startswith("http://"):
    logging.warning("A2A_PROVISIONER_URL is http, normalizing to https for Cloud Run")
    A2A_PROVISIONER_URL = "https://" + A2A_PROVISIONER_URL.split("://",1)[1]
A2A_PROVISIONER_URL = A2A_PROVISIONER_URL.rstrip("/")

PROVISIONING_REQUESTS_COLLECTION = "provisioning-requests" #dedicated collection for audit trail

# Get port from environment (Cloud Run sets this)
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

# --- 3. INITIALIZE FIRESTORE & AGENTS ---

# Initialize Firestore Client (named database, set via FIRESTORE_DATABASE env var)
db = firestore.Client(project=PROJECT_ID, database=FIRESTORE_DATABASE)
logging.info(f"Initialized Firestore client for project: {PROJECT_ID}")


class CompatVertexAiSessionService(VertexAiSessionService):
    """
    Adapts VertexAiSessionService to the orchestrator's ADK 1.x-style session API.

    Bridges two gaps confirmed by introspection against the live engine:
      1. update_session() doesn't exist in ADK 2.0. The orchestrator mutates
         session.state then calls update_session(); we translate that into the
         canonical append_event() state-delta path (direct mutation does NOT
         persist on its own -- verified).
      2. delete_session() is keyword-only, but the orchestrator calls it
         positionally with just session_id (the temp A2A session). We record
         (app_name, user_id) at create time and resolve it on delete.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._coords: dict[str, tuple[str, str]] = {}

    async def create_session(self, *, app_name, user_id, state=None, session_id=None, **kwargs):
        session = await super().create_session(
            app_name=app_name, user_id=user_id, state=state, session_id=session_id, **kwargs
        )
        self._coords[session.id] = (app_name, user_id)
        return session

    async def update_session(self, session: Session) -> None:
        event = Event(
            author="orchestrator",
            invocation_id=str(uuid.uuid4()),
            actions=EventActions(state_delta=dict(session.state)),
        )
        await self.append_event(session, event)

    async def delete_session(self, session_id=None, *, app_name=None, user_id=None) -> None:
        if (app_name is None or user_id is None) and session_id in self._coords:
            app_name, user_id = self._coords[session_id]
        if app_name is None or user_id is None:
            logging.warning(f"delete_session: cannot resolve coords for {session_id}; skipping")
            return
        await super().delete_session(app_name=app_name, user_id=user_id, session_id=session_id)
        self._coords.pop(session_id, None)


# Initialize VertexAiSessionService (backed by the bare Agent Engine)
session_service = CompatVertexAiSessionService(
    project=PROJECT_ID,
    location=AGENT_ENGINE_LOCATION,
    agent_engine_id=AGENT_ENGINE_ID,
)
logging.info(f"Initialized VertexAiSessionService on engine {AGENT_ENGINE_ID}")

publisher = pubsub_v1.PublisherClient()
# Define Topic Paths
request_topic_path = publisher.topic_path(PROJECT_ID, IAM_TOPIC_ID)
approval_topic_path = publisher.topic_path(PROJECT_ID, APPROVALS_TOPIC_ID)

# Initialize the Orchestrator Agent with session service using factory method
logging.info(f"Initializing IAMOrchestrator for Project: {PROJECT_ID}")
orchestrator_agent = IAMOrchestrator.create(
    project_id=PROJECT_ID,
    provisioning_service_url=A2A_PROVISIONER_URL,
    gcp_location=GCP_LOCATION,
    rag_engine_id=RAG_ENGINE_ID,
    rag_data_store_id=RAG_DATA_STORE_ID,
    session_service=session_service,
    sender_email=SENDER_EMAIL,
    approval_callback_url=APPROVAL_CALLBACK_URL
)

# --- 4. Dashboard HTML (Entry Point) ---

NAV_BAR = """
<div style="margin-bottom: 20px; border-bottom: 1px solid #ddd; padding-bottom: 10px;">
    <a href="/" style="text-decoration: none; font-weight: bold; color: #1a73e8; margin-right: 20px;">New Request</a>
    <a href="/status" style="text-decoration: none; font-weight: bold; color: #1a73e8;">Live Status Dashboard</a>
</div>
"""

DASHBOARD_HTML = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Zero-Touch IAM Portal</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background-color: #f4f6f8; padding: 40px; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        h1 {{ color: #1a73e8; text-align: center; }}
        .form-group {{ margin-bottom: 20px; }}
        label {{ display: block; margin-bottom: 8px; font-weight: 600; color: #333; }}
        input, select, textarea {{ width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }}
        button {{ width: 100%; padding: 14px; background-color: #1a73e8; color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; transition: background 0.3s; }}
        button:hover {{ background-color: #1557b0; }}
        .note {{ font-size: 0.9em; color: #666; margin-top: 10px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        {NAV_BAR}
        <h1>IAM Access Request</h1>
        <form action="/manual_trigger" method="post">
            <div class="form-group">
                <label>User Email (Identity)</label>
                <input type="email" name="user_id" placeholder="employee@example.com" required>
            </div>
            <div class="form-group">
                <label>Requested Role</label>
                <input type="text" name="requested_role" placeholder="roles/storage.admin" required>
                <p class="note" style="text-align: left; margin-top: 5px;">Must match a '_id' in your Firestore configuration.</p>
            </div>
            <div class="form-group">
                <label>Target Project Scope</label>
                <input type="text" name="project_scope" placeholder="my-gcp-project-id" required>
            </div>
            <div class="form-group">
                <label>Business Justification</label>
                <textarea name="justification" rows="3" placeholder="Why is this access needed?" required></textarea>
            </div>
            <div class="form-group">
                <label>User Timezone</label>
                <input type="text" name="user_timezone" value="UTC">
            </div>
            <button type="submit">🚀 Queue Provisioning Request</button>
            <p class="note">This event will be published to the Cloud Pub/Sub queue.</p>
        </form>
    </div>
</body>
</html>
"""

# 5. HELPER FUNCTIONS FOR FIRESTORE PERSISTENCE ---

def persist_provisioning_request(session_id: str, user_id: str, requested_role: str, project_scope: str, status: str = "INITIATED"):
    """
    Persists a provisioning request to Firestore for audit trail and tracking.
    Returns the document reference.
    """
    try:
        request_data = {
            "session_id": session_id,
            "user_id": user_id,
            "requested_role": requested_role,
            "project_scope": project_scope,
            "status": status,
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
            "project_id": PROJECT_ID
        }

        # Use session_id as document ID for easy lookup
        doc_ref = db.collection(PROVISIONING_REQUESTS_COLLECTION).document(session_id)
        doc_ref.set(request_data)
        logging.info(f"Persisted provisioning request to Firestore: {session_id}")
    except Exception as e:
        logging.error(f"Failed to persist provisioning request: {e}")
        raise

def update_provisioning_request_status(session_id: str, status: str, result_data: dict = None):
    """
    Updates the status of a provisioning request in Firestore.
    Optionally adds result data for completed requests.
    """
    try:
        doc_ref = db.collection(PROVISIONING_REQUESTS_COLLECTION).document(session_id)
        update_data = {
            "status": status,
            "updated_at": datetime.datetime.now(datetime.timezone.utc)
        }

        if result_data:
            update_data["result"] = result_data
        doc_ref.update(update_data)
        logging.info(f"Updated provisioning request status: {session_id} -> {status}")
    except Exception as e:
        logging.error(f"Failed to update provisioning request status: {e}")
        raise

# --- 5. ROUTE HANDLERS ---

async def dashboard_handler(request: Request):
    """Serves the UI."""
    return HTMLResponse(DASHBOARD_HTML)

async def status_dashboard_handler(request: Request):
    """
    Renders the Live Status Dashboard by querying Firestore.
    """
    # Query last 25 requests, sorted by time
    try:
        docs = db.collection(PROVISIONING_REQUESTS_COLLECTION)\
                 .order_by("timestamp", direction=firestore.Query.DESCENDING)\
                 .limit(25)\
                 .stream()
        
        rows = ""
        for doc in docs:
            data = doc.to_dict()
            status = data.get("status", "UNKNOWN")
            
             # Badge Color Logic
            badge_class = "status-unknown"
            if "WAITING" in status: badge_class = "status-waiting"
            elif "DONE" in status or "APPLIED" in status or "APPROVED" in status: badge_class = "status-success"
            elif "FAILED" in status or "REJECTED" in status: badge_class = "status-failed"
            elif "QUEUED" in status or "PROCESSING" in status: badge_class = "status-queued"

            rows += f"""
            <tr>
                <td style="font-family: monospace; color: #5f6368; font-weight: 500;">...{data.get('session_id')[-6:]}</td>
                <td>{data.get('timestamp').strftime('%H:%M:%S') if data.get('timestamp') else 'N/A'}</td>
                <td>{data.get('user_id')}</td>
                <td>{data.get('requested_role')}</td>
                <td>{data.get('project_scope', 'N/A')}</td>
                <td><span class="status-badge {badge_class}">{status}</span></td>
            </tr>
            """
            
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Live Status | Zero-Touch IAM</title>
            <meta http-equiv="refresh" content="5"> 
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background-color: #f8f9fa; padding: 40px; }}
                /* Increased Width for better layout */
                .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 25px rgba(0,0,0,0.05); }}
                
                table {{ width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 25px; }}
                th {{ text-align: left; padding: 15px; border-bottom: 2px solid #eee; color: #5f6368; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; }}
                td {{ padding: 15px; border-bottom: 1px solid #f0f0f0; font-size: 0.95em; }}
                tr:last-child td {{ border-bottom: none; }}
                
                /* Badge Styles */
                .status-badge {{
                    padding: 6px 12px;
                    border-radius: 20px;
                    font-weight: 700;
                    font-size: 0.75em;
                    text-transform: uppercase;
                    display: inline-block;
                }}
                .status-success {{ background-color: #e6f4ea; color: #1e8e3e; }}
                .status-waiting {{ background-color: #fef7e0; color: #b06000; }}
                .status-failed  {{ background-color: #fce8e6; color: #c5221f; }}
                .status-queued  {{ background-color: #e8f0fe; color: #1967d2; }}
                .status-unknown {{ background-color: #f1f3f4; color: #3c4043; }}
                
                h2 {{ color: #202124; margin-bottom: 10px; }}
            </style>
        </head>
        <body>
            <div class="container">
                {NAV_BAR}
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h2>Live Operations Center</h2>
                    <span style="font-size: 0.8em; color: #9aa0a6;">Auto-refresh: 5s</span>
                </div>
                
                <table>
                    <thead>
                        <tr>
                            <th style="width: 100px;">Session ID</th>
                            <th>Time (UTC)</th>
                            <th>User</th>
                            <th>Role Requested</th>
                            <th>Scope</th>
                            <th>Current Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"Error loading dashboard: {e}", status_code=500)

async def manual_trigger_handler(request: Request):
    """
    Handles Form POST -> Publishes to Pub/Sub.
    Decouples UI from Execution.
    """
    form_data = await request.form()
    session_id = f"session-{uuid.uuid4()}"
    
    # 1. Construct Payload
    payload = {
        "session_id": session_id,
        "user_id": form_data.get("user_id"),
        "requested_role": form_data.get("requested_role"),
        "project_scope": form_data.get("project_scope"),
        "user_timezone": form_data.get("user_timezone", "UTC"),
        "trigger_source": "DASHBOARD_UI"
    }

    logging.info(f"🚀 Publishing Request to Pub/Sub: {session_id}")
    persist_provisioning_request(session_id, payload["user_id"], payload["requested_role"], payload["project_scope"], "QUEUED_PUBSUB")

    try:
        # 3. Publish Message
        data_str = json.dumps(payload)
        data = data_str.encode("utf-8")
        
        future = publisher.publish(request_topic_path, data)
        message_id = future.result() # Wait for publish confirmation
        
        logging.info(f"✅ Published message ID: {message_id}")
        
        # 4. Return Success UI
        return HTMLResponse(f"""
            <div style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1 style="color: green;">Request Queued Successfully ✅</h1>
                <p><strong>Session ID:</strong> {session_id}</p>
                <p><strong>Message ID:</strong> {message_id}</p>
                <p>The request has been sent to the event bus. The agent will pick it up shortly.</p>
                <hr>
                <p><small>({SENDER_EMAIL}) will send the approval request to the approvers.</small></p>
                <a href="/">Submit Another Request</a>
            </div>
        """)
    except Exception as e:
        logging.error(f"Pub/Sub Publish Error: {e}")
        return HTMLResponse(f"<h1>Publish Error</h1><p>{str(e)}</p>", status_code=500)

async def start_provisioning_endpoint(request: Request):
    """
    Handles Trigger Events.
    UPDATED: Now supports both Direct JSON AND Pub/Sub Push envelopes.
    """
    try:
        raw_body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    payload = {}

    # --- Detect Pub/Sub Envelope ---
    if "message" in raw_body and "data" in raw_body["message"]:
        try:
            b64_data = raw_body["message"]["data"]
            decoded_str = base64.b64decode(b64_data).decode("utf-8")
            payload = json.loads(decoded_str)
            logging.info("📩 Consumed Pub/Sub Event for Session: {payload.get('session_id')}")
        except Exception as e:
            logging.error(f"Pub/Sub decode failed: {e}")
            return JSONResponse({"error": "Bad Pub/Sub Payload"}, status_code=400)
    else:
        # Direct JSON (CLI/Postman)
        payload = raw_body

    # Extract & Validate
    session_id = payload.get("session_id", f"auto-{uuid.uuid4()}")
    user_id = payload.get("user_id")
    requested_role = payload.get("requested_role")
    project_scope = payload.get("project_scope")
    user_timezone = payload.get("user_timezone", 'UTC')

    if not all([user_id, requested_role, project_scope]):
        return JSONResponse({"error": "Missing required fields"}, status_code=400)

    # Update status to PROCESSING (it was QUEUED before)
    update_provisioning_request_status(session_id, "PROCESSING_AGENT_STARTED")

    try:
        result = await orchestrator_agent.start_provisioning(
            session_id=session_id, user_id=user_id, requested_role=requested_role,
            project_scope=project_scope, user_timezone=user_timezone
        )
        update_provisioning_request_status(session_id, result.get("status", "UNKNOWN"), result)
        return JSONResponse(result)
    except Exception as e:
        logging.error(f"Error: {e}")
        update_provisioning_request_status(session_id, "FAILED", {"error": str(e)})
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)

# --- PUB/SUB HANDLER FOR APPROVALS ---
async def process_approval_event(request: Request):
    """
    Consumer Endpoint (Pub/Sub Push) for APPROVALS.
    UPDATED: Fast NLU check, then background execution.
    """
    session_id = None  # Initialize for error handling
    approver_email = None  # Initialize for logging


    try:
        raw_body = await request.json()
        
        # Unwrap Pub/Sub Message
        if "message" in raw_body and "data" in raw_body["message"]:
            b64_data = raw_body["message"]["data"]
            decoded_str = base64.b64decode(b64_data).decode("utf-8")
            payload = json.loads(decoded_str)
        else:
            return JSONResponse({"error": "Not a Pub/Sub Message"}, status_code=400)
            
        session_id = payload.get("session_id")
        approver_email = payload.get("approver_email")
        raw_response_text = payload.get("raw_response_text")

        # Validate required fields
        if not all([session_id, approver_email, raw_response_text]):
            logging.error(f"Missing required fields in payload: {payload}")
            return JSONResponse(
                {"status": "rejected", "reason": "Missing required fields"}, 
                status_code=200  # ← Logical error, don't retry
            )
        
        logging.info(f"⚙️ FAST PATH: Processing approval for {session_id}")
        update_provisioning_request_status(session_id, "NLU_CLASSIFICATION_STARTED")

        # 🚀 STEP 1: Fast NLU Check (returns in <500ms)
        decision_result = await orchestrator_agent.check_nlu_and_decide(
            session_id=session_id,
            raw_response_text=raw_response_text, 
            approver_email=approver_email
        )
        
        decision_status = decision_result.get('status')
        
        # Handle rejection (quick path - no provisioning needed)
        if decision_status == "REJECTED":
            update_provisioning_request_status(session_id, f"REJECTED_{decision_result.get('reason', 'NLU')}")
            logging.info(f"✅ [{session_id}] Fast ACK: Request rejected by NLU")
            
            # Return 200 immediately (ACK to Pub/Sub)
            return JSONResponse({
                "status": "processed_fast", 
                "final_state": "REJECTED",
                "reason": decision_result.get('reason'),
                "session_id": session_id
            })
        
        # Handle errors in NLU
        if decision_status == "ERROR":
            update_provisioning_request_status(session_id, "NLU_ERROR")
            logging.error(f"❌ [{session_id}] NLU check failed")
            return JSONResponse({
                "status": "rejected",
                "reason": decision_result.get('reason'),
                "session_id": session_id
            }, status_code=200)  # Still ACK to prevent retry
        
        # 🚀 STEP 2: If approved, queue background provisioning
        if decision_status == "APPROVED":
            update_provisioning_request_status(session_id, "APPROVED_PROVISIONING_QUEUED")
            
            nlu_result = decision_result.get('nlu_result', {})

            # Fire-and-forget: Start background task
            import asyncio
            asyncio.create_task(
                _background_provisioning_wrapper(
                    orchestrator_agent,
                    session_id,
                    approver_email,
                    nlu_result
                )
            )
            
            logging.info(f"✅ [{session_id}] Fast ACK: Approved, provisioning queued in background")
            
            # Return 200 immediately (ACK to Pub/Sub - typically <500ms from start)
            return JSONResponse({
                "status": "processed_fast",
                "final_state": "APPROVED_PROVISIONING_IN_PROGRESS",
                "session_id": session_id,
                "message": "NLU approved, provisioning started in background"
            })

    except (ValueError, KeyError, json.JSONDecodeError) as e:
        # Logical errors - don't retry
        logging.error(f"Logical error processing approval: {e}", exc_info=True)
        
        if session_id:
            try:
                update_provisioning_request_status(
                    session_id, 
                    "FAILED_LOGICAL_ERROR", 
                    {"error": str(e), "error_type": type(e).__name__}
                )
            except Exception as update_error:
                logging.error(f"Failed to update status for session {session_id}: {update_error}")
        
        return JSONResponse({
            "status": "rejected", 
            "reason": f"Logical error: {str(e)}",
            "error_type": type(e).__name__,
            "session_id": session_id or "unknown"
        }, status_code=200)
    
    except (ConnectionError, TimeoutError) as e:
        # Transient errors - allow retry
        logging.warning(f"Transient error (will retry): {e}")
        
        if session_id:
            try:
                update_provisioning_request_status(
                    session_id, 
                    "RETRY_PENDING", 
                    {"error": str(e), "retry": True}
                )
            except Exception as update_error:
                logging.error(f"Failed to update status for session {session_id}: {update_error}")
        
        return JSONResponse({
            "status": "retry", 
            "reason": f"Transient error: {str(e)}",
            "session_id": session_id or "unknown"
        }, status_code=500)
    
    except Exception as e:
        # Unknown errors - log and don't retry
        logging.error(f"Unknown error in approval processing: {e}", exc_info=True)
        
        if session_id:
            try:
                update_provisioning_request_status(
                    session_id, 
                    "FAILED_UNKNOWN_ERROR", 
                    {"error": str(e), "error_type": type(e).__name__}
                )
            except Exception as update_error:
                logging.error(f"Failed to update status for session {session_id}: {update_error}")
        
        return JSONResponse({
            "status": "rejected", 
            "reason": f"Processing error: {str(e)}",
            "session_id": session_id or "unknown"
        }, status_code=200)

# Helper function for background execution with proper error handling
async def _background_provisioning_wrapper(
    orchestrator, 
    session_id: str, 
    approver_email: str, 
    nlu_result: Dict[str, Any]
):
    """
    Wrapper for background provisioning that handles errors and updates Firestore.
    """
    try:
        result = await orchestrator.execute_approved_provisioning(
            session_id=session_id,
            approver_email=approver_email,
            nlu_result=nlu_result
        )
        
        # Update final status in Firestore
        final_status = result.get('status', 'UNKNOWN')
        update_provisioning_request_status(session_id, final_status, result)
        
    except Exception as e:
        logging.error(f"❌ Background provisioning wrapper error for {session_id}: {e}", exc_info=True)
        update_provisioning_request_status(
            session_id, 
            "BACKGROUND_EXECUTION_FAILED",
            {"error": str(e), "error_type": type(e).__name__}
        )
    
async def process_approval_webhook(request: Request):
    """
    Handles Email Click -> Validates JWT -> PUBLISHES to Pub/Sub.
    Returns UI immediately.
    """
    token = request.query_params.get('token')

    session_id = None
    action = None
    approver_email = None

    if token:
        # --- JWT PATH ---
        try:
            secret = os.environ.get("JWT_SECRET")
            if not secret:
                return HTMLResponse("<h1>System Error</h1><p>JWT_SECRET not configured on server.</p>", status_code=500)
            
            # Decode & Verify
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            
            session_id = payload.get("sid")
            action = payload.get("act")
            approver_email = payload.get("sub")
            
            logging.info(f"🔐 JWT Verified: {action} by {approver_email}")
            
        except jwt.ExpiredSignatureError:
            return HTMLResponse("<h1>Link Expired</h1><p>This approval link is no longer valid.</p>", status_code=403)
        except jwt.InvalidTokenError as e:
            logging.warning(f"Invalid Token Attempt: {e}")
            return HTMLResponse("<h1>Security Check Failed</h1><p>Invalid authentication token.</p>", status_code=403)
        
        if not session_id or not action:
            return HTMLResponse("<h1>Error: Invalid Link</h1>", status_code=400)

    logging.info(f"WEBHOOK: Received {action} for session {session_id}")
    update_provisioning_request_status(session_id, "QUEUED_APPROVAL")

    # --- GENERATE SYNTHETIC TEXT FOR NLU ---
    # In a future version, this could come from a HTML text box or reply email body.
    if action == "APPROVED":
        simulated_text = "I have reviewed the policy justification and I explicitly APPROVE this access request."
    else:
        simulated_text = "I am REJECTING this request because it violates our internal freeze period."

    message_payload = {
        "session_id": session_id,
        "approver_email": approver_email,
        "raw_response_text": simulated_text,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    try:
        data_str = json.dumps(message_payload)
        future = publisher.publish(approval_topic_path, data_str.encode("utf-8"))
        msg_id = future.result()
        logging.info(f"✅ Approval queued to Pub/Sub: {msg_id}")
        
        # Return "Processing" Page
        return HTMLResponse(f"""
        <html>
            <head>
                <title>Processing Decision</title>
                <meta http-equiv="refresh" content="3;url={APPROVAL_CALLBACK_URL.replace('/respond', '/status')}">
            </head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1 style="color: #1a73e8;">Decision Received</h1>
                <p>Your decision has been securely queued for processing.</p>
                <p><strong>Action:</strong> {action}</p>
                <p><strong>Session:</strong> ...{session_id[-6:]}</p>
                <p style="color: #666;">Redirecting to status dashboard...</p>
            </body>
        </html>
        """)

    except Exception as e:
        logging.error(f"Webhook Error: {e}")
        return HTMLResponse(f"<h1>System Error</h1><p>{e}</p>", status_code=500)
    
async def emergency_stop_handler(request: Request):
    """Emergency Kill Switch."""
    logging.critical("🚨 EMERGENCY STOP TRIGGERED 🚨")
    # In production, this would Iterate active sessions -> Cancel them -> Call Provisioner to revoke JIT tokens
    return JSONResponse({"status": "SYSTEM_SUSPENDED", "action": "Revocation Queued"})

async def test_provision_handler(request: Request):
    """TEMPORARY -- validates the A2A provisioning chain without the email/approval
    round-trip (DWD pending). Seeds a session and calls execute_approved_provisioning
    directly: orchestrator -> A2A -> deployed provisioner -> real setIamPolicy
    (1-hour time-bound grant). REMOVE BEFORE DEMO."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = f"test-{uuid.uuid4()}"
    user_id = body.get("user_id", "testengineer@narmadanannaka.com")
    role = body.get("requested_role", "roles/storage.admin")
    scope = body.get("project_scope", "project-data-eng-479300")

    session = await orchestrator_agent.session_service.create_session(
        session_id=session_id, app_name=orchestrator_agent.name, user_id="system"
    )
    session.state["request"] = {"user_id": user_id, "role": role, "scope": scope, "user_timezone": "UTC"}
    session.state["lookup_result"] = {"role_id": role, "user_id": user_id, "gcp_project_scope": scope}
    await orchestrator_agent.session_service.update_session(session)

    result = await orchestrator_agent.execute_approved_provisioning(
        session_id=session_id,
        approver_email="test-approver@example.com",
        nlu_result={"status": "APPROVED", "reason_summary": "Temp test bypass"},
    )
    return JSONResponse(result)

# Create Starlette app with routes
app = Starlette(
    routes=[
        Route('/', dashboard_handler, methods=['GET']),  # Dashboard
        Route('/status', status_dashboard_handler, methods=['GET']), # Status
        Route('/manual_trigger', manual_trigger_handler, methods=['POST']), # Form Handler
        Route('/start_provisioning', start_provisioning_endpoint, methods=['POST']), # Updated Trigger
        Route('/process_approval_event', process_approval_event, methods=['POST']), 
        Route('/respond', process_approval_webhook, methods=['GET']),
        Route('/emergency-stop', emergency_stop_handler, methods=['POST']), # Safety
        Route('/test_provision', test_provision_handler, methods=['POST']),  # TEMPORARY: remove before demo
    ]
)

logging.info(f"Orchestrator HTTP Server configured on {HOST}:{PORT}")

# The uvicorn CMD command in the Dockerfile will now run this 'app' object.