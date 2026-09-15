import platform
from pathlib import Path

import pytest

from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import AdapterError, IsolatedAdapterRunner
from nimbledesk.backends.adapters import AdapterDesktopBackend, AdapterRegistry
from nimbledesk.backends.simulator import SimulatorBackend
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.protocol.models import ActionKind, ActionRequest, ActionStatus, SessionConfig


def _manifest() -> AdapterManifest:
    return AdapterManifest(
        adapter_id="nimbledesk.fixture",
        version="1.0.0",
        vendor="NimbleDesk",
        entrypoint="nimbledesk.adapters.fixture:handle",
        supported_platforms=frozenset({platform.system()}),
        commands={
            "inspect": AdapterCommand(
                risk="observe",
                read_only=True,
                required_arguments=("project",),
                path_arguments=("project",),
            ),
            "sleep": AdapterCommand(
                risk="low",
                timeout_seconds=0.1,
                required_arguments=("seconds",),
            ),
        },
    )


def test_adapter_runs_in_worker_with_granted_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = IsolatedAdapterRunner().execute(
        _manifest(),
        "inspect",
        {"project": str(project)},
        granted_paths=(tmp_path,),
    )

    assert result.success
    assert result.result == {"received": {"project": str(project)}}


def test_adapter_rejects_path_outside_session_grants(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="outside session grants"):
        IsolatedAdapterRunner().execute(
            _manifest(),
            "inspect",
            {"project": str(tmp_path)},
            granted_paths=(tmp_path / "different-root",),
        )


def test_adapter_timeout_terminates_worker() -> None:
    with pytest.raises(AdapterError, match="timed out"):
        IsolatedAdapterRunner().execute(
            _manifest(),
            "sleep",
            {"seconds": 5},
        )


def test_adapter_command_runs_after_exact_approval_with_session_paths(
    tmp_path: Path,
) -> None:
    backend = AdapterDesktopBackend(
        SimulatorBackend(), AdapterRegistry({_manifest().adapter_id: _manifest()})
    )
    approvals = ApprovalManager()
    runtime = DesktopRuntime(
        backend=backend,
        sessions=SessionManager(),
        policy=ActionPolicy(host_input_enabled=True),
        approvals=approvals,
        audit=AuditLog(),
    )
    session = runtime.start_session(
        "adapter fixture",
        SessionConfig(input_enabled=True, granted_paths=(str(tmp_path),)),
    )
    observation = runtime.observe(session.session_id)
    action = ActionRequest(
        session_id=session.session_id,
        source_observation_id=observation.observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={
            "adapter_id": "nimbledesk.fixture",
            "command": "inspect",
            "arguments": {"project": str(tmp_path)},
        },
    )

    pending = runtime.execute(action)
    assert pending.status is ActionStatus.CONFIRMATION_REQUIRED
    assert pending.approval_id is not None
    token = approvals.approve(pending.approval_id)
    result = runtime.execute(action.model_copy(update={"approval_token": token}))

    assert result.status is ActionStatus.COMPLETED
    assert result.data["result"] == {"received": {"project": str(tmp_path)}}
