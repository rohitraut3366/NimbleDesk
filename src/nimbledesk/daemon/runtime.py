from __future__ import annotations

from time import time

from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.sessions import SessionError, SessionManager
from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    CaptureOptions,
    DesktopObservation,
    PolicyDecision,
    Rectangle,
    ScreenCapture,
    Session,
    SessionConfig,
    SessionState,
)


class DesktopRuntime:
    def __init__(
        self,
        backend: DesktopBackend,
        sessions: SessionManager,
        policy: ActionPolicy,
        approvals: ApprovalManager,
        audit: AuditLog,
    ) -> None:
        self._backend = backend
        self._sessions = sessions
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._observations: dict[str, DesktopObservation] = {}

    def start_session(self, reason: str, config: SessionConfig) -> Session:
        return self._sessions.start(reason, config)

    def set_session_state(self, session_id: str, state: SessionState) -> Session:
        session = self._sessions.set_state(session_id, state)
        if state in {SessionState.PAUSED, SessionState.STOPPED}:
            self._backend.cancel_input()
        return session

    def observe(self, session_id: str) -> DesktopObservation:
        session = self._sessions.get(session_id)
        if session.state is SessionState.STOPPED:
            raise SessionError("session is stopped")
        observation = self._backend.observe()
        self._observations[session_id] = observation
        return observation

    def approve(self, approval_id: str) -> str:
        return self._approvals.approve(approval_id)

    def capture(
        self,
        session_id: str,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        session = self._sessions.get(session_id)
        if session.state is SessionState.STOPPED:
            raise SessionError("session is stopped")
        observation = self._observations.get(session_id)
        if observation is None or observation.observation_id != observation_id:
            raise ValueError("capture requires the latest observation")
        if time() >= observation.expires_at:
            raise ValueError("observation has expired")
        return self._backend.capture(observation_id, region, options)

    def execute(self, action: ActionRequest) -> ActionResult:
        started_at = time()
        try:
            session = self._sessions.get(action.session_id)
        except SessionError as error:
            return self._finish(action, ActionStatus.REJECTED, str(error), started_at)

        observation_error = self._validate_observation(action)
        if observation_error:
            return self._finish(
                action,
                ActionStatus.STALE_OBSERVATION,
                observation_error,
                started_at,
            )

        approved = self._approvals.consume(action.approval_token, action)
        outcome = self._policy.evaluate(session, action, approved)
        if outcome.decision is PolicyDecision.DENY:
            return self._finish(action, ActionStatus.REJECTED, outcome.reason, started_at)
        if outcome.decision is PolicyDecision.REQUIRE_CONFIRMATION:
            pending = self._approvals.request(action)
            return self._finish(
                action,
                ActionStatus.CONFIRMATION_REQUIRED,
                outcome.reason,
                started_at,
                approval_id=pending.approval_id,
            )

        try:
            self._sessions.consume_action(action.session_id)
            result = self._backend.execute(action)
        except (SessionError, ValueError, RuntimeError) as error:
            return self._finish(action, ActionStatus.FAILED, str(error), started_at)
        self._audit.record(action, result)
        return result

    def _validate_observation(self, action: ActionRequest) -> str | None:
        if action.kind is ActionKind.WAIT:
            return None
        observation = self._observations.get(action.session_id)
        if observation is None:
            return "observe the desktop before executing input"
        if action.source_observation_id != observation.observation_id:
            return "source observation is not the latest observation"
        if time() >= observation.expires_at:
            return "source observation has expired"
        if (
            action.expected_application_id
            and action.expected_application_id != observation.active_application_id
        ):
            return "active application changed"
        if action.expected_window_id and action.expected_window_id != observation.focused_window_id:
            return "focused window changed"
        return None

    def _finish(
        self,
        action: ActionRequest,
        status: ActionStatus,
        message: str,
        started_at: float,
        approval_id: str | None = None,
    ) -> ActionResult:
        result = ActionResult(
            action_id=action.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            approval_id=approval_id,
        )
        self._audit.record(action, result)
        return result
