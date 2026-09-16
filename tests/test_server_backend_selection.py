from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

import nimbledesk.daemon.server as server
from nimbledesk.backends.semantic import UnavailableSemanticProvider
from nimbledesk.backends.simulator import SimulatorBackend


class RecordingProcess:
    def __init__(self, command: list[str], **options: Any) -> None:
        self.command = command
        self.options = options


def test_native_backend_uses_portal_capture_in_wayland_session(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    selected: list[str] = []

    def portal_backend() -> SimulatorBackend:
        selected.append("portal")
        return SimulatorBackend()

    monkeypatch.setenv("NIMBLEDESK_BACKEND", "native")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("NIMBLEDESK_ADAPTER_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(server.platform, "system", lambda: "Linux")
    monkeypatch.setattr(server, "WaylandPortalBackend", portal_backend)
    monkeypatch.setattr(
        server,
        "system_semantic_provider",
        lambda: UnavailableSemanticProvider("fixture"),
    )
    monkeypatch.setattr(
        server,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    runtime = server.build_runtime(tmp_path)

    assert selected == ["portal"]
    assert runtime.health()["backend"] == "adapters+native:unavailable+simulator"


def test_native_backend_uses_macos_system_io(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    selected: list[str] = []
    native_backend = SimulatorBackend()

    def macos_backend() -> SimulatorBackend:
        selected.append("macos")
        return native_backend

    monkeypatch.setenv("NIMBLEDESK_BACKEND", "native")
    monkeypatch.setenv("NIMBLEDESK_ADAPTER_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(server, "MacOSNativeBackend", macos_backend)
    monkeypatch.setattr(
        server,
        "system_semantic_provider",
        lambda: UnavailableSemanticProvider("fixture"),
    )
    monkeypatch.setattr(
        server,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    runtime = server.build_runtime(tmp_path)

    assert selected == ["macos"]
    assert native_backend.input_cancelled is True
    assert runtime.health()["backend"] == "adapters+native:unavailable+simulator"


def test_native_backend_uses_windows_system_io(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    selected: list[str] = []

    def windows_backend() -> SimulatorBackend:
        selected.append("windows")
        return SimulatorBackend()

    monkeypatch.setenv("NIMBLEDESK_BACKEND", "native")
    monkeypatch.setenv("NIMBLEDESK_ADAPTER_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(server.platform, "system", lambda: "Windows")
    monkeypatch.setattr(server, "WindowsNativeBackend", windows_backend)
    monkeypatch.setattr(
        server,
        "system_semantic_provider",
        lambda: UnavailableSemanticProvider("fixture"),
    )
    monkeypatch.setattr(
        server,
        "import_module",
        lambda name: (_ for _ in ()).throw(AssertionError(f"unexpected import: {name}")),
    )

    runtime = server.build_runtime(tmp_path)

    assert selected == ["windows"]
    assert runtime.health()["backend"] == "adapters+native:unavailable+simulator"


def test_daemon_starts_safety_console_with_parent_liveness_pipe(
    monkeypatch: MonkeyPatch,
) -> None:
    processes: list[RecordingProcess] = []

    def start_process(command: list[str], **options: Any) -> RecordingProcess:
        process = RecordingProcess(command, **options)
        processes.append(process)
        return process

    monkeypatch.delenv("NIMBLEDESK_SAFETY_CONSOLE", raising=False)
    monkeypatch.setattr(server.subprocess, "Popen", start_process)

    process = server._start_safety_console()

    assert process is processes[0]
    assert process.command == [
        server.sys.executable,
        "-m",
        "nimbledesk.console.safety",
        "--parent-pipe",
    ]
    assert process.options["stdin"] == server.subprocess.PIPE
    assert process.options["stdout"] == server.subprocess.DEVNULL
    assert process.options["stderr"] == server.subprocess.DEVNULL


def test_daemon_can_disable_safety_console(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBLEDESK_SAFETY_CONSOLE", "0")
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        lambda *_args, **_options: (_ for _ in ()).throw(
            AssertionError("disabled console must not start")
        ),
    )

    assert server._start_safety_console() is None
