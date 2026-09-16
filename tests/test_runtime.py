import json
from pathlib import Path

import pytest

from nimbledesk.backends import SimulatorBackend
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.perception.ocr import OcrMatch
from nimbledesk.protocol.models import (
    ActionCondition,
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    CaptureOptions,
    ConditionKind,
    CoordinateTarget,
    ElementTarget,
    IdempotencyClass,
    Point,
    RecoveryOptions,
    Rectangle,
    SelectorTarget,
    SessionConfig,
    SessionState,
    TextTarget,
    VisualTarget,
)


def make_runtime(audit_path: Path | None = None) -> tuple[DesktopRuntime, SimulatorBackend]:
    backend = SimulatorBackend()
    return (
        DesktopRuntime(
            backend=backend,
            sessions=SessionManager(),
            policy=ActionPolicy(host_input_enabled=True),
            approvals=ApprovalManager(),
            audit=AuditLog(audit_path),
        ),
        backend,
    )


def click_request(session_id: str, observation_id: str) -> ActionRequest:
    return ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        expected_application_id="fixture.app",
        expected_window_id="fixture-window",
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=300, y=200)),
    )


def test_input_requires_enabled_session_and_fresh_observation() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("read-only inspection", SessionConfig())
    observation = runtime.observe(session.session_id)

    result = runtime.execute(click_request(session.session_id, observation.observation_id))

    assert result.status is ActionStatus.REJECTED
    assert backend.executed_actions == []


def test_startup_recovery_stops_sessions_and_releases_input() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("stale session", SessionConfig(input_enabled=True))

    runtime.recover_startup()

    assert runtime.session_status(session.session_id).state is SessionState.STOPPED
    assert backend.input_cancelled is True


def test_observation_change_summary_detects_unchanged_semantic_state() -> None:
    runtime, _backend = make_runtime()
    session = runtime.start_session("change fixture", SessionConfig())
    previous = runtime.observe(session.session_id)
    current = runtime.observe(session.session_id)

    changes = runtime.observation_changes(
        session.session_id, previous.observation_id, current.observation_id
    )

    assert changes["unchanged"] is True
    assert changes["changed_fields"] == []
    assert previous.windows_sha256 == current.windows_sha256
    assert previous.ui_tree_sha256 == current.ui_tree_sha256


def test_stopping_session_invalidates_retained_observations() -> None:
    runtime, _backend = make_runtime()
    session = runtime.start_session("retained observation fixture", SessionConfig())
    observation = runtime.observe(session.session_id)

    runtime.set_session_state(session.session_id, SessionState.STOPPED)

    with pytest.raises(ValueError, match="unknown or expired"):
        runtime.observation(session.session_id, observation.observation_id)


def test_backend_failure_forces_input_release() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("failure fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={"target": CoordinateTarget(point=Point(x=-1, y=-1))}
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.FAILED
    assert backend.input_cancelled is True


def test_stale_observation_is_rejected() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("fixture test", SessionConfig(input_enabled=True))
    runtime.observe(session.session_id)
    latest = runtime.observe(session.session_id)

    result = runtime.execute(click_request(session.session_id, "old-observation"))

    assert result.status is ActionStatus.STALE_OBSERVATION
    assert result.observation_id is None
    assert backend.executed_actions == []
    assert latest.sequence == 2


def test_valid_click_executes_and_is_audited(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    runtime, backend = make_runtime(audit_path)
    session = runtime.start_session("fixture test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id)

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert result.error_code is None
    assert result.resolved_target == action.target
    assert result.backend_evidence["backend"] == "simulator"
    assert result.duration_ms is not None
    assert backend.executed_actions == [action]
    record = json.loads(audit_path.read_text())
    assert record["request"]["action_id"] == action.action_id
    assert len(record["entry_hash"]) == 64
    assert runtime.audit_summaries(session.session_id, 10) == (
        {
            "action_id": action.action_id,
            "kind": "click",
            "status": "completed",
            "message": "Action executed by simulator",
            "started_at": record["result"]["started_at"],
            "finished_at": record["result"]["finished_at"],
            "entry_hash": record["entry_hash"],
        },
    )


def test_action_precondition_stops_input_before_execution() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("precondition fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={
            "preconditions": (
                ActionCondition(
                    kind=ConditionKind.ACTIVE_APPLICATION,
                    value="different.app",
                ),
            )
        }
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.REJECTED
    assert result.error_code == "precondition_failed"
    assert result.data["preconditions"]["satisfied"] is False
    assert backend.executed_actions == []


def test_action_postcondition_returns_follow_up_observation() -> None:
    runtime, _backend = make_runtime()
    session = runtime.start_session("postcondition fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={
            "postconditions": (
                ActionCondition(
                    kind=ConditionKind.ELEMENT_PRESENT,
                    value="Create",
                    role="button",
                ),
            )
        }
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert result.next_observation_id is not None
    assert result.postcondition_result is not None
    assert result.postcondition_result.satisfied is True
    assert result.postcondition_result.observation_id == result.next_observation_id


def test_idempotency_replays_completed_result_without_repeating_input() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("idempotency fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={"idempotency": IdempotencyClass.IDEMPOTENT}
    )

    first = runtime.execute(action)
    replay = runtime.execute(action)

    assert first.status is ActionStatus.COMPLETED
    assert replay.status is ActionStatus.COMPLETED
    assert replay.data["idempotent_replay"] is True
    assert backend.executed_actions == [action]


def test_non_idempotent_duplicate_is_rejected() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("duplicate fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={"idempotency": IdempotencyClass.NON_IDEMPOTENT}
    )

    assert runtime.execute(action).status is ActionStatus.COMPLETED
    duplicate = runtime.execute(action)

    assert duplicate.status is ActionStatus.REJECTED
    assert duplicate.error_code == "duplicate_action"
    assert backend.executed_actions == [action]


def test_action_id_cannot_replay_a_changed_idempotent_request() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("action ID fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={"idempotency": IdempotencyClass.IDEMPOTENT}
    )
    changed = action.model_copy(
        update={"target": CoordinateTarget(point=Point(x=400, y=300))}
    )

    assert runtime.execute(action).status is ActionStatus.COMPLETED
    conflict = runtime.execute(changed)

    assert conflict.status is ActionStatus.REJECTED
    assert conflict.error_code == "action_id_conflict"
    assert backend.executed_actions == [action]


def test_deadline_is_enforced_before_backend_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("deadline fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    clock = iter((0.0, 2.0))
    monkeypatch.setattr("nimbledesk.daemon.runtime.monotonic", lambda: next(clock))
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={"deadline_ms": 1}
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.TIMED_OUT
    assert result.error_code == "deadline_exceeded"
    assert backend.executed_actions == []


def test_only_explicitly_idempotent_actions_retry_backend_failures() -> None:
    class FlakyBackend(SimulatorBackend):
        def execute(self, request: ActionRequest) -> ActionResult:
            if not self.executed_actions:
                self.executed_actions.append(request)
                now = 1.0
                return ActionResult(
                    action_id=request.action_id,
                    status=ActionStatus.FAILED,
                    message="temporary failure",
                    started_at=now,
                    finished_at=now,
                )
            return super().execute(request)

    backend = FlakyBackend()
    runtime = DesktopRuntime(
        backend,
        SessionManager(),
        ActionPolicy(host_input_enabled=True),
        ApprovalManager(),
        AuditLog(),
    )
    session = runtime.start_session("retry fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id).model_copy(
        update={
            "idempotency": IdempotencyClass.IDEMPOTENT,
            "maximum_retries": 1,
        }
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert result.data["execution_attempts"] == 2
    assert backend.executed_actions == [action, action]


def test_emergency_stop_stops_sessions_releases_input_and_revokes_approvals() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("fixture test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    pending = runtime.execute(
        ActionRequest(
            session_id=session.session_id,
            source_observation_id=observation.observation_id,
            kind=ActionKind.APP_COMMAND,
            arguments={"adapter_id": "fixture", "command": "save", "arguments": {}},
        )
    )

    stopped = runtime.emergency_stop()

    assert stopped[0].state is SessionState.STOPPED
    assert runtime.list_sessions()[0].state is SessionState.STOPPED
    assert backend.input_cancelled is True
    assert pending.approval_id is not None
    assert runtime.approval_status(pending.approval_id).status == "rejected"


def test_audit_hash_chain_survives_daemon_restart_and_detects_tampering(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    runtime, _backend = make_runtime(audit_path)
    session = runtime.start_session("fixture test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = click_request(session.session_id, observation.observation_id)
    first_result = runtime.execute(action)

    reopened = AuditLog(audit_path)
    second_action = action.model_copy(update={"action_id": "second-action"})
    second_result = first_result.model_copy(update={"action_id": "second-action"})
    reopened.record(second_action, second_result)
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]

    assert records[1]["previous_hash"] == records[0]["entry_hash"]
    assert reopened.integrity()["valid"] is True

    records[0]["result"]["message"] = "tampered"
    audit_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    with pytest.raises(RuntimeError, match="audit log integrity failed at line 1"):
        AuditLog(audit_path)


def test_clipboard_and_launch_flow_through_policy_approval_and_audit(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    backend = SimulatorBackend()
    approvals = ApprovalManager()
    runtime = DesktopRuntime(
        backend=backend,
        sessions=SessionManager(),
        policy=ActionPolicy(
            host_input_enabled=True,
            host_clipboard_enabled=True,
        ),
        approvals=approvals,
        audit=AuditLog(audit_path),
    )
    session = runtime.start_session(
        "edit",
        SessionConfig(
            input_enabled=True,
            clipboard_enabled=True,
            allowed_applications=frozenset({"com.example.Editor"}),
        ),
    )
    observation = runtime.observe(session.session_id)
    write = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.WRITE_CLIPBOARD,
        arguments={"text": "private clipboard"},
    )
    pending = runtime.execute(write)
    assert pending.status is ActionStatus.CONFIRMATION_REQUIRED
    assert pending.approval_id is not None
    with pytest.raises(ValueError, match="clipboard writes require exact approval"):
        runtime.approve_temporary(pending.approval_id, 300, 2)
    token = approvals.approve(str(pending.approval_id))
    written = runtime.execute(write.model_copy(update={"approval_token": token}))
    read = runtime.execute(
        ActionRequest(
            session_id=session.session_id,
            source_observation_id=observation.observation_id,
            kind=ActionKind.READ_CLIPBOARD,
            arguments={"maximum_characters": 7},
        )
    )
    launch = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.LAUNCH_APPLICATION,
        arguments={"application_id": "com.example.Editor"},
    )
    launch_pending = runtime.execute(launch)
    launch_token = approvals.approve(str(launch_pending.approval_id))
    launched = runtime.execute(
        launch.model_copy(update={"approval_token": launch_token})
    )

    assert written.status is ActionStatus.COMPLETED
    assert read.data["text"] == "private"
    assert read.data["truncated"] is True
    assert launched.status is ActionStatus.COMPLETED
    assert backend.launched_applications == ["com.example.Editor"]
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "private clipboard" not in audit_text
    assert "\"text\": \"[REDACTED]\"" in audit_text


def test_semantic_element_click_is_observation_bound() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("semantic fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        expected_window_id="fixture-window",
        kind=ActionKind.CLICK,
        target=ElementTarget(
            observation_id=observation.observation_id,
            element_id="fixture-create",
        ),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert backend.executed_actions == [action]


def test_target_resolve_converts_unique_selector_without_input() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("resolve fixture", SessionConfig())
    observation = runtime.observe(session.session_id)

    result = runtime.resolve_target(
        session.session_id,
        observation.observation_id,
        SelectorTarget(role="button", name="Create"),
    )

    assert result["target"] == {
        "target_type": "element",
        "observation_id": observation.observation_id,
        "element_id": "fixture-create",
    }
    assert result["evidence"]["name"] == "Create"
    assert backend.executed_actions == []


def test_creative_paths_must_stay_inside_session_grants(tmp_path: Path) -> None:
    granted = tmp_path / "project"
    granted.mkdir()
    runtime, _backend = make_runtime()
    session = runtime.start_session(
        "creative fixture", SessionConfig(granted_paths=(str(granted),))
    )

    runtime.authorize_paths(
        session.session_id, (granted / "source.mp4", granted / "outputs")
    )

    with pytest.raises(ValueError, match="outside session grants"):
        runtime.authorize_paths(session.session_id, (tmp_path / "other.mp4",))


def test_visual_click_recaptures_signature_and_executes_at_crop_center() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("visual fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    bounds = Rectangle(left=140, top=210, width=120, height=40)
    signature = runtime.capture(
        session.session_id,
        observation.observation_id,
        bounds,
        CaptureOptions(image_format="png", max_width=120, max_height=64),
    ).sha256
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        expected_window_id="fixture-window",
        kind=ActionKind.CLICK,
        target=VisualTarget(
            observation_id=observation.observation_id,
            bounds=bounds,
            signature=signature,
            confidence=0.9,
        ),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert backend.executed_actions[0].target == CoordinateTarget(
        point=Point(x=200, y=230)
    )
    assert result.data["visual_signature_actual"] == signature
    assert result.data["visual_target_point"] == {"x": 200, "y": 230}


def test_visual_click_rejects_changed_pixels_before_input() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("visual fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.CLICK,
        target=VisualTarget(
            observation_id=observation.observation_id,
            bounds=Rectangle(left=10, top=10, width=80, height=80),
            signature="0" * 64,
            confidence=0.95,
        ),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.STALE_OBSERVATION
    assert "pixels changed" in result.message
    assert backend.executed_actions == []


def test_ocr_click_resolves_unambiguous_text_inside_focused_window() -> None:
    class FixtureOcr:
        available = True

        def locate(self, image: bytes, query: str, exact: bool) -> tuple[OcrMatch, ...]:
            assert image
            assert query == "Create project"
            assert exact
            return (
                OcrMatch(
                    text="Create project",
                    bounds=Rectangle(left=40, top=100, width=120, height=40),
                    confidence=0.94,
                ),
            )

    backend = SimulatorBackend()
    runtime = DesktopRuntime(
        backend,
        SessionManager(),
        ActionPolicy(host_input_enabled=True),
        ApprovalManager(),
        AuditLog(),
        ocr_provider=FixtureOcr(),
    )
    session = runtime.start_session("OCR fixture", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        expected_window_id="fixture-window",
        kind=ActionKind.CLICK,
        target=TextTarget(
            observation_id=observation.observation_id,
            text="Create project",
            exact=True,
        ),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert backend.executed_actions[0].target == CoordinateTarget(
        point=Point(x=200, y=220)
    )
    assert result.data["targeting_method"] == "local_ocr"
    assert result.data["ocr_confidence"] == 0.94


def test_stale_semantic_target_can_be_reobserved_and_revalidated() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("semantic recovery", SessionConfig(input_enabled=True))
    original = runtime.observe(session.session_id)
    runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=original.observation_id,
        expected_application_id="fixture.app",
        expected_window_id="fixture-window",
        kind=ActionKind.CLICK,
        target=ElementTarget(
            observation_id=original.observation_id,
            element_id="fixture-create",
        ),
        recovery=RecoveryOptions(max_reobservations=1),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.COMPLETED
    assert result.data["recovery_classification"] == "stale_target_revalidated"
    assert result.data["reobservations"] == 1
    assert backend.executed_actions[0].source_observation_id != original.observation_id


def test_recovery_stops_when_application_changes() -> None:
    class FocusChangingBackend(SimulatorBackend):
        observations = 0

        def observe(self):  # type: ignore[no-untyped-def]
            observation = super().observe()
            self.observations += 1
            if self.observations >= 3:
                return observation.model_copy(update={"active_application_id": "dialog.app"})
            return observation

    backend = FocusChangingBackend()
    runtime = DesktopRuntime(
        backend,
        SessionManager(),
        ActionPolicy(host_input_enabled=True),
        ApprovalManager(),
        AuditLog(),
    )
    session = runtime.start_session("focus recovery", SessionConfig(input_enabled=True))
    original = runtime.observe(session.session_id)
    runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=original.observation_id,
        expected_application_id="fixture.app",
        kind=ActionKind.CLICK,
        target=ElementTarget(
            observation_id=original.observation_id,
            element_id="fixture-create",
        ),
        recovery=RecoveryOptions(max_reobservations=1),
    )

    result = runtime.execute(action)

    assert result.status is ActionStatus.STALE_OBSERVATION
    assert "active application changed" in result.message
    assert backend.executed_actions == []


def test_application_command_approval_is_exact_and_single_use() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("adapter test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={"adapter": "video", "command": "render"},
    )

    pending = runtime.execute(action)
    assert pending.status is ActionStatus.CONFIRMATION_REQUIRED
    assert pending.approval_id is not None
    approval = runtime.list_approvals()[0]
    assert approval.evidence is not None
    assert approval.evidence.observation_id == observation.observation_id
    assert approval.evidence.mime_type == "image/jpeg"
    assert len(approval.evidence.sha256) == 64
    assert approval.evidence.width <= 640
    assert approval.evidence.height <= 400

    token = runtime.approve(pending.approval_id)
    assert runtime.approval_status(pending.approval_id).status == "approved"
    approved_action = action.model_copy(update={"approval_token": token})
    assert runtime.execute(approved_action).status is ActionStatus.COMPLETED
    assert runtime.approval_status(pending.approval_id).status == "consumed"
    assert runtime.execute(approved_action).status is ActionStatus.CONFIRMATION_REQUIRED
    assert len(backend.executed_actions) == 1


def test_temporary_approval_is_scoped_bounded_and_revocable() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("adapter test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        expected_application_id="fixture.app",
        expected_window_id="fixture-window",
        kind=ActionKind.APP_COMMAND,
        arguments={
            "adapter_id": "video",
            "command": "set_color",
            "arguments": {"look": "warm"},
        },
    )
    pending = runtime.execute(action)
    assert pending.approval_id is not None
    rule = runtime.approve_temporary(pending.approval_id, 300, 2)

    first = runtime.execute(action)
    second = runtime.execute(
        action.model_copy(
            update={
                "action_id": "second-scoped-action",
                "arguments": {
                    **action.arguments,
                    "arguments": {"look": "cool"},
                },
            }
        )
    )
    third = runtime.execute(action.model_copy(update={"action_id": "third-scoped-action"}))

    assert first.status is ActionStatus.COMPLETED
    assert second.status is ActionStatus.COMPLETED
    assert third.status is ActionStatus.CONFIRMATION_REQUIRED
    assert len(backend.executed_actions) == 2
    assert runtime.approval_rules() == ()
    assert rule["description"] == (
        "app_command, application=fixture.app, window=fixture-window, "
        "adapter=video, command=set_color"
    )
    assert third.approval_id is not None
    replacement = runtime.approve_temporary(third.approval_id, 300, 2)
    runtime.revoke_approval_rule(str(replacement["rule_id"]))
    fourth = runtime.execute(action.model_copy(update={"action_id": "fourth-scoped-action"}))
    assert fourth.status is ActionStatus.CONFIRMATION_REQUIRED


def test_duplicate_pending_application_command_reuses_queue_item() -> None:
    runtime, _backend = make_runtime()
    session = runtime.start_session("adapter test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={"adapter_id": "video", "command": "render"},
    )

    first = runtime.execute(action)
    second = runtime.execute(action)

    assert first.approval_id == second.approval_id
    assert len(runtime.list_approvals()) == 1


def test_application_command_can_be_rejected_and_polled() -> None:
    runtime, _backend = make_runtime()
    session = runtime.start_session("adapter test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={"adapter_id": "video", "command": "render"},
    )

    pending = runtime.execute(action)
    assert pending.approval_id is not None
    assert runtime.approval_status(pending.approval_id).status == "pending"
    runtime.reject_approval(pending.approval_id)

    assert runtime.approval_status(pending.approval_id).status == "rejected"


def test_pause_releases_input_and_blocks_actions() -> None:
    runtime, backend = make_runtime()
    session = runtime.start_session("fixture test", SessionConfig(input_enabled=True))
    observation = runtime.observe(session.session_id)

    runtime.set_session_state(session.session_id, SessionState.PAUSED)
    result = runtime.execute(click_request(session.session_id, observation.observation_id))

    assert backend.input_cancelled
    assert result.status is ActionStatus.REJECTED
