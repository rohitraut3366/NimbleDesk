from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import time

from nimbledesk.protocol.models import Session, SessionConfig, SessionState


class SessionError(RuntimeError):
    pass


class SessionManager:
    def __init__(self, clock: Callable[[], float] = time) -> None:
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def start(self, reason: str, config: SessionConfig) -> Session:
        if not reason.strip():
            raise ValueError("session reason cannot be empty")
        now = self._clock()
        session = Session(
            state=SessionState.ACTIVE,
            reason=reason.strip(),
            config=config,
            created_at=now,
            expires_at=now + config.max_duration_seconds,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            try:
                session = self._sessions[session_id]
            except KeyError as error:
                raise SessionError("unknown session") from error
            if session.state is not SessionState.STOPPED and self._clock() >= session.expires_at:
                session = session.model_copy(update={"state": SessionState.STOPPED})
                self._sessions[session_id] = session
            return session

    def set_state(self, session_id: str, state: SessionState) -> Session:
        if state in {SessionState.STARTING, SessionState.STOPPING}:
            raise ValueError(f"cannot set transitional session state directly: {state}")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError("unknown session")
            if session.state is SessionState.STOPPED and state is not SessionState.STOPPED:
                raise SessionError("stopped session cannot be restarted")
            updated = session.model_copy(update={"state": state})
            self._sessions[session_id] = updated
            return updated

    def consume_action(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError("unknown session")
            if session.action_count >= session.config.max_actions:
                raise SessionError("session action budget exhausted")
            updated = session.model_copy(update={"action_count": session.action_count + 1})
            self._sessions[session_id] = updated
            return updated
