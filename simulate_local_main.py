# main.py - FINAL FIX

import os
from agents.orchestrator import IAMOrchestrator
import uuid
import logging
from google.adk.sessions import InMemorySessionService, CloudFirestoreSessionService 
# NEW IMPORT: Add asyncio to run the async workflow
import asyncio 

# Configuration: Ensure PROJECT_ID is set in your environment
PROJECT_ID = os.environ.get("PROJECT_ID", "zero-touch-iam-agent") 
ADK_SESSION_DB = "adk-sessions-store" #database for provisioning requests session

# NEW ASYNC WRAPPER FUNCTION
async def run_simulation(orchestrator, session_id, USER_ID, REQUESTED_ROLE, PROJECT_SCOPE):
    # CRITICAL FIX 5: Await the orchestrator's start method
    return await orchestrator.start_provisioning(session_id, USER_ID, REQUESTED_ROLE, PROJECT_SCOPE)


if __name__ == "__main__":
    
    logging.basicConfig(level=logging.INFO)

    # 1. Initialize the Session Service
    # local_session_service = InMemorySessionService()

    # 1. Switch from local to persistent Firestore service
    persistent_session_service = CloudFirestoreSessionService(database=ADK_SESSION_DB)

    # 2. Initialize the Orchestrator (WITHOUT the forbidden keyword argument)
    orchestrator = IAMOrchestrator(PROJECT_ID)

    # 3. MANUAL INJECTION
    #object.__setattr__(orchestrator, 'session_service', local_session_service)
    object.__setattr__(orchestrator, 'session_service', persistent_session_service)
    
    # --- SIMULATION INPUTS ---
    session_id = str(uuid.uuid4())
    USER_ID = "new-security-analyst@corp.com"
    REQUESTED_ROLE = "roles/iam.serviceAccountKeyAdmin" 
    PROJECT_SCOPE = "project-data-eng"
    # -------------------------

    print("\n" + "=" * 50)
    print(f"SIMULATING WORKFLOW START | SESSION ID: {session_id}")
    print("=" * 50)

    try:
        # CRITICAL FIX 6: Run the async wrapper using asyncio.run
        final_state = asyncio.run(run_simulation(orchestrator, session_id, USER_ID, REQUESTED_ROLE, PROJECT_SCOPE))
        
        print("\n--- FINAL LOOKUP RESULT ---")
        import json
        print(json.dumps(final_state, indent=4))
        
    except Exception as e:
        print(f"\nFATAL ERROR during simulation: {e}")