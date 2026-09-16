from pathlib import Path

from pytest import MonkeyPatch

import nimbledesk.daemon.server as server
from nimbledesk.backends.semantic import UnavailableSemanticProvider
from nimbledesk.backends.simulator import SimulatorBackend


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

    def macos_backend() -> SimulatorBackend:
        selected.append("macos")
        return SimulatorBackend()

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
