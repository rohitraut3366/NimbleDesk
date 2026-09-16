import json
import platform
from pathlib import Path

import pytest
from pydantic import ValidationError

from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import (
    AdapterError,
    IsolatedAdapterRunner,
    _adapter_worker_command,
    _macos_sandbox_profile,
    _sandbox_path,
    _sandboxed_worker_command,
    _strict_adapter_result,
    _windows_appcontainer_paths,
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
            "windows_security": AdapterCommand(risk="observe", read_only=True),
            "spawn_child": AdapterCommand(risk="observe", read_only=True),
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


def test_adapter_catalog_hides_local_package_paths() -> None:
    backend = AdapterDesktopBackend(
        SimulatorBackend(), AdapterRegistry({_manifest().adapter_id: _manifest()})
    )

    descriptions = backend.adapter_descriptions()

    assert descriptions[0]["adapter_id"] == "nimbledesk.fixture"
    assert "package_path" not in descriptions[0]
    assert descriptions[0]["commands"]["inspect"]["read_only"] is True


def test_frozen_bundle_uses_internal_adapter_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nimbledesk.adapters.runner.sys.frozen", True, raising=False)

    command = _adapter_worker_command("example.adapter:handle")

    assert command[1:] == ["adapter-worker", "example.adapter:handle"]


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


def test_adapter_worker_cannot_create_child_processes() -> None:
    result = IsolatedAdapterRunner().execute(_manifest(), "spawn_child", {})

    assert result.success
    assert result.result["child_process_created"] is False


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
    scratch = tmp_path / "scratch"
    scratch.mkdir()
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
        scratch,
    )

    assert command[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in command
    assert "--share-net" not in command
    assert command[command.index("--tmpfs") + 1] == "/"
    triples = [command[index : index + 3] for index in range(len(command))]
    assert ["--ro-bind", "/", "/"] not in triples
    assert ["--ro-bind", str(tmp_path.resolve()), str(tmp_path.resolve())] in triples
    assert ["--bind", str(output), str(output)] in triples
    assert command[-3:] == ["--", "python", "worker.py"]


def test_linux_sandbox_only_shares_network_when_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest().model_copy(
        update={"isolation": "sandboxed", "network_access": True}
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("nimbledesk.adapters.runner.shutil.which", lambda _name: "/usr/bin/bwrap")

    command = _sandboxed_worker_command(
        ["python", "worker.py"], manifest, {}, (), scratch
    )

    assert "--unshare-all" in command
    assert "--share-net" in command


def test_windows_sandbox_uses_restricted_process_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    manifest = _manifest().model_copy(update={"isolation": "sandboxed"})
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    command = _sandboxed_worker_command(
        ["python", "worker.py"], manifest, {}, (), scratch
    )

    assert command == ["python", "worker.py"]


def test_windows_appcontainer_paths_separate_read_and_write_grants(tmp_path: Path) -> None:
    executable = tmp_path / "nimbledesk.exe"
    executable.touch()
    granted = tmp_path / "media"
    granted.mkdir()
    package = tmp_path / "adapter-package"
    package.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    manifest = _manifest().model_copy(
        update={
            "package_path": package,
            "writable_path_arguments": ("project",),
        }
    )

    readable, writable = _windows_appcontainer_paths(
        [str(executable), "adapter-worker"],
        manifest,
        {"project": str(output)},
        (granted,),
        scratch,
    )

    assert readable == (executable.resolve(), granted.resolve(), package.resolve())
    assert writable == (scratch.resolve(), output.resolve())


def test_frozen_windows_adapter_uses_appcontainer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CompletedProcess:
        stdin = None
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("completed process must not be terminated")

        def kill(self) -> None:
            raise AssertionError("completed process must not be killed")

        def close(self) -> None:
            return None

    def fake_start(command: list[str], **keywords: object) -> CompletedProcess:
        captured.update(keywords)
        stdout_file = keywords["stdout_file"]
        stdout_file.write(b'{"success":true,"result":{"isolated":true},"error":null}')
        return CompletedProcess()

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr("nimbledesk.adapters.runner.sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "nimbledesk.adapters.windows_appcontainer.start_windows_appcontainer_process",
        fake_start,
    )
    manifest = _manifest().model_copy(
        update={"supported_platforms": frozenset({"Windows"}), "isolation": "sandboxed"}
    )

    result = IsolatedAdapterRunner().execute(manifest, "windows_security", {})

    assert result.result == {"isolated": True}
    assert captured["network_access"] is False
    assert Path(str(captured["cwd"])).resolve() in captured["writable_paths"]


@pytest.mark.skipif(platform.system() != "Windows", reason="requires Windows security APIs")
def test_adapter_runs_inside_windows_restricted_job() -> None:
    manifest = _manifest().model_copy(update={"isolation": "sandboxed"})

    result = IsolatedAdapterRunner().execute(manifest, "windows_security", {})

    assert result.success
    assert result.result["child_process_created"] is False
    assert set(result.result["enabled_privileges"]) <= {"SeChangeNotifyPrivilege"}


def test_macos_sandbox_profile_limits_reads_and_network(tmp_path: Path) -> None:
    granted = tmp_path / "granted"
    writable = granted / "output"
    scratch = tmp_path / "scratch"
    writable.mkdir(parents=True)
    scratch.mkdir()
    executable = tmp_path / "nimbledesk"
    executable.write_bytes(b"executable")
    profile = _macos_sandbox_profile((granted, executable), (writable,), scratch, False)

    assert f'(subpath "{_sandbox_path(granted)}")' in profile
    assert (
        f'(allow file-read* file-map-executable (literal "{_sandbox_path(executable)}"))'
    ) in profile
    assert (
        f'(allow file-read-data (literal "{_sandbox_path(executable.parent)}"))'
    ) in profile
    assert "(allow ipc-sysv-sem)" in profile
    assert (
        f'(allow file-write* (literal "{_sandbox_path(writable)}") '
        f'(subpath "{_sandbox_path(writable)}"))'
    ) in profile
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


def test_external_adapter_requires_explicit_package_root() -> None:
    with pytest.raises(ValidationError, match="external adapters require package_path"):
        AdapterManifest(
            adapter_id="example.external",
            version="1.0.0",
            vendor="Example",
            entrypoint="example_adapter:handle",
            supported_platforms=frozenset({platform.system()}),
            commands={"inspect": AdapterCommand(risk="observe", read_only=True)},
        )


def test_registry_resolves_and_worker_imports_external_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "example_adapter.py").write_text(
        "def handle(command, arguments):\n"
        "    return {'command': command, 'value': arguments['value']}\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "example.json"
    manifest_path.write_text(
        json.dumps(
            {
                "adapter_id": "example.external",
                "version": "1.0.0",
                "vendor": "Example",
                "entrypoint": "example_adapter:handle",
                "package_path": "package",
                "supported_platforms": [platform.system()],
                "commands": {
                    "inspect": {
                        "risk": "observe",
                        "read_only": True,
                        "required_arguments": ["value"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = AdapterRegistry.load(tmp_path).manifests["example.external"]
    result = IsolatedAdapterRunner().execute(manifest, "inspect", {"value": "loaded"})

    assert manifest.package_path == package.resolve()
    assert result.result == {"command": "inspect", "value": "loaded"}


def test_sandbox_includes_external_adapter_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "adapter-package"
    package.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    manifest = AdapterManifest(
        adapter_id="example.external",
        version="1.0.0",
        vendor="Example",
        entrypoint="example_adapter:handle",
        package_path=package,
        supported_platforms=frozenset({"Linux"}),
        commands={"inspect": AdapterCommand(risk="observe", read_only=True)},
        isolation="sandboxed",
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("nimbledesk.adapters.runner.shutil.which", lambda _name: "/usr/bin/bwrap")

    command = _sandboxed_worker_command(
        ["python", "worker.py"], manifest, {}, (), scratch
    )

    triples = [command[index : index + 3] for index in range(len(command))]
    assert ["--ro-bind", str(package.resolve()), str(package.resolve())] in triples


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
