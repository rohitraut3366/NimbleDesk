import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

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
