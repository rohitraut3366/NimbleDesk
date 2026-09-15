from __future__ import annotations

import base64
import hashlib
import io
import platform
from time import sleep, time
from typing import Any

from PIL import Image

from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CoordinateTarget,
    DesktopObservation,
    Display,
    PermissionState,
    Point,
    Rectangle,
    ScreenCapture,
)


class PortableDesktopBackend:
    """PyAutoGUI fallback used until a native capability provider is available."""

    def __init__(self, automation: Any) -> None:
        self._automation = automation
        self._automation.FAILSAFE = True
        self._automation.PAUSE = 0.1
        self._sequence = 0

    @property
    def backend_id(self) -> str:
        return "portable-pyautogui"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {Capability.SCREEN_CAPTURE, Capability.POINTER, Capability.KEYBOARD}
        )

    def observe(self) -> DesktopObservation:
        now = time()
        self._sequence += 1
        width, height = self._automation.size()
        cursor_x, cursor_y = self._automation.position()
        bounds = Rectangle(left=0, top=0, width=int(width), height=int(height))
        return DesktopObservation(
            sequence=self._sequence,
            captured_at=now,
            expires_at=now + 5,
            platform=platform.system(),
            capabilities=self.capabilities,
            permissions={capability: PermissionState.GRANTED for capability in self.capabilities},
            displays=(
                Display(
                    display_id="primary",
                    logical_bounds=bounds,
                    physical_bounds=bounds,
                    primary=True,
                ),
            ),
            cursor=Point(x=int(cursor_x), y=int(cursor_y)),
            warnings=(
                "Portable fallback cannot inspect windows or semantic UI elements",
                "Portable fallback reports the primary display only",
            ),
        )

    def capture(self, observation_id: str, region: Rectangle | None = None) -> ScreenCapture:
        screenshot: Image.Image = self._automation.screenshot()
        if region is not None:
            self._validate_rectangle(region)
            screenshot = screenshot.crop(
                (
                    region.left,
                    region.top,
                    region.left + region.width,
                    region.top + region.height,
                )
            )
        output = io.BytesIO()
        screenshot.save(output, format="PNG")
        image_bytes = output.getvalue()
        return ScreenCapture(
            observation_id=observation_id,
            width=screenshot.width,
            height=screenshot.height,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            data_base64=base64.b64encode(image_bytes).decode("ascii"),
        )

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        handlers = {
            ActionKind.MOVE_POINTER: self._move_pointer,
            ActionKind.CLICK: self._click,
            ActionKind.DRAG: self._drag,
            ActionKind.SCROLL: self._scroll,
            ActionKind.TYPE_TEXT: self._type_text,
            ActionKind.PRESS_KEY: self._press_key,
            ActionKind.HOTKEY: self._hotkey,
            ActionKind.WAIT: self._wait,
        }
        handler = handlers.get(request.kind)
        if handler is None:
            return self._result(
                request,
                ActionStatus.CAPABILITY_UNAVAILABLE,
                f"portable backend does not support {request.kind}",
                started_at,
            )
        try:
            handler(request)
        except (KeyError, TypeError, ValueError) as error:
            return self._result(request, ActionStatus.FAILED, str(error), started_at)
        return self._result(request, ActionStatus.COMPLETED, "Action executed", started_at)

    def cancel_input(self) -> None:
        for key in ("shift", "ctrl", "alt", "command", "win"):
            self._automation.keyUp(key)
        self._automation.mouseUp(button="left")
        self._automation.mouseUp(button="middle")
        self._automation.mouseUp(button="right")

    def _move_pointer(self, request: ActionRequest) -> None:
        point = self._coordinate(request)
        self._automation.moveTo(
            point.x,
            point.y,
            duration=_bounded_float(request.arguments, "duration", 0.2, 0, 5),
        )

    def _click(self, request: ActionRequest) -> None:
        point = self._coordinate(request)
        button = str(request.arguments.get("button", "left"))
        if button not in {"left", "middle", "right"}:
            raise ValueError("button must be left, middle, or right")
        self._automation.click(
            point.x,
            point.y,
            clicks=_bounded_int(request.arguments, "clicks", 1, 1, 10),
            interval=_bounded_float(request.arguments, "interval", 0.1, 0, 2),
            button=button,
        )

    def _drag(self, request: ActionRequest) -> None:
        point = self._coordinate(request)
        button = str(request.arguments.get("button", "left"))
        if button not in {"left", "middle", "right"}:
            raise ValueError("button must be left, middle, or right")
        self._automation.dragTo(
            point.x,
            point.y,
            duration=_bounded_float(request.arguments, "duration", 0.5, 0.1, 10),
            button=button,
        )

    def _scroll(self, request: ActionRequest) -> None:
        amount = _bounded_int(request.arguments, "amount", 0, -100, 100)
        if amount == 0:
            raise ValueError("scroll amount cannot be zero")
        self._automation.scroll(amount)

    def _type_text(self, request: ActionRequest) -> None:
        text = request.arguments.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 10_000:
            raise ValueError("text must contain between 1 and 10000 characters")
        self._automation.write(
            text,
            interval=_bounded_float(request.arguments, "interval", 0.02, 0, 2),
        )

    def _press_key(self, request: ActionRequest) -> None:
        key = request.arguments.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 30:
            raise ValueError("key must contain between 1 and 30 characters")
        self._automation.press(
            key,
            presses=_bounded_int(request.arguments, "presses", 1, 1, 20),
            interval=_bounded_float(request.arguments, "interval", 0.1, 0, 2),
        )

    def _hotkey(self, request: ActionRequest) -> None:
        keys = request.arguments.get("keys")
        if not isinstance(keys, list) or not 2 <= len(keys) <= 5:
            raise ValueError("keys must contain between 2 and 5 key names")
        if any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("each hotkey entry must be a non-empty string")
        self._automation.hotkey(*keys, interval=0.05)

    def _wait(self, request: ActionRequest) -> None:
        sleep(_bounded_float(request.arguments, "seconds", 0, 0, 10))

    def _coordinate(self, request: ActionRequest) -> Point:
        if not isinstance(request.target, CoordinateTarget):
            raise ValueError("portable backend requires a coordinate target")
        width, height = self._automation.size()
        bounds = Rectangle(left=0, top=0, width=int(width), height=int(height))
        if not bounds.contains(request.target.point):
            raise ValueError("target is outside the primary display")
        return request.target.point

    def _validate_rectangle(self, rectangle: Rectangle) -> None:
        width, height = self._automation.size()
        screen = Rectangle(left=0, top=0, width=int(width), height=int(height))
        corners = (
            Point(x=rectangle.left, y=rectangle.top),
            Point(x=rectangle.left + rectangle.width - 1, y=rectangle.top + rectangle.height - 1),
        )
        if not all(screen.contains(corner) for corner in corners):
            raise ValueError("capture region is outside the primary display")

    @staticmethod
    def _result(
        request: ActionRequest,
        status: ActionStatus,
        message: str,
        started_at: float,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            data={"backend": "portable-pyautogui"},
        )


def _bounded_float(
    arguments: dict[str, Any],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = float(arguments.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_int(
    arguments: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    return int(_bounded_float(arguments, name, default, minimum, maximum))
