from __future__ import annotations

from time import time

from nimbledesk.daemon.approvals import ApprovalDecision, ApprovalManager, PendingApproval
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
    ElementTarget,
    PolicyDecision,
    Rectangle,
    ScreenCapture,
    SelectorTarget,
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
        self._observation_history: dict[str, dict[str, DesktopObservation]] = {}

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
        history = self._observation_history.setdefault(session_id, {})
        history[observation.observation_id] = observation
        while len(history) > 8:
            del history[next(iter(history))]
        return observation

    def approve(self, approval_id: str) -> str:
        return self._approvals.approve(approval_id)

    def reject_approval(self, approval_id: str) -> None:
        self._approvals.reject(approval_id)

    def list_approvals(self) -> tuple[PendingApproval, ...]:
        return self._approvals.list_pending()

    def approval_status(self, approval_id: str) -> ApprovalDecision:
        return self._approvals.status(approval_id)

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

        execution_action = action
        observation_error = self._validate_observation(execution_action)
        recovery_attempts = 0
        while observation_error and recovery_attempts < action.recovery.max_reobservations:
            recovered, recovery_error = self._recover_stale_action(execution_action)
            if recovered is None:
                observation_error = recovery_error
                break
            execution_action = recovered
            recovery_attempts += 1
            observation_error = self._validate_observation(execution_action)
        if observation_error:
            return self._finish(
                action,
                ActionStatus.STALE_OBSERVATION,
                observation_error,
                started_at,
                data={
                    "recovery_classification": "revalidation_failed",
                    "reobservations": recovery_attempts,
                },
            )

        approved = self._approvals.consume(action.approval_token, action)
        outcome = self._policy.evaluate(session, execution_action, approved)
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
            if execution_action.kind is ActionKind.APP_COMMAND:
                execution_action = execution_action.model_copy(
                    update={
                        "arguments": {
                            **action.arguments,
                            "_trusted_granted_paths": list(session.config.granted_paths),
                        }
                    }
                )
            result = self._backend.execute(execution_action)
        except (SessionError, ValueError, RuntimeError) as error:
            return self._finish(action, ActionStatus.FAILED, str(error), started_at)
        if recovery_attempts:
            result = result.model_copy(
                update={
                    "data": {
                        **result.data,
                        "recovery_classification": "stale_target_revalidated",
                        "reobservations": recovery_attempts,
                        "recovered_observation_id": execution_action.source_observation_id,
                    }
                }
            )
        self._audit.record(action, result)
        return result

    def _recover_stale_action(
        self, action: ActionRequest
    ) -> tuple[ActionRequest | None, str]:
        if action.kind is ActionKind.APP_COMMAND:
            return None, "application commands are never retried automatically"
        if not isinstance(action.target, (ElementTarget, SelectorTarget)):
            return None, "only semantic targets can be revalidated automatically"
        previous = self._observation_history.get(action.session_id, {}).get(
            action.source_observation_id or ""
        )
        current = self.observe(action.session_id)
        if action.expected_application_id and (
            current.active_application_id != action.expected_application_id
        ):
            return None, "active application changed during target revalidation"
        if action.expected_window_id and current.focused_window_id != action.expected_window_id:
            return None, "focused window changed during target revalidation"
        target = action.target
        if isinstance(target, ElementTarget):
            if previous is None:
                return None, "original element observation is no longer available"
            original = next(
                (
                    element
                    for element in previous.elements
                    if element.element_id == target.element_id
                ),
                None,
            )
            if original is None:
                return None, "original semantic element is unavailable"
            candidates = [
                element
                for element in current.elements
                if element.enabled
                and element.role == original.role
                and element.name == original.name
                and (
                    action.expected_window_id is None
                    or element.window_id == action.expected_window_id
                )
            ]
            if len(candidates) != 1:
                return None, "semantic target revalidation was ambiguous"
            target = ElementTarget(
                observation_id=current.observation_id,
                element_id=candidates[0].element_id,
            )
        return (
            action.model_copy(
                update={
                    "source_observation_id": current.observation_id,
                    "target": target,
                }
            ),
            "target revalidated",
        )

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
        data: dict[str, object] | None = None,
    ) -> ActionResult:
        result = ActionResult(
            action_id=action.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            approval_id=approval_id,
            data=data or {},
        )
        self._audit.record(action, result)
        return result
