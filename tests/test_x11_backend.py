from __future__ import annotations

import subprocess
from typing import Any

from nimbledesk.backends.simulator import SimulatorBackend
from nimbledesk.backends.x11 import X11DesktopBackend
from nimbledesk.protocol.models import ActionKind, ActionRequest, ActionStatus, Capability


class CommandRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, command: list[str], **arguments: Any) -> subprocess.CompletedProcess[str]:
        input_text = arguments.get("input")
        self.calls.append((command, input_text if isinstance(input_text, str) else None))
        stdout = "49152\n" if command[-1] == "getactivewindow" else "clipboard contents"
        return subprocess.CompletedProcess(command, 0, stdout, "")


def _backend(runner: CommandRunner) -> X11DesktopBackend:
    commands = {
        "xdotool": "/usr/bin/xdotool",
        "xclip": "/usr/bin/xclip",
        "gtk-launch": "/usr/bin/gtk-launch",
    }
    return X11DesktopBackend(
        SimulatorBackend(), runner, lambda executable: commands.get(executable)
    )


def test_x11_reports_window_and_clipboard_capabilities() -> None:
    observation = _backend(CommandRunner()).observe()

    assert Capability.WINDOWS in observation.capabilities
    assert Capability.CLIPBOARD in observation.capabilities


def test_x11_controls_active_window_and_clipboard() -> None:
    runner = CommandRunner()
    backend = _backend(runner)

    moved = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.MOVE_WINDOW,
            arguments={"window_id": "atspi:Editor:window", "left": -20, "top": 40},
        )
    )
    read = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.READ_CLIPBOARD,
            arguments={"maximum_characters": 9},
        )
    )
    written = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.WRITE_CLIPBOARD,
            arguments={"text": "new value"},
        )
    )

    assert moved.status is ActionStatus.COMPLETED
    assert read.data == {
        "backend": "linux-x11:simulator",
        "text": "clipboard",
        "characters": 9,
        "truncated": True,
    }
    assert written.data["characters"] == 9
    assert runner.calls == [
        (["/usr/bin/xdotool", "getactivewindow"], None),
        (["/usr/bin/xdotool", "windowmove", "49152", "-20", "40"], None),
        (["/usr/bin/xclip", "-selection", "clipboard", "-o"], None),
        (["/usr/bin/xclip", "-selection", "clipboard", "-i"], "new value"),
    ]


def test_x11_delegates_pointer_actions_to_desktop_backend() -> None:
    desktop = SimulatorBackend()
    backend = X11DesktopBackend(desktop, CommandRunner(), lambda _name: None)
    request = ActionRequest(session_id="session", kind=ActionKind.WAIT, arguments={"seconds": 0})

    result = backend.execute(request)

    assert result.status is ActionStatus.COMPLETED
    assert desktop.executed_actions == [request]
