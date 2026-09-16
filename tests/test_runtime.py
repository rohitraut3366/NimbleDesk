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
    ActionKind,
    ActionRequest,
    ActionStatus,
    CaptureOptions,
    CoordinateTarget,
    ElementTarget,
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

    token = runtime.approve(pending.approval_id)
    assert runtime.approval_status(pending.approval_id).status == "approved"
    approved_action = action.model_copy(update={"approval_token": token})
    assert runtime.execute(approved_action).status is ActionStatus.COMPLETED
    assert runtime.approval_status(pending.approval_id).status == "consumed"
    assert runtime.execute(approved_action).status is ActionStatus.CONFIRMATION_REQUIRED
    assert len(backend.executed_actions) == 1


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
