from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from time import time
from typing import Any

from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    DesktopObservation,
    PermissionState,
    Rectangle,
    ScreenCapture,
)

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class X11DesktopBackend:
    """Adds explicit X11 window, clipboard, and application operations to desktop I/O."""

    def __init__(
        self,
        desktop: DesktopBackend,
        run_command: RunCommand = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._desktop = desktop
        self._run = run_command
        self._which = which
        self._xdotool = which("xdotool")
        self._clipboard = which("xclip") or which("xsel")
        self._launcher = which("gtk-launch")

    @property
    def backend_id(self) -> str:
        return f"linux-x11:{self._desktop.backend_id}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        capabilities = set(self._desktop.capabilities)
        if self._xdotool:
            capabilities.add(Capability.WINDOWS)
        if self._clipboard:
            capabilities.add(Capability.CLIPBOARD)
        return frozenset(capabilities)

    def observe(self) -> DesktopObservation:
        observation = self._desktop.observe()
        permissions = dict(observation.permissions)
        permissions[Capability.WINDOWS] = self._availability(self._xdotool)
        permissions[Capability.CLIPBOARD] = self._availability(self._clipboard)
        missing = tuple(
            message
            for available, message in (
                (self._xdotool, "Install xdotool for X11 window operations"),
                (self._clipboard, "Install xclip or xsel for X11 clipboard access"),
                (self._launcher, "Install gtk-launch for desktop application launching"),
            )
            if not available
        )
        return observation.model_copy(
            update={
                "capabilities": self.capabilities,
                "permissions": permissions,
                "warnings": (*observation.warnings, *missing),
            }
        )

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        return self._desktop.capture(observation_id, region, options)

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        try:
            data = self._execute_x11(request)
        except LookupError:
            return self._desktop.execute(request)
        except (RuntimeError, TypeError, ValueError) as error:
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                message=str(error),
                started_at=started_at,
                finished_at=time(),
                data={"backend": self.backend_id},
            )
        return ActionResult(
            action_id=request.action_id,
            status=ActionStatus.COMPLETED,
            message="X11 action executed",
            started_at=started_at,
            finished_at=time(),
            data={"backend": self.backend_id, **data},
        )

    def cancel_input(self) -> None:
        self._desktop.cancel_input()

    def _execute_x11(self, request: ActionRequest) -> dict[str, Any]:
        window_commands = {
            ActionKind.FOCUS_WINDOW: ("windowactivate", "--sync"),
            ActionKind.MINIMIZE_WINDOW: ("windowminimize",),
            ActionKind.MAXIMIZE_WINDOW: ("windowmaximize",),
            ActionKind.CLOSE_WINDOW: ("windowclose",),
        }
        command: tuple[str, ...] | None
        if request.kind is ActionKind.MOVE_WINDOW:
            command = (
                "windowmove",
                str(self._integer(request, "left", -100_000, 100_000)),
                str(self._integer(request, "top", -100_000, 100_000)),
            )
        elif request.kind is ActionKind.RESIZE_WINDOW:
            command = (
                "windowsize",
                str(self._integer(request, "width", 1, 100_000)),
                str(self._integer(request, "height", 1, 100_000)),
            )
        else:
            command = window_commands.get(request.kind)
        if command is not None:
            window = self._command(self._require(self._xdotool, "xdotool"), "getactivewindow")
            self._command(self._xdotool or "xdotool", command[0], window.strip(), *command[1:])
            return {"window_id": request.arguments.get("window_id")}
        if request.kind is ActionKind.READ_CLIPBOARD:
            text = self._clipboard_read()
            maximum = self._integer(request, "maximum_characters", 1, 100_000, 10_000)
            return {
                "text": text[:maximum],
                "characters": min(len(text), maximum),
                "truncated": len(text) > maximum,
            }
        if request.kind is ActionKind.WRITE_CLIPBOARD:
            clipboard_value = request.arguments.get("text")
            if not isinstance(clipboard_value, str) or not 1 <= len(clipboard_value) <= 100_000:
                raise ValueError("clipboard text must contain between 1 and 100000 characters")
            self._clipboard_write(clipboard_value)
            return {"characters": len(clipboard_value)}
        if request.kind is ActionKind.LAUNCH_APPLICATION:
            application_id = request.arguments.get("application_id")
            if not isinstance(application_id, str) or not application_id:
                raise ValueError("application ID is required")
            self._command(self._require(self._launcher, "gtk-launch"), application_id)
            return {"application_id": application_id}
        raise LookupError

    def _clipboard_read(self) -> str:
        executable = self._require(self._clipboard, "xclip or xsel")
        arguments = ("-selection", "clipboard", "-o") if executable.endswith("xclip") else (
            "--clipboard",
            "--output",
        )
        return self._command(executable, *arguments)

    def _clipboard_write(self, text: str) -> None:
        executable = self._require(self._clipboard, "xclip or xsel")
        arguments = ("-selection", "clipboard", "-i") if executable.endswith("xclip") else (
            "--clipboard",
            "--input",
        )
        self._command(executable, *arguments, input_text=text)

    def _command(self, executable: str, *arguments: str, input_text: str | None = None) -> str:
        completed = self._run(
            [executable, *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or f"{executable} failed")
        return completed.stdout

    @staticmethod
    def _require(value: str | None, capability: str) -> str:
        if value is None:
            raise RuntimeError(f"X11 action requires {capability}")
        return value

    @staticmethod
    def _availability(value: str | None) -> PermissionState:
        return PermissionState.GRANTED if value else PermissionState.UNAVAILABLE

    @staticmethod
    def _integer(
        request: ActionRequest,
        name: str,
        minimum: int,
        maximum: int,
        default: int | None = None,
    ) -> int:
        raw_value = request.arguments.get(name, default)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"{name} must be an integer")
        value = int(raw_value)
        if value != raw_value or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value
