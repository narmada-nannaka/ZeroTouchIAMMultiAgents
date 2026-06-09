"""
ADK session service compatibility adapter.

Bridges two gaps between the orchestrator's session API and VertexAiSessionService
in ADK 2.0, confirmed by introspection against the live Agent Engine:

  1. update_session() was removed in ADK 2.0. The orchestrator mutates session.state
     then calls update_session(); we translate that into the canonical append_event()
     state-delta path (direct mutation does NOT persist on its own -- verified).

  2. delete_session() is keyword-only in ADK 2.0, but the orchestrator calls it
     positionally with just session_id (the temp A2A session). We record
     (app_name, user_id) at create time and resolve it on delete.
"""
import logging
import uuid

from google.adk.sessions import VertexAiSessionService, Session
from google.adk.events import Event, EventActions


class CompatVertexAiSessionService(VertexAiSessionService):

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
