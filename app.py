# app.py (Entry point for Orchestrator Cloud Run Service)

import os
import logging
import datetime
from google.cloud import firestore
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse, HTMLResponse
from agents.orchestrator import IAMOrchestrator
from google.adk.sessions import Session, BaseSessionService
from typing import Optional

# --- 1. CONFIGURATION ---
logging.basicConfig(level=logging.INFO)

# Retrieve configuration from environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
# CRITICAL: This is the URL we injected during the deploy command!
A2A_PROVISIONER_URL = os.environ.get("A2A_PROVISIONER_URL")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
RAG_ENGINE_ID = os.environ.get("RAG_ENGINE_ID")
RAG_DATA_STORE_ID = os.environ.get("RAG_DATA_STORE_ID")

# --- Communication Config ---
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") 
APPROVAL_CALLBACK_URL = os.environ.get("APPROVAL_CALLBACK_URL", "http://localhost:8080/approve")

if not PROJECT_ID or not A2A_PROVISIONER_URL:
    logging.error("Missing required environment variables (PROJECT_ID or A2A_PROVISIONER_URL).")
    raise EnvironmentError("Deployment environment variables are not set correctly.")

if not RAG_ENGINE_ID or not RAG_DATA_STORE_ID:
    logging.error("Missing required RAG configuration (RAG_ENGINE_ID or RAG_DATA_STORE_ID).")
    raise EnvironmentError("RAG configuration environment variables are not set correctly.")

if not SENDER_EMAIL:
    logging.warning("SENDER_EMAIL not set. Communication Agent will run in simulation mode (no real emails sent).")

# Normalize to https and strip trailing slash
if A2A_PROVISIONER_URL.startswith("http://"):
    logging.warning("A2A_PROVISIONER_URL is http, normalizing to https for Cloud Run")
    A2A_PROVISIONER_URL = "https://" + A2A_PROVISIONER_URL.split("://",1)[1]
A2A_PROVISIONER_URL = A2A_PROVISIONER_URL.rstrip("/")

ADK_SESSION_DB = "adk-sessions-store" #database for provisioning requests session
PROVISIONING_REQUESTS_COLLECTION = "provisioning-requests" #dedicated collection for audit trail

# Get port from environment (Cloud Run sets this)
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

# --- CUSTOM FIRESTORE SESSION SERVICE ---

class FirestoreSessionService(BaseSessionService):
    """
    Custom Firestore-backed session service for ADK agents.
    Implements persistent session storage using Firestore.
    """

    def __init__(self, firestore_client: firestore.Client, collection_name: str = "adk-sessions"):
        """
        Initialize the Firestore session service.

        Args:
            firestore_client: Initialized Firestore client
            collection_name: Name of the Firestore collection to store sessions
        """
        self.db = firestore_client
        self.collection_name = collection_name
        logging.info(f"FirestoreSessionService initialized with collection: {collection_name}")

    async def create_session(self, session_id: str, app_name: str, user_id: str, **kwargs) -> Session:
        """
        Create a new session and persist it to Firestore.

        Args:
            session_id: Unique identifier for the session
            app_name: Name of the application/agent creating the session
            user_id: User identifier for the session

        Returns:
            Session object
        """
        session = Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=kwargs.get('state', {})
        )

        # Persist to Firestore
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc_ref.set({
            "session_id": session_id,
            "app_name": app_name,
            "user_id": user_id,
            "state": session.state,
            "created_at": datetime.datetime.now(datetime.timezone.utc),
            "updated_at": datetime.datetime.now(datetime.timezone.utc)
        })

        logging.info(f"Created and persisted session: {session_id}")
        return session

    async def get_session(self, *, app_name: str, user_id: str, session_id: str, config=None) -> Optional[Session]:
        """
        Retrieve a session from Firestore.

        Args:
            app_name: Application name (for ADK compatibility)
            user_id: User ID (for ADK compatibility)
            session_id: Unique identifier for the session
            config: Optional GetSessionConfig for filtering events

        Returns:
            Session object if found, None otherwise
        """
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc = doc_ref.get()

        if not doc.exists:
            logging.warning(f"Session not found: {session_id}")
            return None

        data = doc.to_dict()
        session = Session(
            id=data["session_id"],
            app_name=data["app_name"],
            user_id=data["user_id"],
            state=data.get("state", {})
        )

        logging.info(f"Retrieved session: {session_id} (app: {app_name}, user: {user_id})")
        return session

    async def update_session(self, session: Session) -> None:
        """
        Update an existing session in Firestore.

        Args:
            session: Session object to update
        """
        doc_ref = self.db.collection(self.collection_name).document(session.id)
        doc_ref.update({
            "state": session.state,
            "updated_at": datetime.datetime.now(datetime.timezone.utc)
        })

        logging.info(f"Updated session: {session.id}")

    async def delete_session(self, session_id: str) -> None:
        """
        Delete a session from Firestore.

        Args:
            session_id: Unique identifier for the session
        """
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc_ref.delete()

        logging.info(f"Deleted session: {session_id}")

    async def list_sessions(self, user_id: Optional[str] = None) -> list[Session]:
        """
        List all sessions, optionally filtered by user_id.

        Args:
            user_id: Optional user identifier to filter sessions

        Returns:
            List of Session objects
        """
        collection_ref = self.db.collection(self.collection_name)

        # Filter by user_id if provided
        if user_id:
            query = collection_ref.where("user_id", "==", user_id)
            docs = query.stream()
        else:
            docs = collection_ref.stream()

        sessions = []
        for doc in docs:
            data = doc.to_dict()
            session = Session(
                id=data["session_id"],
                app_name=data["app_name"],
                user_id=data["user_id"],
                state=data.get("state", {})
            )
            sessions.append(session)

        logging.info(f"Listed {len(sessions)} session(s)" + (f" for user {user_id}" if user_id else ""))
        return sessions

# --- 3. INITIALIZE FIRESTORE & AGENTS ---

# Initialize Firestore Client for session storage
db = firestore.Client(project=PROJECT_ID)
logging.info(f"Initialized Firestore client for project: {PROJECT_ID}")

# Initialize custom Firestore Session Service for ADK sessions
session_service = FirestoreSessionService(
    firestore_client=db,
    collection_name=ADK_SESSION_DB
)
logging.info(f"Initialized FirestoreSessionService with collection: {ADK_SESSION_DB}")

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

# --- 4. HELPER FUNCTIONS FOR FIRESTORE PERSISTENCE ---

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
        return doc_ref
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
            "updated_at": datetime.datetime.utcnow()
        }

        if result_data:
            update_data["result"] = result_data

        doc_ref.update(update_data)
        logging.info(f"Updated provisioning request status: {session_id} -> {status}")
    except Exception as e:
        logging.error(f"Failed to update provisioning request status: {e}")
        raise

# --- 5. CREATE HTTP SERVER WITH SESSION ENDPOINTS ---

async def start_provisioning_endpoint(request):
    """HTTP endpoint to initiate IAM provisioning"""
    data = await request.json()
    session_id = data.get("session_id")
    user_id = data.get("user_id")
    requested_role = data.get("requested_role")
    project_scope = data.get("project_scope")
    user_timezone = data.get("user_timezone", 'UTC')

    persist_provisioning_request(session_id, user_id, requested_role, project_scope, "INITIATED")

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

async def process_approval_webhook(request):
    """
    Handles the click from the email.
    Generates synthetic text to feed the NLU Agent.
    """
    session_id = request.query_params.get('session_id')
    action = request.query_params.get('action') # APPROVED or DENIED
    
    if not session_id or not action:
        return HTMLResponse("<h1>Error: Invalid Link</h1>", status_code=400)

    logging.info(f"WEBHOOK: Received {action} for session {session_id}")
    update_provisioning_request_status(session_id, f"HUMAN_CLICKED_{action}")

    # --- GENERATE SYNTHETIC TEXT FOR NLU ---
    # In a future version, this could come from a HTML text box.
    if action == "APPROVED":
        simulated_text = "I have reviewed the policy justification and I explicitly APPROVE this access request."
    else:
        simulated_text = "I am REJECTING this request because it violates our internal freeze period."
    
    # We assume the approver is the Admin (simplified for prototype)
    # In prod, we'd verify the token to know exactly who clicked it.
    approver_identity = SENDER_EMAIL 

    # Execute the provisioning workflow
    try:
        # Resume the Orchestrator with TEXT, not just a flag
        result = await orchestrator_agent.resume_with_approval(
            session_id=session_id,
            raw_response_text=simulated_text, 
            approver_email=approver_identity
        )
        
        status = result.get('status')
        color = "green" if "APPLIED" in str(status) else "red"
        
        html_content = f"""
        <html>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1 style="color: {color};">Decision Processed</h1>
                <p>The NLU Agent has analyzed your input.</p>
                <p><strong>Input:</strong> "{simulated_text}"</p>
                <p><strong>NLU Classification:</strong> {status}</p>
                <p>You can close this window.</p>
            </body>
        </html>
        """
        return HTMLResponse(html_content)

    except Exception as e:
        logging.error(f"Webhook Error: {e}")
        return HTMLResponse(f"<h1>System Error</h1><p>{e}</p>", status_code=500)

# Create Starlette app with routes
app = Starlette(
    routes=[
        Route('/start_provisioning', start_provisioning_endpoint, methods=['POST']),
        Route('/respond', process_approval_webhook, methods=['GET']),
    ]
)

logging.info(f"Orchestrator HTTP Server configured on {HOST}:{PORT}")

# The uvicorn CMD command in the Dockerfile will now run this 'app' object.