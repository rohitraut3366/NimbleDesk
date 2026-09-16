from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from time import monotonic, time

from nimbledesk.analysis.models import ContentIndex
from nimbledesk.daemon.approvals import (
    ApprovalDecision,
    ApprovalEvidence,
    ApprovalManager,
    PendingApproval,
)
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.sessions import SessionError, SessionManager
from nimbledesk.jobs.service import (
    CreateJobRequest,
    JobRecord,
    JobService,
    PhotoJobRequest,
    ReviseJobRequest,
    VariantSelectionRequest,
    artifact_paths,
)
from nimbledesk.perception.ocr import OcrProvider
from nimbledesk.ports import AdapterCatalogBackend, DesktopBackend
from nimbledesk.protocol.models import (
    ActionCondition,
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    ConditionEvaluation,
    ConditionKind,
    CoordinateTarget,
    DesktopObservation,
    ElementTarget,
    IdempotencyClass,
    PermissionState,
    Point,
    PolicyDecision,
    PostconditionResult,
    Rectangle,
    ScreenCapture,
    SelectorTarget,
    Session,
    SessionConfig,
    SessionState,
    Target,
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
        jobs: JobService | None = None,
    ) -> None:
        self._backend = backend
        self._sessions = sessions
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._ocr_provider = ocr_provider
        self._jobs = jobs
        self._observations: dict[str, DesktopObservation] = {}
        self._observation_history: dict[str, dict[str, DesktopObservation]] = {}
        self._content_indexes: dict[tuple[str, str], ContentIndex] = {}
        self._action_results: dict[
            tuple[str, str], tuple[ActionRequest, ActionResult]
        ] = {}

    def health(self) -> dict[str, object]:
        capabilities = set(self._backend.capabilities)
        if self._ocr_provider is not None and self._ocr_provider.available:
            capabilities.add(Capability.OCR)
        return {
            "status": "ok",
            "backend": self._backend.backend_id,
            "capabilities": sorted(capability.value for capability in capabilities),
            "ocr_available": Capability.OCR in capabilities,
        }

    def recover_startup(self) -> None:
        self._sessions.stop_all()
        self._approvals.revoke_all()
        self._observations.clear()
        self._observation_history.clear()
        self._content_indexes.clear()
        self._action_results.clear()
        self._backend.cancel_input()

    def start_session(self, reason: str, config: SessionConfig) -> Session:
        return self._sessions.start(reason, config)

    def set_session_state(self, session_id: str, state: SessionState) -> Session:
        session = self._sessions.set_state(session_id, state)
        if state in {SessionState.PAUSED, SessionState.STOPPED}:
            self._backend.cancel_input()
        if state is SessionState.STOPPED:
            self._observations.pop(session_id, None)
            self._observation_history.pop(session_id, None)
            self._action_results = {
                key: value for key, value in self._action_results.items() if key[0] != session_id
            }
            self._content_indexes = {
                key: value for key, value in self._content_indexes.items() if key[0] != session_id
            }
        return session

    def list_sessions(self) -> tuple[Session, ...]:
        return self._sessions.list_sessions()

    def session_status(self, session_id: str) -> Session:
        return self._sessions.get(session_id)

    def emergency_stop(self) -> tuple[Session, ...]:
        sessions = self._sessions.stop_all()
        self._backend.cancel_input()
        self._approvals.revoke_all()
        self._observations.clear()
        self._observation_history.clear()
        self._content_indexes.clear()
        self._action_results.clear()
        return sessions

    def shutdown(self) -> tuple[Session, ...]:
        sessions = self.emergency_stop()
        if self._jobs is not None:
            self._jobs.close(cancel_running=True)
        return sessions

    def submit_job(
        self,
        kind: str,
        request: dict[str, object],
        session_id: str | None = None,
        parent_job_id: str | None = None,
    ) -> dict[str, object]:
        jobs = self._job_service()
        if session_id is not None:
            self._active_session(session_id)
        if kind == "create":
            creation = CreateJobRequest.model_validate(request)
            self._authorize_job_request(session_id, creation)
            return jobs.submit(creation, session_id=session_id).response()
        if kind == "photo":
            photo = PhotoJobRequest.model_validate(request)
            self._authorize_job_request(session_id, photo)
            return jobs.submit_photo(photo, session_id=session_id).response()
        if kind == "revision":
            if parent_job_id is None:
                raise ValueError("revision jobs require a parent job ID")
            parent = self._owned_job(session_id, parent_job_id)
            revision = ReviseJobRequest.model_validate(request)
            self._authorize_job_request(session_id, revision)
            return jobs.submit_revision(
                parent.state.job_id, revision, session_id=session_id
            ).response()
        raise ValueError(f"unknown job kind: {kind}")

    def job_response(
        self, job_id: str, session_id: str | None = None
    ) -> dict[str, object]:
        return self._owned_job(session_id, job_id).response()

    def list_jobs(self) -> list[dict[str, object]]:
        return self._job_service().list()

    def cancel_job(
        self, job_id: str, session_id: str | None = None
    ) -> dict[str, object]:
        self._owned_job(session_id, job_id)
        job = self._job_service().cancel(job_id)
        if job is None:
            raise ValueError("unknown creative job")
        return job.response()

    def select_job_variant(
        self,
        job_id: str,
        variant_id: str,
        request: dict[str, object],
        session_id: str | None = None,
    ) -> dict[str, object]:
        job = self._owned_job(session_id, job_id)
        selection = VariantSelectionRequest.model_validate(request)
        return self._job_service().select_variant(job, variant_id, selection).response()

    def job_artifact(
        self, job_id: str, artifact_name: str, session_id: str | None = None
    ) -> dict[str, object]:
        job = self._owned_job(session_id, job_id)
        with job.lock:
            path = artifact_paths(job.state).get(artifact_name)
        if path is None:
            raise ValueError("unknown job artifact")
        return {"path": str(path), "size_bytes": path.stat().st_size}

    def _job_service(self) -> JobService:
        if self._jobs is None:
            raise RuntimeError("creative job service is unavailable")
        return self._jobs

    def _owned_job(self, session_id: str | None, job_id: str) -> JobRecord:
        job = self._job_service().get(job_id)
        if job is None:
            raise ValueError("unknown creative job")
        with job.lock:
            if session_id is not None and job.state.session_id != session_id:
                raise ValueError("unknown creative job")
        return job

    def _authorize_job_request(
        self,
        session_id: str | None,
        request: CreateJobRequest | ReviseJobRequest | PhotoJobRequest,
    ) -> None:
        if session_id is None:
            return
        if isinstance(request, (CreateJobRequest, PhotoJobRequest)):
            paths = [request.source, request.output_directory]
        else:
            paths = [request.plan, request.output_directory]
        if isinstance(request, CreateJobRequest):
            paths.extend(
                path
                for path in (
                    request.events,
                    request.game_pack,
                    request.vision_provider,
                    request.transcript,
                    request.music_catalog,
                    request.sound_catalog,
                )
                if path is not None
            )
        self.authorize_paths(session_id, tuple(paths))

    def adapter_descriptions(self) -> tuple[dict[str, object], ...]:
        if not isinstance(self._backend, AdapterCatalogBackend):
            return ()
        return self._backend.adapter_descriptions()

    def audit_summaries(self, session_id: str, limit: int) -> tuple[dict[str, object], ...]:
        self._sessions.get(session_id)
        if not 1 <= limit <= 100:
            raise ValueError("audit result limit must be between 1 and 100")
        return self._audit.summaries(session_id, limit)

    def audit_integrity(self) -> dict[str, object]:
        return self._audit.integrity()

    def policy_summary(self) -> dict[str, object]:
        return self._policy.summary()

    def authorize_paths(self, session_id: str, paths: tuple[Path, ...]) -> None:
        session = self._active_session(session_id)
        for path in paths:
            _granted_path(path, session.config.granted_paths)

    def open_content_index(self, session_id: str, index_path: Path) -> dict[str, object]:
        session = self._active_session(session_id)
        resolved = _granted_file(index_path, session.config.granted_paths)
        index = ContentIndex.model_validate_json(resolved.read_text(encoding="utf-8"))
        identity = hashlib.sha256(
            (session_id + index.asset.asset_id + _index_configuration(index)).encode()
        ).hexdigest()[:24]
        index_id = f"index-{identity}"
        self._content_indexes[(session_id, index_id)] = index
        return {
            "index_id": index_id,
            "asset_id": index.asset.asset_id,
            "duration_seconds": index.asset.metadata.duration_seconds,
            "tracks": [track.name for track in index.tracks],
            "semantic_event_count": len(index.semantic_events),
        }

    def search_content_index(
        self,
        session_id: str,
        index_id: str,
        query: str,
        maximum_results: int,
        maximum_tokens: int,
    ) -> dict[str, object]:
        index = self._content_index(session_id, index_id)
        if not query.strip():
            raise ValueError("media search query cannot be empty")
        if not 1 <= maximum_results <= 200 or not 128 <= maximum_tokens <= 100_000:
            raise ValueError("media search result or token budget is outside allowed bounds")
        terms = query.casefold().split()
        results: list[dict[str, object]] = []
        for track in index.tracks:
            for point_number, point in enumerate(track.points):
                searchable = " ".join((*point.labels, point.text or "", *point.evidence)).casefold()
                if all(term in searchable for term in terms):
                    results.append(
                        {
                            "result_id": _point_id(
                                track.name,
                                point_number,
                                track.provenance.configuration_hash,
                            ),
                            "kind": "track_point",
                            "track": track.name,
                            "start_seconds": point.source_range.start.seconds,
                            "duration_seconds": point.source_range.duration.seconds,
                            "confidence": point.confidence,
                            "labels": point.labels,
                            "excerpt": (point.text or " ".join(point.evidence))[:300],
                        }
                    )
        for event_number, event in enumerate(index.semantic_events):
            searchable = " ".join(
                (event.event_type, event.label or "", *event.provenance, *event.evidence)
            ).casefold()
            if all(term in searchable for term in terms):
                results.append(
                    {
                        "result_id": _event_id(event_number, index.asset.asset_id),
                        "kind": "semantic_event",
                        "event_type": event.event_type,
                        "time_seconds": event.time_seconds,
                        "confidence": event.importance,
                        "label": event.label,
                    }
                )
        return _bounded_media_results(results, maximum_results, maximum_tokens)

    def content_index_detail(
        self, session_id: str, index_id: str, result_id: str, maximum_tokens: int
    ) -> dict[str, object]:
        index = self._content_index(session_id, index_id)
        if not 128 <= maximum_tokens <= 100_000:
            raise ValueError("media detail token budget is outside allowed bounds")
        detail: dict[str, object]
        truncated_fields: list[str] = []
        if result_id.startswith("event:"):
            event_number = int(result_id.split(":", 2)[1])
            if not 0 <= event_number < len(index.semantic_events):
                raise ValueError("unknown media result ID")
            event = index.semantic_events[event_number]
            if result_id != _event_id(event_number, index.asset.asset_id):
                raise ValueError("media result ID is stale")
            detail = event.model_dump(mode="json")
        elif result_id.startswith("track:"):
            _, track_name, point_text, configuration = result_id.split(":", 3)
            track = index.track(track_name)
            point_number = int(point_text)
            if not 0 <= point_number < len(track.points):
                raise ValueError("unknown media result ID")
            if configuration != track.provenance.configuration_hash[:12]:
                raise ValueError("media result ID is stale")
            detail = track.points[point_number].model_dump(mode="json")
            detail["track"] = track.name
            detail["provenance"] = track.provenance.model_dump(mode="json")
        else:
            raise ValueError("unknown media result ID")
        usage = _estimated_tokens(detail)
        if usage > maximum_tokens:
            detail.pop("text", None)
            detail["evidence"] = []
            truncated_fields.extend(("text", "evidence"))
            usage = _estimated_tokens(detail)
        if usage > maximum_tokens:
            raise ValueError("media detail budget is too small for required fields")
        return {
            "detail": detail,
            "usage": {
                "estimated_text_tokens": usage,
                "maximum_text_tokens": maximum_tokens,
                "truncated_fields": truncated_fields,
            },
        }

    def _content_index(self, session_id: str, index_id: str) -> ContentIndex:
        self._active_session(session_id)
        try:
            return self._content_indexes[(session_id, index_id)]
        except KeyError as error:
            raise ValueError("unknown or expired media index handle") from error

    def _active_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session.state is not SessionState.ACTIVE:
            raise SessionError("session is not active")
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
        observation = observation.model_copy(
            update={
                "windows_sha256": _content_hash(
                    [window.model_dump(mode="json") for window in observation.windows]
                ),
                "ui_tree_sha256": _content_hash(
                    [element.model_dump(mode="json") for element in observation.elements]
                ),
            }
        )
        self._observations[session_id] = observation
        history = self._observation_history.setdefault(session_id, {})
        history[observation.observation_id] = observation
        while len(history) > 8:
            del history[next(iter(history))]
        return observation

    def observation(self, session_id: str, observation_id: str) -> DesktopObservation:
        self._sessions.get(session_id)
        observation = self._observation_history.get(session_id, {}).get(observation_id)
        if observation is None or time() >= observation.expires_at:
            raise ValueError("observation is unknown or expired")
        return observation

    def observation_changes(
        self, session_id: str, previous_id: str, current_id: str
    ) -> dict[str, object]:
        history = self._observation_history.get(session_id, {})
        previous = history.get(previous_id)
        current = history.get(current_id)
        if previous is None or current is None:
            return {
                "previous_observation_id": previous_id,
                "unavailable": True,
                "unchanged": False,
                "changed_fields": [],
                "changed_windows": 0,
                "changed_elements": 0,
            }
        previous_windows = {window.window_id: window for window in previous.windows}
        current_windows = {window.window_id: window for window in current.windows}
        previous_elements = {element.element_id: element for element in previous.elements}
        current_elements = {element.element_id: element for element in current.elements}
        changed_windows = sum(
            previous_windows.get(item) != current_windows.get(item)
            for item in set(previous_windows) | set(current_windows)
        )
        changed_elements = sum(
            previous_elements.get(item) != current_elements.get(item)
            for item in set(previous_elements) | set(current_elements)
        )
        fields = {
            "active_application": previous.active_application_id
            != current.active_application_id,
            "focused_window": previous.focused_window_id != current.focused_window_id,
            "cursor": previous.cursor != current.cursor,
            "capabilities": previous.capabilities != current.capabilities,
            "permissions": previous.permissions != current.permissions,
            "displays": previous.displays != current.displays,
            "windows": changed_windows > 0,
            "elements": changed_elements > 0,
        }
        return {
            "previous_observation_id": previous_id,
            "unchanged": not any(fields.values()),
            "changed_fields": sorted(name for name, changed in fields.items() if changed),
            "changed_windows": changed_windows,
            "changed_elements": changed_elements,
        }

    def approve(self, approval_id: str) -> str:
        return self._approvals.approve(approval_id)

    def reject_approval(self, approval_id: str) -> None:
        self._approvals.reject(approval_id)

    def approve_temporary(
        self, approval_id: str, duration_seconds: int, maximum_uses: int
    ) -> dict[str, object]:
        rule = self._approvals.approve_temporary(
            approval_id, duration_seconds, maximum_uses
        )
        return {
            "rule_id": rule.rule_id,
            "session_id": rule.session_id,
            "description": rule.description,
            "expires_at": rule.expires_at,
            "remaining_uses": rule.remaining_uses,
        }

    def approval_rules(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "rule_id": rule.rule_id,
                "session_id": rule.session_id,
                "description": rule.description,
                "expires_at": rule.expires_at,
                "remaining_uses": rule.remaining_uses,
            }
            for rule in self._approvals.list_rules()
        )

    def revoke_approval_rule(self, rule_id: str) -> None:
        self._approvals.revoke_rule(rule_id)

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

    def resolve_target(
        self, session_id: str, observation_id: str, target: Target
    ) -> dict[str, object]:
        probe = ActionRequest(
            session_id=session_id,
            source_observation_id=observation_id,
            kind=ActionKind.MOVE_POINTER,
            target=target,
        )
        observation_error = self._validate_observation(probe)
        if observation_error:
            raise ValueError(observation_error)
        observation = self._observations[session_id]
        resolved_target: Target = target
        evidence: dict[str, object] = {}
        if isinstance(target, ElementTarget):
            matches = [
                element
                for element in observation.elements
                if element.element_id == target.element_id and element.enabled
            ]
            if len(matches) != 1:
                raise ValueError("semantic element target is unavailable or ambiguous")
            element = matches[0]
            evidence = {
                "element_id": element.element_id,
                "window_id": element.window_id,
                "role": element.role,
                "name": element.name,
                "bounds": element.bounds.model_dump() if element.bounds else None,
            }
        elif isinstance(target, SelectorTarget):
            matches = [
                element
                for element in observation.elements
                if element.enabled
                and (target.role is None or element.role == target.role)
                and (target.name is None or element.name == target.name)
                and (target.window_id is None or element.window_id == target.window_id)
                and (
                    target.application_id is None
                    or any(
                        window.window_id == element.window_id
                        and window.application_id == target.application_id
                        for window in observation.windows
                    )
                )
            ]
            if len(matches) != 1:
                raise ValueError(f"selector resolved to {len(matches)} enabled elements")
            element = matches[0]
            resolved_target = ElementTarget(
                observation_id=observation_id, element_id=element.element_id
            )
            evidence = {
                "element_id": element.element_id,
                "window_id": element.window_id,
                "role": element.role,
                "name": element.name,
                "bounds": element.bounds.model_dump() if element.bounds else None,
            }
        elif isinstance(target, VisualTarget):
            resolved, error, evidence = self._resolve_visual_target(probe)
            if error:
                raise ValueError(error)
            assert resolved.target is not None
            resolved_target = resolved.target
        elif isinstance(target, TextTarget):
            resolved, error, evidence = self._resolve_text_target(probe)
            if error:
                raise ValueError(error)
            assert resolved.target is not None
            resolved_target = resolved.target
        return {
            "observation_id": observation_id,
            "target": resolved_target.model_dump(mode="json"),
            "evidence": evidence,
        }

    def execute(self, action: ActionRequest) -> ActionResult:
        started_at = time()
        started_monotonic = monotonic()
        deadline = started_monotonic + action.deadline_ms / 1_000
        action_key = (action.session_id, action.action_id)
        cached_entry = self._action_results.get(action_key)
        if cached_entry is not None:
            completed_action, cached = cached_entry
            if completed_action != action:
                return self._finish(
                    action,
                    ActionStatus.REJECTED,
                    "action ID was already used for a different request",
                    started_at,
                    error_code="action_id_conflict",
                )
            if action.idempotency in {
                IdempotencyClass.IDEMPOTENT,
                IdempotencyClass.READ_ONLY,
            }:
                replay = cached.model_copy(
                    update={"data": {**cached.data, "idempotent_replay": True}}
                )
                self._audit.record(action, replay)
                return replay
            if action.idempotency is IdempotencyClass.NON_IDEMPOTENT:
                return self._finish(
                    action,
                    ActionStatus.REJECTED,
                    "non-idempotent action ID has already completed",
                    started_at,
                    error_code="duplicate_action",
                )
        try:
            session = self._sessions.get(action.session_id)
        except SessionError as error:
            return self._finish(
                action,
                ActionStatus.REJECTED,
                str(error),
                started_at,
                error_code="session_unavailable",
            )

        if monotonic() >= deadline:
            return self._finish(
                action,
                ActionStatus.TIMED_OUT,
                "action deadline expired before validation",
                started_at,
                error_code="deadline_exceeded",
            )

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
                error_code="stale_observation",
            )

        preconditions = self._evaluate_conditions(
            action.preconditions, self._observations.get(action.session_id)
        )
        if not preconditions.satisfied:
            return self._finish(
                action,
                ActionStatus.REJECTED,
                "one or more action preconditions were not satisfied",
                started_at,
                data={
                    "preconditions": preconditions.model_dump(mode="json"),
                },
                error_code="precondition_failed",
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
                    error_code="visual_target_changed",
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
                    action,
                    status,
                    text_error,
                    started_at,
                    data=text_evidence,
                    error_code=(
                        "capability_unavailable"
                        if status is ActionStatus.CAPABILITY_UNAVAILABLE
                        else "text_target_changed"
                    ),
                )
            visual_evidence.update(text_evidence)

        if monotonic() >= deadline:
            return self._finish(
                action,
                ActionStatus.TIMED_OUT,
                "action deadline expired before policy evaluation",
                started_at,
                error_code="deadline_exceeded",
                resolved_target=execution_action.target,
            )

        approved = self._approvals.consume(action.approval_token, action)
        outcome = self._policy.evaluate(session, execution_action, approved)
        if outcome.decision is PolicyDecision.DENY:
            return self._finish(
                action,
                ActionStatus.REJECTED,
                outcome.reason,
                started_at,
                error_code="policy_denied",
                resolved_target=execution_action.target,
            )
        if outcome.decision is PolicyDecision.REQUIRE_CONFIRMATION:
            pending = self._approvals.request(action, self._approval_evidence(action))
            return self._finish(
                action,
                ActionStatus.CONFIRMATION_REQUIRED,
                outcome.reason,
                started_at,
                approval_id=pending.approval_id,
                error_code="approval_required",
                resolved_target=execution_action.target,
            )

        if monotonic() >= deadline:
            return self._finish(
                action,
                ActionStatus.TIMED_OUT,
                "action deadline expired before execution",
                started_at,
                error_code="deadline_exceeded",
                resolved_target=execution_action.target,
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
            execution_attempts = 1
            result = self._backend.execute(execution_action)
            while (
                result.status is ActionStatus.FAILED
                and execution_attempts <= action.maximum_retries
                and action.idempotency
                in {IdempotencyClass.IDEMPOTENT, IdempotencyClass.READ_ONLY}
                and monotonic() < deadline
            ):
                execution_attempts += 1
                result = self._backend.execute(execution_action)
        except (SessionError, ValueError, RuntimeError) as error:
            self._backend.cancel_input()
            return self._finish(
                action,
                ActionStatus.FAILED,
                str(error),
                started_at,
                error_code="backend_failure",
                resolved_target=execution_action.target,
            )
        backend_evidence = result.backend_evidence or result.data
        if execution_attempts > 1:
            result = result.model_copy(
                update={
                    "data": {**result.data, "execution_attempts": execution_attempts}
                }
            )
        if monotonic() >= deadline:
            self._backend.cancel_input()
            result = result.model_copy(
                update={
                    "status": ActionStatus.TIMED_OUT,
                    "message": "action exceeded its deadline",
                    "error_code": "deadline_exceeded",
                }
            )
        if result.status in {
            ActionStatus.FAILED,
            ActionStatus.TIMED_OUT,
            ActionStatus.CANCELLED,
        }:
            self._backend.cancel_input()
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
        next_observation: DesktopObservation | None = None
        postcondition_result: PostconditionResult | None = None
        if result.status is ActionStatus.COMPLETED and (
            action.capture_after or action.postconditions
        ):
            try:
                next_observation = self.observe(action.session_id)
            except (SessionError, ValueError, RuntimeError) as error:
                result = result.model_copy(
                    update={
                        "status": ActionStatus.FAILED,
                        "message": f"action completed but follow-up observation failed: {error}",
                        "error_code": "postcondition_observation_failed",
                    }
                )
            else:
                postcondition_result = self._evaluate_conditions(
                    action.postconditions, next_observation
                )
                if not postcondition_result.satisfied:
                    result = result.model_copy(
                        update={
                            "status": ActionStatus.FAILED,
                            "message": "action completed but a postcondition was not satisfied",
                            "error_code": "postcondition_failed",
                        }
                    )
        result = result.model_copy(
            update={
                "duration_ms": max(0, (time() - started_at) * 1_000),
                "backend_evidence": backend_evidence,
                "resolved_target": execution_action.target,
                "postcondition_result": postcondition_result,
                "next_observation_id": (
                    next_observation.observation_id if next_observation else None
                ),
                "error_code": result.error_code or _error_code_for_status(result.status),
            }
        )
        self._audit.record(action, result)
        if result.status is ActionStatus.COMPLETED and action.idempotency in {
            IdempotencyClass.IDEMPOTENT,
            IdempotencyClass.READ_ONLY,
            IdempotencyClass.NON_IDEMPOTENT,
        }:
            self._action_results[action_key] = (action, result)
        return result

    def _evaluate_conditions(
        self,
        conditions: tuple[ActionCondition, ...],
        observation: DesktopObservation | None,
    ) -> PostconditionResult:
        evaluations: list[ConditionEvaluation] = []
        for condition in conditions:
            satisfied = False
            actual: str | None = None
            if observation is not None:
                if condition.kind is ConditionKind.ACTIVE_APPLICATION:
                    actual = observation.active_application_id
                    satisfied = actual == condition.value
                elif condition.kind is ConditionKind.FOCUSED_WINDOW:
                    actual = observation.focused_window_id
                    satisfied = actual == condition.value
                elif condition.kind in {
                    ConditionKind.ELEMENT_PRESENT,
                    ConditionKind.ELEMENT_ABSENT,
                }:
                    element_matches = [
                        element
                        for element in observation.elements
                        if (
                            element.element_id == condition.value
                            or element.name == condition.value
                        )
                        and (condition.role is None or element.role == condition.role)
                    ]
                    actual = str(len(element_matches))
                    satisfied = bool(element_matches)
                    if condition.kind is ConditionKind.ELEMENT_ABSENT:
                        satisfied = not satisfied
                elif condition.kind in {
                    ConditionKind.WINDOW_PRESENT,
                    ConditionKind.WINDOW_ABSENT,
                }:
                    window_matches = [
                        window
                        for window in observation.windows
                        if window.window_id == condition.value or window.title == condition.value
                    ]
                    actual = str(len(window_matches))
                    satisfied = bool(window_matches)
                    if condition.kind is ConditionKind.WINDOW_ABSENT:
                        satisfied = not satisfied
                elif condition.kind is ConditionKind.UI_TREE_CHANGED:
                    actual = observation.ui_tree_sha256
                    satisfied = actual is not None and actual != condition.value
                elif condition.kind is ConditionKind.WINDOWS_CHANGED:
                    actual = observation.windows_sha256
                    satisfied = actual is not None and actual != condition.value
            evaluations.append(
                ConditionEvaluation(
                    condition=condition,
                    satisfied=satisfied,
                    actual=actual,
                )
            )
        return PostconditionResult(
            satisfied=all(evaluation.satisfied for evaluation in evaluations),
            observation_id=observation.observation_id if observation else None,
            evaluations=tuple(evaluations),
        )

    def _approval_evidence(self, action: ActionRequest) -> ApprovalEvidence | None:
        observation_id = action.source_observation_id
        if observation_id is None:
            return None
        observation = self._observations.get(action.session_id)
        if observation is None or observation.observation_id != observation_id:
            return None
        try:
            capture = self._backend.capture(
                observation_id,
                options=CaptureOptions(
                    image_format="jpeg", max_width=640, max_height=400, jpeg_quality=50
                ),
            )
        except (ValueError, RuntimeError):
            return None
        return ApprovalEvidence(
            observation_id=observation_id,
            mime_type=capture.mime_type,
            data_base64=capture.data_base64,
            sha256=capture.sha256,
            width=capture.width,
            height=capture.height,
        )

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
        error_code: str | None = None,
        resolved_target: Target | None = None,
    ) -> ActionResult:
        finished_at = time()
        result = ActionResult(
            action_id=action.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=finished_at,
            approval_id=approval_id,
            data=data or {},
            error_code=error_code or _error_code_for_status(status),
            duration_ms=max(0, (finished_at - started_at) * 1_000),
            resolved_target=resolved_target,
        )
        self._audit.record(action, result)
        return result


def _error_code_for_status(status: ActionStatus) -> str | None:
    return {
        ActionStatus.FAILED: "backend_failure",
        ActionStatus.REJECTED: "rejected",
        ActionStatus.CONFIRMATION_REQUIRED: "approval_required",
        ActionStatus.STALE_OBSERVATION: "stale_observation",
        ActionStatus.TIMED_OUT: "deadline_exceeded",
        ActionStatus.CANCELLED: "cancelled",
        ActionStatus.CAPABILITY_UNAVAILABLE: "capability_unavailable",
    }.get(status)


def _granted_file(path: Path, granted_paths: tuple[str, ...]) -> Path:
    resolved = _granted_path(path, granted_paths)
    if not resolved.is_file():
        raise ValueError("media index path is not a file")
    return resolved


def _granted_path(path: Path, granted_paths: tuple[str, ...]) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise ValueError("media index path contains a symbolic link")
    resolved = absolute.resolve()
    grants = tuple(Path(value).expanduser().resolve() for value in granted_paths)
    if not any(resolved == grant or grant in resolved.parents for grant in grants):
        raise ValueError("path is outside session grants")
    return resolved


def _index_configuration(index: ContentIndex) -> str:
    return "|".join(track.provenance.configuration_hash for track in index.tracks)


def _point_id(track_name: str, point_number: int, configuration_hash: str) -> str:
    return f"track:{track_name}:{point_number}:{configuration_hash[:12]}"


def _event_id(event_number: int, asset_id: str) -> str:
    return f"event:{event_number}:{asset_id[:12]}"


def _estimated_tokens(value: object) -> int:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return max(1, (len(encoded) + 3) // 4)


def _content_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bounded_media_results(
    results: list[dict[str, object]], maximum_results: int, maximum_tokens: int
) -> dict[str, object]:
    selected: list[dict[str, object]] = []
    for result in results[:maximum_results]:
        if _estimated_tokens({"results": [*selected, result]}) > maximum_tokens:
            break
        selected.append(result)
    return {
        "results": selected,
        "usage": {
            "estimated_text_tokens": _estimated_tokens({"results": selected}),
            "maximum_text_tokens": maximum_tokens,
            "truncated": len(selected) < len(results),
            "available_results": len(results),
        },
    }
