"""
Vertex AI Memory Bank integration (long-term grant recall), scoped per requester.

Uses the bare Agent Engine that already backs sessions. API confirmed via
memory_probe.py:
  - create(name, fact, scope={"user_id": ...})
  - retrieve(name, scope={"user_id": ...}) -> [RetrievedMemory(.memory.fact)]

Each grant is stored as a parseable fact carrying its expiry, so a repeat request
within the 1-hour JIT window can be short-circuited ("already active").
Best-effort: never raises into the workflow.
"""
import os
import re
import logging
import datetime
import vertexai

PROJECT = os.environ.get("PROJECT_ID")
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")
ENGINE = f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}"

_client = vertexai.Client(project=PROJECT, location=LOCATION)

# Stored fact shape: "ACTIVE_GRANT role=<role> project=<project> expires=<iso8601>"
_GRANT_RE = re.compile(r"role=(\S+)\s+project=(\S+)\s+expires=(\S+)")


def _retrieve_facts(requester: str):
    res = _client.agent_engines.memories.retrieve(name=ENGINE, scope={"user_id": requester})
    facts = []
    for item in res:
        mem = getattr(item, "memory", None) or item
        fact = getattr(mem, "fact", None)
        if fact:
            facts.append(fact)
    return facts


def check_grants(requester: str, role: str, project: str):
    """Return (total_count, active_expiry_utc_or_None).

    active_expiry is set when a stored grant matches this role+project AND its
    expiry is still in the future (i.e. the JIT window hasn't lapsed).
    """
    try:
        facts = _retrieve_facts(requester)
    except Exception as e:
        logging.warning(f"Memory Bank recall failed: {e}")
        return 0, None

    now = datetime.datetime.now(datetime.timezone.utc)
    active = None
    for fact in facts:
        m = _GRANT_RE.search(fact)
        if not m:
            continue
        f_role, f_proj, f_exp = m.group(1), m.group(2), m.group(3)
        if f_role != role or f_proj != project:
            continue
        try:
            exp = datetime.datetime.fromisoformat(f_exp)
        except Exception:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        if exp > now and (active is None or exp > active):
            active = exp
    return len(facts), active


def record_grant(requester: str, role: str, project_scope: str, expires_utc: datetime.datetime):
    """Store a long-term memory of a completed grant (with expiry), scoped to the requester."""
    try:
        fact = f"ACTIVE_GRANT role={role} project={project_scope} expires={expires_utc.isoformat()}"
        _client.agent_engines.memories.create(name=ENGINE, fact=fact, scope={"user_id": requester})
        logging.info(f"Memory Bank: recorded grant for {requester}: {fact}")
    except Exception as e:
        logging.warning(f"Memory Bank record failed: {e}")
