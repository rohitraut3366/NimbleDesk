from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nimbledesk.client import DaemonClient

DEFAULT_PAUSE_HOTKEY = "<ctrl>+<alt>+<shift>+p"
HOTKEY_TOKEN = re.compile(r"^(?:<[a-z0-9_]+>|[a-z0-9])$")


class SafetySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pause_hotkey: str = Field(default=DEFAULT_PAUSE_HOTKEY, min_length=1, max_length=200)

    @field_validator("pause_hotkey")
    @classmethod
    def valid_hotkey(cls, value: str) -> str:
        tokens = value.casefold().split("+")
        if len(tokens) < 2 or any(not HOTKEY_TOKEN.fullmatch(token) for token in tokens):
            raise ValueError("hotkey must use pynput syntax such as <ctrl>+<alt>+p")
        return value.casefold()


class HotkeyListener(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class TrayIcon(Protocol):
    def run(self) -> None: ...

    def stop(self) -> None: ...


class SafetyController:
    def __init__(
        self,
        client_factory: Callable[[], DaemonClient],
        status_path: Path,
        pause_hotkey: str,
    ) -> None:
        self._client_factory = client_factory
        self._status_path = status_path
        self._pause_hotkey = pause_hotkey
        self._action_lock = threading.Lock()

    def pause_all(self) -> None:
        self._run(self._pause_all)

    def stop_all(self) -> None:
        self._run(self._stop_all)

    async def _pause_all(self) -> dict[str, object]:
        client = self._client_factory()
        response = await client.call("session_list")
        sessions = response.get("sessions", [])
        paused = 0
        for session in sessions if isinstance(sessions, list) else []:
            if isinstance(session, dict) and session.get("state") == "active":
                await client.call(
                    "session_set_state",
                    {"session_id": str(session["session_id"]), "state": "paused"},
                )
                paused += 1
        return {"action": "pause_all", "paused_sessions": paused}

    async def _stop_all(self) -> dict[str, object]:
        result = await self._client_factory().call("emergency_stop")
        return {"action": "emergency_stop", **result}

    def _run(
        self, operation: Callable[[], Coroutine[Any, Any, dict[str, object]]]
    ) -> None:
        if not self._action_lock.acquire(blocking=False):
            return
        try:
            try:
                result = asyncio.run(operation())
            except Exception as error:
                self._write_status({"status": "error", "error": str(error)})
            else:
                self._write_status({"status": "ready", "last_action": result})
        finally:
            self._action_lock.release()

    def _write_status(self, update: dict[str, object]) -> None:
        payload = {
            "pause_hotkey": self._pause_hotkey,
            "updated_at": time.time(),
            **update,
        }
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self._status_path)


def settings_path() -> Path:
    configured = os.getenv("NIMBLEDESK_SAFETY_SETTINGS")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".nimbledesk/settings.json"
    )


def connection_path() -> Path:
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".nimbledesk/runtime/connection.json"
    )


def load_settings(path: Path) -> SafetySettings:
    if not path.is_file():
        return SafetySettings()
    return SafetySettings.model_validate_json(path.read_text(encoding="utf-8"))


def save_settings(path: Path, settings: SafetySettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _run_in_background(action: Callable[[], None]) -> None:
    threading.Thread(target=action, daemon=True, name="nimbledesk-safety-action").start()


def _tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (20, 24, 32, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(113, 229, 180, 255))
    draw.polygon(((32, 16), (46, 42), (18, 42)), fill=(20, 24, 32, 255))
    return image


def run_console(
    settings: SafetySettings,
    client_factory: Callable[[], DaemonClient],
    status_path: Path,
    *,
    monitor_parent_pipe: bool = False,
) -> None:
    keyboard = import_module("pynput.keyboard")
    pystray = import_module("pystray")
    controller = SafetyController(client_factory, status_path, settings.pause_hotkey)
    listener = cast(
        HotkeyListener,
        keyboard.GlobalHotKeys(
            {settings.pause_hotkey: lambda: _run_in_background(controller.pause_all)}
        ),
    )
    icon: TrayIcon

    def quit_console(_icon: object = None, _item: object = None) -> None:
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            "Pause all sessions",
            lambda _icon, _item: _run_in_background(controller.pause_all),
        ),
        pystray.MenuItem(
            "Emergency stop all",
            lambda _icon, _item: _run_in_background(controller.stop_all),
        ),
        pystray.MenuItem("Quit safety console", quit_console),
    )
    icon = cast(TrayIcon, pystray.Icon("NimbleDesk", _tray_image(), "NimbleDesk", menu))
    if monitor_parent_pipe:
        def stop_after_parent_closes() -> None:
            sys.stdin.buffer.read()
            icon.stop()

        threading.Thread(
            target=stop_after_parent_closes,
            daemon=True,
            name="nimbledesk-console-parent",
        ).start()
    controller._write_status({"status": "ready"})
    listener.start()
    try:
        icon.run()
    finally:
        listener.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NimbleDesk tray safety controls")
    parser.add_argument("--hotkey", help="global pause shortcut in pynput syntax")
    parser.add_argument("--settings-file", type=Path, default=settings_path())
    parser.add_argument("--connection-file", type=Path, default=connection_path())
    parser.add_argument("--parent-pipe", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    settings = load_settings(arguments.settings_file)
    if arguments.hotkey:
        settings = SafetySettings(pause_hotkey=arguments.hotkey)
        save_settings(arguments.settings_file, settings)
    status = arguments.settings_file.parent / "runtime" / "safety-console.json"
    run_console(
        settings,
        lambda: DaemonClient.from_file(
            arguments.connection_file, caller_id="safety-console"
        ),
        status,
        monitor_parent_pipe=arguments.parent_pipe,
    )


if __name__ == "__main__":
    main()
