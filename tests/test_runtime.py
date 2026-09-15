import json
from pathlib import Path

from nimbledesk.backends import SimulatorBackend
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    CoordinateTarget,
    ElementTarget,
    Point,
    SessionConfig,
    SessionState,
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
