# Zero-Touch IAM Provisioning Multi-Agent System

## Overview

A production-grade, AI-powered IAM (Identity and Access Management) provisioning system built using Google's Agent Development Kit (ADK). This system automates secure, policy-compliant IAM role assignments through an intelligent multi-agent architecture with human-in-the-loop approval workflows.

## What Has Been Built

This project implements an **autonomous IAM provisioning orchestration system** that combines:

- **Multi-agent AI orchestration** using Google ADK
- **Agent-to-Agent (A2A) security isolation** for privileged operations
- **Policy-aware decision making** with RAG (Retrieval Augmented Generation)
- **Natural Language Understanding** for email-based approval classification
- **Temporal compliance guardrails** for time-restricted access
- **Comprehensive audit trails** for regulatory compliance

### Key Innovation

The system demonstrates **Zero-Touch IAM Provisioning** - where access requests flow through automated policy checks, human approvals, and privilege elevation without manual intervention, while maintaining enterprise-grade security and auditability.

---

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR SERVICE                         │
│                    (Cloud Run - Low Privilege)                  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          IAMOrchestrator (Main Agent)                    │  │
│  │                                                          │  │
│  │  Sub-Agents:                                            │  │
│  │  ├─ ApproverLookupAgent (Firestore)                    │  │
│  │  ├─ PolicyContextAgent (RAG + Compliance)              │  │
│  │  ├─ NLUClassifierAgent (Gemini)                        │  │
│  │  └─ RemoteA2aAgent (Provisioning Client)              │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ A2A Protocol (Isolated Communication)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│               PROVISIONING SERVICE                              │
│               (Cloud Run - High Privilege + JIT)                │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │     IAMProvisioningAgent                                 │  │
│  │     └─ execute_iam_set_tool (IAM API + PAM)            │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Architecture Principles

1. **Security Isolation**: Orchestrator (low privilege) and Provisioner (high privilege) run in separate Cloud Run services
2. **A2A Protocol**: Agents communicate via standardized Agent-to-Agent protocol, preventing direct privilege escalation
3. **Session Persistence**: Firestore-backed session management enables async workflows
4. **Policy Grounding**: RAG retrieval ensures decisions are grounded in organizational policies
5. **Audit-First Design**: Every decision point generates structured audit logs

---

## System Components

### 1. Orchestrator Service (`app.py`)

**Purpose**: Main entry point and workflow coordinator

**Key Features**:
- HTTP endpoint (`/start_provisioning`) for initiating access requests
- Firestore-backed session service for persistent state management
- Coordinates the 9-step provisioning workflow
- Maintains audit trail in `provisioning-requests` collection
- ✅ **NEW**: Integrated Gmail OAuth token support via `token.json`
- ✅ **NEW**: Automatic fallback to simulation mode if email not configured

**Environment Variables**:
- `PROJECT_ID`: GCP project identifier
- `A2A_PROVISIONER_URL`: URL of the isolated provisioning service
- `GCP_LOCATION`: Region for Vertex AI services (default: global)
- `RAG_ENGINE_ID`: Vertex AI Search engine ID
- `RAG_DATA_STORE_ID`: Data store ID for policy documents
- `SENDER_EMAIL`: Gmail address for sending approval emails (optional)
- `APPROVAL_CALLBACK_URL`: URL for manual approval callback (optional)
- `GMAIL_TOKEN_PATH`: Path to Gmail OAuth token file (default: token.json)
- `PORT`: HTTP server port (default: 8080)

### 2. IAM Orchestrator Agent (`agents/orchestrator.py`)

**Purpose**: Master agent that orchestrates the entire workflow

**9-Step Workflow**:

1. **Session Initialization**: Create persistent session in Firestore
2. **Approver Lookup**: Query Firestore for required human approvers
3. **Policy Retrieval**: Use RAG to retrieve relevant policy documents
4. **Compliance Check**: Enforce temporal/contextual guardrails
5. **Communication** (simulated): Send approval request to humans
6. **Response Collection** (simulated): Wait for email response
7. **NLU Classification**: Classify approval/denial using Gemini
8. **A2A Execution**: Delegate to isolated provisioning agent
9. **Audit Trail**: Generate comprehensive audit narrative

**Sub-Agents Managed**:
- `ApproverLookupAgent`
- `PolicyContextAgent`
- `NLUClassifierAgent`
- `CommunicationAgent` (email helper)
- `RemoteA2aAgent` (provisioning client)

### 3. Approver Lookup Agent (`agents/lookup_agent.py`)

**Purpose**: Retrieves approval requirements from Firestore

**Data Source**:
- Firestore database: `approver-store`
- Collection: `role_approvals`

**Query Pattern**:
```python
WHERE role_id == <requested_role>
  AND gcp_project_scope == <target_project>
```

**Returns**:
- List of required approvers
- Policy document reference
- Role metadata

### 4. Policy Context Agent (`agents/context_agent.py`)

**Purpose**: RAG-powered policy retrieval and compliance enforcement

**Features**:

a) **RAG Retrieval** (`_retrieve_policy_text`) - ✅ **FULLY IMPLEMENTED**:
   - Queries Vertex AI Search with policy document ID
   - Extracts extractive segments and snippets from Discovery Engine
   - Returns policy context with source links
   - Real integration with Google Cloud Discovery Engine API
   - Handles both structured and unstructured policy documents

b) **Constraint Detection** (`_detect_constraint_type`) - ✅ **FULLY IMPLEMENTED**:
   - Analyzes policy text for constraint types using regex patterns:
     - `temporal_access_only`: Time-restricted access
     - `guardrail_deny`: Explicitly forbidden
     - `none`: Standard access
   - Production-ready pattern matching

c) **Temporal Compliance Check** (`_check_temporal_compliance`) - ✅ **FULLY IMPLEMENTED**:
   - Validates access against current time
   - Enforces business hours (Mon-Fri, 08:00-17:00)
   - Timezone-aware checking using pytz
   - **Active Guardrail**: Blocks non-compliant requests immediately
   - Returns detailed compliance reasons for audit trail

**Configuration**:
- ✅ Uses Vertex AI Discovery Engine for RAG (real implementation)
- ✅ Supports extractive content and snippets
- Configurable via `RAG_ENGINE_ID` and `RAG_DATA_STORE_ID`

### 5. NLU Classifier Agent (`agents/nlu_classifier_agent.py`)

**Purpose**: Classifies human email responses using Gemini

**Model**: `gemini-2.5-flash` via Vertex AI SDK (PRODUCTION-GRADE)

**Input**:
- `email_body`: Raw email text from approver
- `sender_email`: Approver's email address

**Output** (Structured JSON):
```json
{
  "status": "APPROVED | DENIED | REJECTED_CONTEXT_MISSING",
  "approver_id": "approver@example.com",
  "reason_summary": "1-2 sentence summary of reason"
}
```

**Features**:
- ✅ **FULLY IMPLEMENTED** - Uses real Gemini 2.5 Flash via Vertex AI SDK
- Enforces strict JSON schema via `GenerationConfig`
- System instruction for impartial classification
- Exponential backoff retry logic (3 attempts)
- Fallback to `REJECTED_CONTEXT_MISSING` on failure

**Region**: `asia-southeast1`

### 6. Communication Agent (`agents/communication_agent.py`)

**Purpose**: ✅ **FULLY IMPLEMENTED** - Sends approval request emails via Gmail API

**Authentication Methods**:
1. **OAuth User Credentials** (Personal Gmail):
   - Reads from `token.json` file (generated via `generate_gmail_token.py`)
   - Supports personal @gmail.com accounts
   - Includes refresh token for long-term operation

2. **Default Credentials** (Enterprise):
   - Fallback to service account credentials
   - For Google Workspace domains

**Key Features**:
- ✅ Real Gmail API integration using `google-api-python-client`
- ✅ Async email sending with asyncio thread pool executor
- ✅ Automatic fallback to simulation mode if credentials missing
- ✅ Structured email templates with justification context
- ✅ Returns message metadata (message_id, thread_id) for tracking

**Input Parameters**:
- `session_id`: Request correlation ID
- `requester_email`: User requesting access
- `requested_role`: IAM role being requested
- `project_scope`: Target GCP project
- `approvers`: List of approver email addresses
- `justification`: Policy context for approval decision

**Output**:
```python
{
  "message_id": "gmail-message-id",
  "thread_id": "gmail-thread-id",
  "provider": "gmail" | "simulated"
}
```

**Email Template Structure**:
- Subject: `[ACTION REQUIRED] Access request for {requester_email}`
- Body includes: session ID, requested role, project scope, justification text
- Instructions for approvers to reply with APPROVED/DENIED
- Optional callback URL for web-based approval

**Error Handling**:
- Graceful fallback to simulation mode if:
  - `SENDER_EMAIL` not configured
  - Gmail API client initialization fails
  - OAuth token missing or expired
- All errors logged with detailed context for troubleshooting

### 7. IAM Provisioning Agent (`provisioning_service/provisioning_agent.py`)

**Purpose**: Isolated, highly-privileged agent for IAM execution

**Security Model**:
- Runs in separate Cloud Run service
- Requires JIT (Just-in-Time) privilege elevation
- Only accepts commands via A2A protocol

**Tool**: `execute_iam_set_tool`

**Parameters**:
- `requested_role`: IAM role to assign
- `user_id`: Target user email
- `justification`: Audit justification text
- `gcp_project_scope`: Target GCP project

**Response Schema** (Pydantic):
```python
class IAMProvisioningResponse(BaseModel):
    status: str  # POLICY_APPLIED | EXECUTION_FAILURE | JIT_FAILURE
    timestamp: str
    reason: Optional[str]
    applied_policy: Optional[PolicyDetails]
    audit_trail: Optional[AuditTrail]
```

**Current Implementation**:
- Simulates JIT elevation (5% failure rate)
- Returns structured response with audit trail
- Production would call actual IAM API

### 8. Provisioning Service (`provisioning_service/app.py`)

**Purpose**: A2A server exposing the provisioning agent with security middleware

**Endpoints**:

| Endpoint | Method | Purpose | Auth Required |
|----------|--------|---------|---------------|
| `/` | GET/POST | Service information / Task delegation | ✅ Yes (POST) |
| `/.well-known/agent.json` | GET | A2A agent card | No |
| `/tasks/send` | POST | A2A task execution | ✅ Yes |
| `/health` | GET | Health check | No |

**A2A Protocol**:
- Compliant with A2A specification v0.2.6
- Accepts JSON-RPC format requests
- Returns structured task responses with proper Task envelope
- Supports multiple message part formats (text, JSON, base64, inline_data)
- Handles tool_calls envelope extraction from various SDK formats

**Security Features** - ✅ **FULLY IMPLEMENTED**:
- **ServiceAccountAuthMiddleware**: Validates incoming requests
- ID Token verification via `google.oauth2.id_token`
- Restricts access to specific service account (`ALLOWED_CALLER_SA`)
- Public endpoints bypass authentication (health, agent card)
- Audit trail includes authenticated caller identity

**Configuration**:
- `SERVICE_URL`: Public URL for agent card (required for token validation)
- `ALLOWED_CALLER_SA`: Email of authorized orchestrator service account
- `PORT`: HTTP server port (default: 8080)

**Middleware Flow**:
1. Public endpoints → bypass authentication
2. Protected endpoints → extract Bearer token
3. Verify token audience matches SERVICE_URL
4. Extract caller email from ID token
5. Validate caller matches ALLOWED_CALLER_SA
6. Inject authenticated_caller into request.state
7. Include caller in audit trail

---

## Data Models

### Firestore Collections

#### 1. `adk-sessions` (Session Store)
```javascript
{
  session_id: string,
  app_name: string,
  user_id: string,
  state: {
    request: {
      user_id: string,
      role: string,
      scope: string,
      user_timezone: string,
      status: string
    },
    status: string,
    final_audit: object
  },
  created_at: timestamp,
  updated_at: timestamp
}
```

#### 2. `provisioning-requests` (Audit Trail)
```javascript
{
  session_id: string,
  user_id: string,
  requested_role: string,
  project_scope: string,
  status: string,  // INITIATED | APPROVED | REJECTED | POLICY_APPLIED | FAILED
  timestamp: timestamp,
  project_id: string,
  updated_at: timestamp,
  result: object  // Final execution result
}
```

#### 3. `role_approvals` (Approver Database)
```javascript
{
  role_id: string,
  gcp_project_scope: string,
  required_approvers: [string],
  baseline_policy_doc_id: string,
  firestore_document_id: string
}
```

---

## Dependencies

### Core Framework
- **google-adk[a2a]**: Google Agent Development Kit with A2A support
- **google-cloud-firestore**: Session and approval data storage
- **google-cloud-aiplatform**: Vertex AI integration for NLU (Gemini)
- **google-cloud-discoveryengine**: RAG search capabilities (Vertex AI Search)
- **google-cloud-run**: Cloud Run SDK

### Web Framework
- **uvicorn[standard]**: ASGI server
- **starlette**: Lightweight async web framework

### Communication & Authentication
- **google-api-python-client**: Gmail API client
- **google-auth**: GCP authentication
- **google-auth-oauthlib**: OAuth flows for personal Gmail
- **google-auth-httplib2**: HTTP transport for Gmail API

### Utilities
- **pytz**: Timezone handling for compliance checks

---

## IAM Roles & Permissions

### Custom Roles Defined

#### 1. `ZeroTouchIamProvisionerRole` (Source Project)
**File**: `iam_custom_role.yaml`

**Permissions**:
- `iam.serviceAccounts.setIamPolicy`
- `resourcemanager.projects.setIamPolicy`

**Purpose**: Allows the provisioning service account to modify IAM policies

#### 2. `ZeroTouchIamProvisionerRole` (Target Project)
**File**: `provisioner_role.yaml`

**Permissions**:
- `resourcemanager.projects.setIamPolicy`

**Purpose**: Allows provisioning on specific target projects

---

## Workflow Example

### Step-by-Step Flow

```
1. Request Initiated
   POST /start_provisioning
   {
     "session_id": "session-123",
     "user_id": "dev@example.com",
     "requested_role": "roles/editor",
     "project_scope": "my-gcp-project",
     "user_timezone": "America/Los_Angeles"
   }

2. Approver Lookup
   Query Firestore: role_approvals
   → Returns: required_approvers, policy_doc_id

3. Policy Retrieval
   RAG Query → Vertex AI Search
   → Returns: policy_text, constraint_type

4. Compliance Check
   ✓ Constraint: temporal_access_only
   ✓ Current time: 10:30 AM PST, Wednesday
   ✓ PASS: Within business hours (Mon-Fri, 08:00-17:00)

5. Email Simulation (Mocked)
   → Simulate approval email sent

6. Response Simulation
   email_body = "Yes, please approve this request."

7. NLU Classification
   Gemini 2.5 Flash → Structured JSON
   {
     "status": "APPROVED",
     "approver_id": "manager@example.com",
     "reason_summary": "Explicit approval granted"
   }

8. A2A Execution
   RemoteA2aAgent → POST /tasks/send
   {
     "tool_calls": [{
       "function": {
         "name": "execute_iam_set_tool",
         "arguments": {
           "requested_role": "roles/editor",
           "user_id": "dev@example.com",
           "justification": "Policy excerpt...",
           "gcp_project_scope": "my-gcp-project"
         }
       }
     }]
   }

9. Audit Trail
   → Generate narrative with full decision history
   → Persist to provisioning-requests collection
   → Return final status to caller
```

---

## Current Implementation Status

### ✅ Completed Features (Production-Grade)

- **Multi-agent orchestration**: 6 specialized agents coordinated by orchestrator
- **A2A security isolation**: Provisioning agent isolated via A2A protocol with auth middleware
- **Firestore session management**: Persistent, resumable sessions with custom session service
- **RAG policy retrieval**: ✅ **REAL** Integration with Vertex AI Search Discovery Engine
- **Temporal compliance guardrails**: ✅ **ACTIVE** enforcement of time restrictions with timezone support
- **NLU classification**: ✅ **REAL** Gemini 2.5 Flash via Vertex AI SDK with structured output
- **Communication Agent**: ✅ **FULLY IMPLEMENTED** Gmail API integration with OAuth2 support
- **Service Account Authentication**: ✅ **FULLY IMPLEMENTED** ID token validation middleware
- **Structured audit trails**: Comprehensive logging and narrative generation
- **Docker containerization**: Ready for Cloud Run deployment
- **Custom IAM roles**: Least-privilege security model defined
- **Gmail OAuth Token Generation**: Utility script (`generate_gmail_token.py`) for personal Gmail

### ⚠️ Simulated Components (Working, but not calling real APIs)

- **IAM Provisioning**: Simulated execution (production: real IAM API calls via `setIamPolicy`)
- **JIT Elevation**: Simulated PAM integration with 5% failure rate (production: requires PAM system)
- **Approval Response Collection**: Hardcoded simulation (production: Pub/Sub email webhook)

### ❌ Not Yet Implemented

- **Pub/Sub Integration**: Async event handling for inbound email responses
- **Email Response Webhook**: Automatic parsing of approval email replies
- **Emergency Stop Mechanism**: Security kill switch endpoint
- **Production Error Handling**: Comprehensive retry and recovery logic for IAM API failures
- **Real IAM API Calls**: Actual `resourcemanager.projects.setIamPolicy` invocation

---

## Testing & Simulation

### Local Simulation

The system includes a simulation mode for testing:

**File**: `simulate_local_main.py` (if present)

**Simulated Components**:
1. **Email Communication**: Hardcoded approval response instead of actual email
2. **JIT Elevation**: Random success (95%) without real PAM
3. **IAM API**: No actual policy changes, returns mock success

**Test Scenario**:
```python
# Simulated approver response
simulated_response_text = "Yes, please approve this request."
# Alternative: "I'm travelling this week, so I'll review this next Monday."
```

### Running Tests

```bash
# Start the orchestrator service
python app.py

# Make a test request
curl -X POST http://localhost:8080/start_provisioning \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-1",
    "user_id": "dev@example.com",
    "requested_role": "roles/viewer",
    "project_scope": "test-project",
    "user_timezone": "UTC"
  }'
```

---

## Deployment

### Prerequisites

1. GCP Project with enabled APIs:
   - Cloud Run
   - Firestore
   - Vertex AI
   - Discovery Engine
   - IAM

2. Firestore databases:
   - `approver-store` (for approver lookups)
   - `adk-sessions-store` (for session management)

3. Vertex AI Search:
   - RAG engine created
   - Policy documents indexed

4. Service Accounts:
   - Orchestrator SA (low privilege)
   - Provisioner SA (high privilege with custom role)

### Deployment Steps

#### 1. Deploy Provisioning Service
```bash
cd provisioning_service
gcloud run deploy iam-provisioner-a2a \
  --source . \
  --region us-central1 \
  --service-account provisioner-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars SERVICE_URL=https://iam-provisioner-a2a-XXX.run.app
```

#### 2. Deploy Orchestrator Service
```bash
gcloud run deploy iam-orchestrator \
  --source . \
  --region us-central1 \
  --service-account orchestrator-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars \
    PROJECT_ID=your-project-id,\
    A2A_PROVISIONER_URL=https://iam-provisioner-a2a-XXX.run.app,\
    RAG_ENGINE_ID=your-engine-id,\
    RAG_DATA_STORE_ID=your-datastore-id
```

---

## Security Considerations

### Defense in Depth

1. **Network Isolation**: Provisioner service accessible only via authenticated A2A
2. **Least Privilege**: Orchestrator has no IAM write permissions
3. **Session Integrity**: Firestore rules enforce session ownership
4. **Audit Immutability**: All decisions logged before execution
5. **Temporal Guardrails**: Time-based access enforced at policy layer

### Threat Model

**Protected Against**:
- ✅ Privilege escalation via orchestrator compromise
- ✅ Time-based policy violations
- ✅ Unauthorized approver bypass (via Firestore validation)
- ✅ Audit trail tampering (write-once pattern)

## Monitoring & Observability

### Log Structure

All agents emit structured logs with:
- `session_id`: Request correlation ID
- `agent_name`: Source agent
- `phase`: Workflow step (lookup, rag, nlu, execution)
- `status`: SUCCESS | FAILURE | REJECTED
- `timestamp`: ISO 8601 format

### Audit Trail Format

```
--- AUDIT TRACE: SESSION session-123 ---
IAM REQUEST: Assigned role roles/editor to dev@example.com in my-gcp-project.
COMPLIANCE CHECK: Status: PASSED. Constraint: temporal_access_only.
JUSTIFICATION: Access restricted to business hours...
APPROVAL: Status: APPROVED. Approver: manager@example.com.
EXECUTION STATUS: POLICY_APPLIED.
JIT ACCESS: jit-7842.
IAM POLICY APPLIED: role=roles/editor, member=user:dev@example.com, resource=projects/my-gcp-project
--- END OF TRACE ---
```

### Firestore Audit Collection

All requests persisted to `provisioning-requests` with:
- Initial state: `INITIATED`
- Final states: `POLICY_APPLIED | REJECTED | FAILED`
- Full result payload with timestamp

---

## Future Enhancements

### Phase 2: Production Readiness
- Implement Communication Agent (Gmail API + Pub/Sub)
- Integrate real PAM/JIT system
- Add actual IAM API calls with dry-run mode
- Implement emergency stop mechanism
- Add rate limiting and abuse detection

### Phase 3: Advanced Features
- Multi-approver workflows (AND/OR logic)
- Risk-based approval routing
- Policy version tracking
- Self-service policy updates
- Integration with SIEM systems

### Phase 4: Intelligence
- Anomaly detection (unusual access patterns)
- Auto-revocation on suspicious activity
- Predictive access recommendations
- Policy drift detection

---

## Architecture Diagram

The architecture diagram is available in:
- `Architecture/Architecture Diagrams/document-export-02-11-2025-19_30_49.md`

---

## Contributing

This is a hackathon/research project demonstrating advanced multi-agent patterns for IAM automation.

### Key Design Patterns Demonstrated

1. **ADK Multi-Tool Pattern**: Agents as specialized tool collections
2. **A2A Security Isolation**: High-privilege operations via protocol boundary
3. **Session-Based Orchestration**: Persistent state across async workflows
4. **RAG-Grounded Decisions**: Policy retrieval for explainable AI
5. **LLM-Powered NLU**: Natural language approval classification
6. **Audit-First Architecture**: Immutable decision logs

---

## License

See `LICENSE` file for details.

---

## Acknowledgments

Built using:
- Google Agent Development Kit (ADK)
- Google Cloud Platform (Cloud Run, Firestore, Vertex AI)
- Gemini 2.5 Flash (Vertex AI)
- Vertex AI Search (RAG)

---

## Contact & Support

For questions about the architecture or implementation, refer to:
- Code comments in each agent file
- `PMO/gap analysis.txt` for implementation status
- Architecture diagrams in `Architecture/` directory

---

**Status**: Hackathon Prototype (60% Production-Ready)
**Last Updated**: 2025-02-11
**Version**: 1.0.0
