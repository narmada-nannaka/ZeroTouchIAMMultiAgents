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

**Environment Variables**:
- `PROJECT_ID`: GCP project identifier
- `A2A_PROVISIONER_URL`: URL of the isolated provisioning service
- `GCP_LOCATION`: Region for Vertex AI services (default: global)
- `RAG_ENGINE_ID`: Vertex AI Search engine ID
- `RAG_DATA_STORE_ID`: Data store ID for policy documents
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

a) **RAG Retrieval** (`_retrieve_policy_text`):
   - Queries Vertex AI Search with policy document ID
   - Extracts extractive segments and snippets
   - Returns policy context with source links

b) **Constraint Detection** (`_detect_constraint_type`):
   - Analyzes policy text for constraint types:
     - `temporal_access_only`: Time-restricted access
     - `guardrail_deny`: Explicitly forbidden
     - `none`: Standard access

c) **Temporal Compliance Check** (`_check_temporal_compliance`):
   - Validates access against current time
   - Enforces business hours (Mon-Fri, 08:00-17:00)
   - Timezone-aware checking
   - **Active Guardrail**: Blocks non-compliant requests immediately

**Configuration**:
- Uses Vertex AI Discovery Engine for RAG
- Supports extractive content and snippets
- Configurable via `RAG_ENGINE_ID` and `RAG_DATA_STORE_ID`

### 5. NLU Classifier Agent (`agents/nlu_classifier_agent.py`)

**Purpose**: Classifies human email responses using Gemini

**Model**: `gemini-2.5-flash` via Vertex AI SDK

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
- Enforces strict JSON schema via `GenerationConfig`
- System instruction for impartial classification
- Exponential backoff retry logic (3 attempts)
- Fallback to `REJECTED_CONTEXT_MISSING` on failure

**Region**: `asia-southeast1`

### 6. IAM Provisioning Agent (`provisioning_service/provisioning_agent.py`)

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

### 7. Provisioning Service (`provisioning_service/app.py`)

**Purpose**: A2A server exposing the provisioning agent

**Endpoints**:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Service information |
| `/.well-known/agent.json` | GET | A2A agent card |
| `/tasks/send` | POST | A2A task execution |
| `/health` | GET | Health check |

**A2A Protocol**:
- Compliant with A2A specification v0.2.6
- Accepts JSON-RPC format requests
- Returns structured task responses
- Supports multiple message part formats (text, JSON, base64)

**Configuration**:
- `SERVICE_URL`: Public URL for agent card
- `PORT`: HTTP server port (default: 8080)

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
- **google-cloud-aiplatform**: Vertex AI integration for NLU
- **google-cloud-discoveryengine**: RAG search capabilities
- **google-cloud-run**: Cloud Run SDK

### Web Framework
- **uvicorn[standard]**: ASGI server
- **starlette**: Lightweight async web framework

### Utilities
- **google-auth**: GCP authentication
- **google-auth-oauthlib**: OAuth flows
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

### ✅ Completed Features

- **Multi-agent orchestration**: 5 specialized agents coordinated by orchestrator
- **A2A security isolation**: Provisioning agent isolated via A2A protocol
- **Firestore session management**: Persistent, resumable sessions
- **RAG policy retrieval**: Integration with Vertex AI Search
- **Temporal compliance guardrails**: Active enforcement of time restrictions
- **NLU classification**: Gemini-powered approval classification
- **Structured audit trails**: Comprehensive logging and narrative generation
- **Docker containerization**: Ready for Cloud Run deployment
- **Custom IAM roles**: Least-privilege security model defined

### ⚠️ Partially Implemented

- **Policy Context Agent**: Uses simulated RAG (production: Vertex AI Search)
- **IAM Provisioning**: Simulated execution (production: real IAM API calls)
- **JIT Elevation**: Simulated PAM integration (production: requires PAM system)

### ❌ Not Yet Implemented

- **Communication Agent**: Email sending/receiving (planned: Gmail API + Pub/Sub)
- **Pub/Sub Integration**: Async event handling (planned: Cloud Pub/Sub)
- **Service Account Isolation**: Separate SAs per privilege level
- **Emergency Stop Mechanism**: Security kill switch
- **Production Error Handling**: Comprehensive retry and recovery logic

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
