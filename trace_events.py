"""
Per-step trace events for the live node-graph UI.

The orchestrator calls record_event() at each phase; events are written to a
Firestore subcollection (provisioning-requests/{session_id}/trace). The SSE
endpoint in app.py reads them with read_events_since(). Firestore-backed (not
in-memory) so it works across Cloud Run instances and a late-connecting browser
replays the full sequence from seq=0.

status values: running | completed | rejected | info
The sentinel node "__done__" (status "end") signals the SSE stream to close.
"""
import os
import time
import datetime
import logging

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

PROJECT_ID = os.environ.get("PROJECT_ID")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "agbg-anz-zerotouch-iam-db")
PROVISIONING_REQUESTS_COLLECTION = "provisioning-requests"
TRACE_SUBCOLLECTION = "trace"

_db = firestore.Client(project=PROJECT_ID, database=FIRESTORE_DATABASE)


def record_event(session_id: str, node: str, status: str, result: str = "") -> None:
    """Append one trace event. Best-effort: never raise into the workflow."""
    if not session_id:
        return
    try:
        seq = time.time_ns()  # monotonic-enough wall clock, instance-independent
        _db.collection(PROVISIONING_REQUESTS_COLLECTION).document(session_id) \
            .collection(TRACE_SUBCOLLECTION).document(str(seq)).set({
                "node": node,
                "status": status,
                "result": result or "",
                "seq": seq,
                "ts": datetime.datetime.now(datetime.timezone.utc),
            })
    except Exception as e:
        logging.warning(f"trace record_event failed ({node}/{status}): {e}")


def done(session_id: str) -> None:
    """Emit the terminal sentinel so the SSE stream can close."""
    record_event(session_id, "__done__", "end", "")


def read_events_since(session_id: str, last_seq: int) -> list[dict]:
    """Return trace events with seq > last_seq, ordered by seq."""
    out = []
    try:
        q = (
            _db.collection(PROVISIONING_REQUESTS_COLLECTION).document(session_id)
            .collection(TRACE_SUBCOLLECTION)
            .where(filter=FieldFilter("seq", ">", last_seq))
            .order_by("seq")
        )
        for d in q.stream():
            data = d.to_dict()
            out.append({
                "node": data.get("node"),
                "status": data.get("status"),
                "result": data.get("result", ""),
                "seq": data.get("seq", 0),
            })
    except Exception as e:
        logging.warning(f"trace read_events_since failed: {e}")
    return out
