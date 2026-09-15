from __future__ import annotations

import base64
from time import time

from nimbledesk.daemon.approvals import ApprovalDecision, ApprovalManager, PendingApproval
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.sessions import SessionError, SessionManager
from nimbledesk.perception.ocr import OcrProvider
from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    CoordinateTarget,
    DesktopObservation,
    ElementTarget,
    PermissionState,
    Point,
    PolicyDecision,
    Rectangle,
    ScreenCapture,
    SelectorTarget,
    Session,
    SessionConfig,
    SessionState,
    TextTarget,
    VisualTarget,
)


class DesktopRuntime:
    def __init__(
        self,
        backend: DesktopBackend,
        sessions: SessionManager,
        policy: ActionPolicy,
        approvals: ApprovalManager,
        audit: AuditLog,
        ocr_provider: OcrProvider | None = None,
    ) -> None:
        self._backend = backend
        self._sessions = sessions
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._ocr_provider = ocr_provider
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
        if self._ocr_provider is not None and self._ocr_provider.available:
            observation = observation.model_copy(
                update={
                    "capabilities": frozenset((*observation.capabilities, Capability.OCR)),
                    "permissions": {
                        **observation.permissions,
                        Capability.OCR: PermissionState.GRANTED,
                    },
                }
            )
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

        visual_evidence: dict[str, object] = {}
        if isinstance(execution_action.target, VisualTarget):
            execution_action, visual_error, visual_evidence = self._resolve_visual_target(
                execution_action
            )
            if visual_error:
                return self._finish(
                    action,
                    ActionStatus.STALE_OBSERVATION,
                    visual_error,
                    started_at,
                    data=visual_evidence,
                )
        if isinstance(execution_action.target, TextTarget):
            execution_action, text_error, text_evidence = self._resolve_text_target(
                execution_action
            )
            if text_error:
                status = (
                    ActionStatus.CAPABILITY_UNAVAILABLE
                    if self._ocr_provider is None or not self._ocr_provider.available
                    else ActionStatus.STALE_OBSERVATION
                )
                return self._finish(
                    action, status, text_error, started_at, data=text_evidence
                )
            visual_evidence.update(text_evidence)

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
        if visual_evidence:
            result = result.model_copy(
                update={"data": {**result.data, **visual_evidence}}
            )
        self._audit.record(action, result)
        return result

    def _recover_stale_action(
        self, action: ActionRequest
    ) -> tuple[ActionRequest | None, str]:
        if action.kind is ActionKind.APP_COMMAND:
            return None, "application commands are never retried automatically"
        if not isinstance(
            action.target, (ElementTarget, SelectorTarget, VisualTarget, TextTarget)
        ):
            return None, "only semantic, OCR, or visual targets can be revalidated automatically"
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
        elif isinstance(target, (VisualTarget, TextTarget)):
            target = target.model_copy(update={"observation_id": current.observation_id})
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
        if isinstance(action.target, (ElementTarget, VisualTarget, TextTarget)) and (
            action.target.observation_id != action.source_observation_id
        ):
            return "target and source observations do not match"
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

    def _resolve_visual_target(
        self, action: ActionRequest
    ) -> tuple[ActionRequest, str | None, dict[str, object]]:
        target = action.target
        if not isinstance(target, VisualTarget):
            return action, None, {}
        if action.kind not in {ActionKind.MOVE_POINTER, ActionKind.CLICK, ActionKind.DRAG}:
            return action, "visual targets only support pointer actions", {}
        try:
            capture = self._backend.capture(
                target.observation_id,
                target.bounds,
                CaptureOptions(
                    image_format="png",
                    max_width=max(64, min(4096, target.bounds.width)),
                    max_height=max(64, min(4096, target.bounds.height)),
                ),
            )
        except (ValueError, RuntimeError) as error:
            return action, f"visual target could not be recaptured: {error}", {}
        evidence: dict[str, object] = {
            "visual_target_bounds": target.bounds.model_dump(),
            "visual_target_confidence": target.confidence,
            "visual_signature_expected": target.signature,
            "visual_signature_actual": capture.sha256,
        }
        if target.confidence < 0.65:
            return action, "visual target confidence is below the execution threshold", evidence
        if capture.sha256 != target.signature:
            return action, "visual target pixels changed before execution", evidence
        point = Point(
            x=target.bounds.left + target.bounds.width // 2,
            y=target.bounds.top + target.bounds.height // 2,
        )
        evidence["visual_target_point"] = point.model_dump()
        resolved = action.model_copy(update={"target": CoordinateTarget(point=point)})
        return resolved, None, evidence

    def _resolve_text_target(
        self, action: ActionRequest
    ) -> tuple[ActionRequest, str | None, dict[str, object]]:
        target = action.target
        if not isinstance(target, TextTarget):
            return action, None, {}
        if action.kind not in {ActionKind.MOVE_POINTER, ActionKind.CLICK, ActionKind.DRAG}:
            return action, "OCR targets only support pointer actions", {}
        if self._ocr_provider is None or not self._ocr_provider.available:
            return action, "local OCR provider is unavailable", {}
        observation = self._observations[action.session_id]
        bounds = target.search_bounds
        if bounds is None and observation.focused_window_id:
            window = next(
                (
                    item
                    for item in observation.windows
                    if item.window_id == observation.focused_window_id
                ),
                None,
            )
            bounds = window.bounds if window else None
        if bounds is None:
            primary = next(
                (display for display in observation.displays if display.primary),
                observation.displays[0],
            )
            bounds = primary.logical_bounds
        try:
            capture = self._backend.capture(
                target.observation_id,
                bounds,
                CaptureOptions(image_format="png", max_width=4096, max_height=4096),
            )
            matches = tuple(
                match
                for match in self._ocr_provider.locate(
                    base64.b64decode(capture.data_base64), target.text, target.exact
                )
                if match.confidence >= target.minimum_confidence
            )
        except (ValueError, RuntimeError) as error:
            return action, f"OCR target resolution failed: {error}", {}
        alternatives = [
            {"text": match.text, "confidence": match.confidence}
            for match in matches[:5]
        ]
        evidence: dict[str, object] = {
            "targeting_method": "local_ocr",
            "ocr_query": target.text,
            "ocr_alternatives": alternatives,
        }
        if not matches:
            return action, "OCR text target was not found with sufficient confidence", evidence
        if len(matches) > 1 and matches[0].confidence - matches[1].confidence < 0.1:
            return action, "OCR text target is ambiguous; narrow the search bounds", evidence
        match = matches[0]
        scale_x = bounds.width / capture.width
        scale_y = bounds.height / capture.height
        resolved_bounds = Rectangle(
            left=bounds.left + round(match.bounds.left * scale_x),
            top=bounds.top + round(match.bounds.top * scale_y),
            width=max(1, round(match.bounds.width * scale_x)),
            height=max(1, round(match.bounds.height * scale_y)),
        )
        point = Point(
            x=resolved_bounds.left + resolved_bounds.width // 2,
            y=resolved_bounds.top + resolved_bounds.height // 2,
        )
        evidence.update(
            {
                "ocr_match": match.text,
                "ocr_confidence": match.confidence,
                "ocr_bounds": resolved_bounds.model_dump(),
                "ocr_target_point": point.model_dump(),
            }
        )
        return action.model_copy(update={"target": CoordinateTarget(point=point)}), None, evidence

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
