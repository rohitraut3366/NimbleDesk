import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

import nimbledesk.console.safety as safety
from nimbledesk.client import DaemonClient
from nimbledesk.console.safety import (
    SafetyController,
    SafetySettings,
    load_settings,
    save_settings,
)


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
        self.calls.append((method, params or {}))
        if method == "session_list":
            return {
                "sessions": [
                    {"session_id": "active", "state": "active"},
                    {"session_id": "paused", "state": "paused"},
                ]
            }
        if method == "emergency_stop":
            return {"status": "stopped", "stopped_sessions": 2}
        return {"state": "paused"}


def test_safety_settings_are_validated_and_saved_atomically(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = SafetySettings(pause_hotkey="<ctrl>+<shift>+x")

    save_settings(path, settings)

    assert load_settings(path) == settings
    with pytest.raises(ValidationError, match="hotkey must use"):
        SafetySettings(pause_hotkey="control shift x")


def test_global_pause_and_tray_stop_use_authenticated_daemon_calls(tmp_path: Path) -> None:
    client = RecordingClient()
    status_path = tmp_path / "safety-status.json"
    controller = SafetyController(
        lambda: cast(DaemonClient, client),
        status_path,
        "<ctrl>+<alt>+<shift>+p",
    )

    controller.pause_all()
    controller.stop_all()

    assert client.calls == [
        ("session_list", {}),
        ("session_set_state", {"session_id": "active", "state": "paused"}),
        ("emergency_stop", {}),
    ]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["last_action"]["action"] == "emergency_stop"
    assert status["pause_hotkey"] == "<ctrl>+<alt>+<shift>+p"


def test_wayland_uses_the_compositor_global_shortcut_portal(
    monkeypatch: MonkeyPatch,
) -> None:
    created: list[tuple[str, object]] = []
    portal_listener = object()

    def make_listener(hotkey: str, action: object) -> object:
        created.append((hotkey, action))
        return portal_listener

    monkeypatch.setattr(safety.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(safety, "WaylandGlobalShortcut", make_listener)

    listener = safety._hotkey_listener("<ctrl>+<alt>+p", lambda: None)

    assert listener is portal_listener
    assert created[0][0] == "<ctrl>+<alt>+p"


def test_tray_remains_available_when_hotkey_registration_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    tray_runs: list[str] = []

    class FailingListener:
        def start(self) -> None:
            raise RuntimeError("global shortcut portal unavailable")

        def stop(self) -> None:
            raise AssertionError("a listener that did not start must not be stopped")

    class FakeIcon:
        def __init__(self, *_args: object) -> None:
            pass

        def run(self) -> None:
            tray_runs.append("run")

        def stop(self) -> None:
            pass

    fake_pystray = SimpleNamespace(
        Icon=FakeIcon,
        Menu=lambda *items: items,
        MenuItem=lambda *arguments: arguments,
    )
    monkeypatch.setattr(safety, "_hotkey_listener", lambda *_args: FailingListener())
    monkeypatch.setattr(safety, "import_module", lambda _name: fake_pystray)
    status_path = tmp_path / "safety-status.json"

    safety.run_console(
        SafetySettings(),
        lambda: cast(DaemonClient, RecordingClient()),
        status_path,
    )

    assert tray_runs == ["run"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "degraded"
    assert status["hotkey_error"] == "global shortcut portal unavailable"
