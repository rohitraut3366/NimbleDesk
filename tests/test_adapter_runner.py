import platform
from pathlib import Path

import pytest
from pydantic import ValidationError

from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import (
    AdapterError,
    IsolatedAdapterRunner,
    _macos_sandbox_profile,
    _sandboxed_worker_command,
    _strict_adapter_result,
)
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
            "huge": AdapterCommand(risk="observe", read_only=True),
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


def test_adapter_rejects_oversized_worker_result() -> None:
    with pytest.raises(AdapterError, match="one-megabyte"):
        IsolatedAdapterRunner().execute(_manifest(), "huge", {})


def test_adapter_rejects_duplicate_json_keys() -> None:
    payload = b'{"success":true,"success":false,"result":{},"error":null}'

    with pytest.raises(AdapterError, match="duplicate key"):
        _strict_adapter_result(payload)


def test_adapter_rejects_symlinked_path_arguments(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is unavailable")

    with pytest.raises(AdapterError, match="symbolic link"):
        IsolatedAdapterRunner().execute(
            _manifest(),
            "inspect",
            {"project": str(linked)},
            granted_paths=(tmp_path,),
        )


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS sandbox-exec")
def test_adapter_runs_inside_macos_sandbox(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _manifest().model_copy(update={"isolation": "sandboxed"})

    result = IsolatedAdapterRunner().execute(
        manifest,
        "inspect",
        {"project": str(project)},
        granted_paths=(tmp_path,),
    )

    assert result.success
    assert result.result == {"received": {"project": str(project)}}


def test_linux_sandbox_disables_network_and_binds_only_declared_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    manifest = _manifest().model_copy(
        update={
            "isolation": "sandboxed",
            "writable_path_arguments": ("project",),
        }
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("nimbledesk.adapters.runner.shutil.which", lambda _name: "/usr/bin/bwrap")

    command = _sandboxed_worker_command(
        ["python", "worker.py"],
        manifest,
        {"project": str(output)},
        (tmp_path,),
        tmp_path / "scratch",
    )

    assert command[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in command
    bind_index = command.index(str(output))
    assert command[bind_index - 1] == "--bind"
    assert command[-3:] == ["--", "python", "worker.py"]


def test_macos_sandbox_profile_limits_reads_and_network(tmp_path: Path) -> None:
    granted = tmp_path / "granted"
    writable = granted / "output"
    scratch = tmp_path / "scratch"
    profile = _macos_sandbox_profile((granted,), (writable,), scratch, False)

    assert f'(subpath "{granted}")' in profile
    assert f'(allow file-write* (subpath "{writable}"))' in profile
    assert "(allow network*)" not in profile


def test_manifest_rejects_undeclared_writable_path_argument() -> None:
    with pytest.raises(ValidationError, match="must be declared path arguments"):
        AdapterManifest(
            adapter_id="nimbledesk.invalid",
            version="1.0.0",
            vendor="NimbleDesk",
            entrypoint="nimbledesk.adapters.fixture:handle",
            supported_platforms=frozenset({platform.system()}),
            commands={"inspect": AdapterCommand(risk="observe", read_only=True)},
            isolation="sandboxed",
            writable_path_arguments=("output",),
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
