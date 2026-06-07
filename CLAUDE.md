# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Zero-Touch IAM Security Multi-Agent System**: An AI-assisted Identity and Access Management (IAM) provisioning platform built with Google's Agent Development Kit (ADK). Demonstrates multi-agent orchestration, policy-aware decision making, human approval workflows, and secure privilege elevation for GCP IAM operations.

**Key Innovation**: End-to-end IAM provisioning flow combining LLM agents, RAG-grounded policy retrieval, NLU-based approval classification, and A2A (Agent-to-Agent) security isolation.

## Architecture at a Glance

### Two-Service Model (Security by Isolation)

Orchestrator Service (Low Privilege) coordinates workflow via authenticated A2A protocol to isolated Provisioning Service (High Privilege). Orchestrator has NO direct IAM write permissions.

### Nine-Step Provisioning Workflow

1. Session Init → Firestore persistence
2. Approver Lookup → Query role_approvals database
3. Policy Retrieval → RAG query (Vertex AI Search)
4. Compliance Check → Temporal guardrails (business hours, time zones)
5. Communication → Send HTML email with JWT-signed approval buttons
6. Response Collection → Pub/Sub async collection of approvals
7. NLU Classification → Gemini 2.5 Flash structured JSON parsing
8. A2A Execution → RemoteA2aAgent calls provisioner service
9. Audit Trail → Narrative generation + Firestore persistence

## Core Components & Responsibilities

### Entry Point: app.py (Orchestrator Service)
- HTTP Endpoints: /start_provisioning, /respond, /process_approval_event, /status dashboard
- Session Service: Custom FirestoreSessionService wrapping ADK's BaseSessionService
- Pub/Sub Integration: Handles both direct JSON and Pub/Sub envelope formats
- JWT Link Handling: Validates signed approval links, publishes to approvals topic
- Environment Variables: PROJECT_ID, A2A_PROVISIONER_URL, RAG_ENGINE_ID, RAG_DATA_STORE_ID, SENDER_EMAIL, JWT_SECRET, APPROVAL_CALLBACK_URL

### Agents: agents/ Directory

| Agent | File | Purpose |
|-------|------|---------|
| IAMOrchestrator | orchestrator.py | Master coordinator; owns RemoteA2aAgent, instantiates all sub-agents |
| ApproverLookupAgent | lookup_agent.py | Firestore query: role_approvals collection (database: "approver-store") |
| PolicyContextAgent | context_agent.py | Dual-tool agent: RAG retrieval (Vertex AI Search) + temporal compliance checks |
| NLUClassifierAgent | nlu_classifier_agent.py | Gemini 2.5 Flash (asia-southeast1) with structured JSON output |
| CommunicationAgent | communication_agent.py | Gmail API via Domain-Wide Delegation; generates JWT links for email buttons |
| IAMProvisioningAgent | provisioning_service/provisioning_agent.py | Executes real setIamPolicy calls with time-bound conditions |

### Provisioning Service: provisioning_service/app.py
- A2A Server: Exposes IAMProvisioningAgent via standardized A2A protocol
- ServiceAccountAuthMiddleware: ID token verification; restricts to ALLOWED_CALLER_SA
- Endpoints: /.well-known/agent.json (agent card), /tasks/send (protected)
- Environment Variables: SERVICE_URL, ALLOWED_CALLER_SA

## Data Models & Firestore Collections

- adk-sessions-store (separate database) → adk-sessions collection: Session state management
- (default) Firestore database → provisioning-requests collection: Audit trail (INITIATED→APPROVED→POLICY_APPLIED/FAILED)
- approver-store (separate database) → role_approvals collection: Role/project approval requirements (query: role_id==X AND gcp_project_scope==Y)

## Key Patterns & Implementation Notes

### ADK Multi-Tool Agents
Agents are LlmAgent subclasses with FunctionTool instances. Tools are defined via closures to capture instance state to avoid Pydantic initialization conflicts.

### Pydantic Initialization Workaround
Use self.__dict__['attr'] = value after super().__init__() to assign attributes without triggering Pydantic validation.

### RAG Integration
Service: Google Cloud Discovery Engine (Vertex AI Search)
Implementation: agents/context_agent.py → _retrieve_policy_text()
Returns: Extractive segments + snippets + server-side summaries
Filter Syntax: policy_id: ANY("doc_type")
Status: ✅ Fully implemented; not simulated

### Compliance Guardrails
Implementation: agents/context_agent.py → _check_temporal_compliance()
Active Blocking: Returns FAILED if outside business hours (Mon-Fri, 08:00-17:00)
Timezone-Aware: Uses pytz; user_timezone passed from request

### JWT-Signed Email Links
Purpose: Tamper-proof approval links in email
Payload: session_id (sid), action (act), approver_email (sub), iat, exp (7 days)
Implementation: agents/communication_agent.py → _generate_secure_link()
Validation: app.py → /respond endpoint decodes and validates JWT
Secret: JWT_SECRET environment variable (required for production)

### A2A Authentication
Client Side: orchestrator.py creates GCPAuth httpx middleware to fetch OIDC tokens
Server Side: provisioning_service/app.py verifies ID token audience and caller identity
Middleware: ServiceAccountAuthMiddleware validates caller against ALLOWED_CALLER_SA
Audit: Caller email injected into audit trail

### Async Email Sending
Implementation: CommunicationAgent.send_approval_email() uses asyncio + thread pool
Auth Method: Service Account with Domain-Wide Delegation (recommended)
Scopes: https://www.googleapis.com/auth/gmail.send

### Pub/Sub Integration
Request Topic: iam-request-topic (push → /start_provisioning)
Approval Topic: iam-approvals-topic (push → /process_approval_event)
Envelope Handling: Auto base64 decode + unwrap message wrapper
Retry: Transient errors (503, timeouts) vs logical errors (400, 401)

### Real IAM Execution
Implementation: provisioning_agent.py → _perform_iam_set()
Client: google.cloud.resourcemanager_v3.ProjectsClient
Policy Version: v3 (required for time-bound conditions)
Conditions: 1-hour expiration using CEL syntax
Supported Roles: Primitive (viewer/editor/owner) + custom roles

## Common Development Commands

### Prerequisites
- Python 3.11+
- GCP Project with enabled APIs: Cloud Run, Firestore, Vertex AI, Discovery Engine, IAM
- Service account keys for both orchestrator and provisioner services

### Local Development

Install dependencies (root directory for orchestrator):
`
pip install -r requirements.txt
`

Validate Python syntax:
`
python -m py_compile app.py agents/*.py provisioning_service/*.py
`

Run orchestrator locally (requires GCP credentials):
`
export PROJECT_ID="your-project-id"
export A2A_PROVISIONER_URL="https://your-provisioner-url"
export RAG_ENGINE_ID="your-engine-id"
export RAG_DATA_STORE_ID="your-datastore-id"
export SENDER_EMAIL="bot@your-domain.com"
export JWT_SECRET="your-secret"
export APPROVAL_CALLBACK_URL="http://localhost:8080"
uvicorn app:app --reload
`

Run provisioning service locally:
`
cd provisioning_service
export SERVICE_URL="http://localhost:8081"
export ALLOWED_CALLER_SA="orchestrator-sa@project.iam.gserviceaccount.com"
uvicorn app:app --port 8081 --reload
`

### Deployment

Build and push Docker images:
`
docker build -t gcr.io/PROJECT/iam-orchestrator:latest .
docker push gcr.io/PROJECT/iam-orchestrator:latest

cd provisioning_service
docker build -t gcr.io/PROJECT/iam-provisioner:latest .
docker push gcr.io/PROJECT/iam-provisioner:latest
`

Deploy orchestrator to Cloud Run:
`
gcloud run deploy iam-orchestrator \
  --image gcr.io/PROJECT/iam-orchestrator:latest \
  --region us-central1 \
  --service-account orchestrator-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars \
    PROJECT_ID=YOUR_PROJECT,\
    A2A_PROVISIONER_URL=https://iam-provisioner-XXX.run.app,\
    RAG_ENGINE_ID=YOUR_ENGINE_ID,\
    RAG_DATA_STORE_ID=YOUR_DATASTORE_ID,\
    JWT_SECRET=YOUR_SECRET,\
    SENDER_EMAIL=bot@your-domain.com,\
    APPROVAL_CALLBACK_URL=https://iam-orchestrator-XXX.run.app
`

Deploy provisioning service to Cloud Run:
`
cd provisioning_service
gcloud run deploy iam-provisioner \
  --image gcr.io/PROJECT/iam-provisioner:latest \
  --region us-central1 \
  --service-account provisioner-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars \
    SERVICE_URL=https://iam-provisioner-XXX.run.app,\
    ALLOWED_CALLER_SA=orchestrator-sa@PROJECT.iam.gserviceaccount.com
`

Configure Pub/Sub subscriptions:
`
gcloud pubsub subscriptions create iam-requests-sub \
  --topic=iam-request-topic \
  --push-endpoint=https://iam-orchestrator-XXX.run.app/start_provisioning \
  --ack-deadline=600

gcloud pubsub subscriptions create iam-approvals-sub \
  --topic=iam-approvals-topic \
  --push-endpoint=https://iam-orchestrator-XXX.run.app/process_approval_event \
  --ack-deadline=300
`

## Testing & Validation

### Manual End-to-End Test
1. Navigate to https://orchestrator-url/ (web UI)
2. Submit access request form with user email, role, project scope, timezone
3. Check Firestore provisioning-requests collection for session creation
4. Check email inbox for approval request (if SENDER_EMAIL configured)
5. Click APPROVE/DENY button in email (validates JWT signature)
6. Monitor /status dashboard for real-time workflow progress
7. Verify final status in provisioning-requests collection

### Debugging Checklist
- Session Not Created: Check Firestore adk-sessions collection and provisioning-requests audit trail
- Email Not Sent: Verify SENDER_EMAIL, SA_KEY_PATH, Domain-Wide Delegation in Google Workspace Admin
- JWT Link Invalid: Check JWT_SECRET matches between email generation and /respond validation
- A2A Call Failed: Check SERVICE_URL is reachable, ALLOWED_CALLER_SA matches orchestrator service account
- RAG Query Fails: Verify RAG_ENGINE_ID and RAG_DATA_STORE_ID exist in Vertex AI Search
- NLU Classification Fails: Check asia-southeast1 region has Gemini 2.5 Flash access

## Critical Files & Modifications

### High-Risk Areas (Security-Sensitive)
- provisioning_agent.py: Contains real IAM policy execution; changes require audit log review
- provisioning_service/app.py: Authentication middleware; token validation is critical
- agents/communication_agent.py: Email sending with JWT links; link generation algorithm must not change

### Important Configuration Files
- iam_custom_role.yaml: Custom IAM role for orchestrator service account
- provisioner_role.yaml: Custom IAM role for provisioner service account

### Logging & Observability
All agents emit structured logs with: session_id, agent_name, phase, status, timestamp. Firestore provisioning-requests collection is the source of truth for audit trails.

## Known Limitations & Future Work

- JIT Elevation: Currently simulated (5% failure rate); production integration requires PAM system
- Multi-Approver Logic: AND/OR workflows not yet supported; single approver per role
- Rate Limiting: No built-in request throttling or abuse detection
- Emergency Stop: HTTP endpoint exists but logic not fully implemented

## External Dependencies

### Google Cloud APIs
- Cloud Run: Service deployment
- Firestore: Session + audit storage
- Vertex AI: Gemini 2.5 Flash (NLU), SDK
- Vertex AI Search (Discovery Engine): RAG policy retrieval
- Cloud Resource Manager: IAM policy patching
- Cloud Pub/Sub: Async event routing
- Gmail API: Email delivery (requires workspace + domain-wide delegation)

### Python Packages
- google-adk[a2a]: ADK framework with A2A protocol
- google-cloud-*: GCP client libraries
- uvicorn[standard], starlette: ASGI server + framework
- pytz: Timezone-aware compliance checks
- google-api-python-client: Gmail API
- pyjwt: JWT token signing/validation
- pydantic: Response schema validation

---

**Last Updated**: June 2026
**Status**: Hackathon Prototype (60% Production-Ready)
**Reference Documentation**: See README.md for full details on architecture, deployment, threat model, and monitoring.
