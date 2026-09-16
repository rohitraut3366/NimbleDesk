from __future__ import annotations

import platform
from time import sleep, time
from typing import Protocol

from PIL import Image

from nimbledesk.backends.images import encode_capture
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    CoordinateTarget,
    DesktopObservation,
    Display,
    PermissionState,
    Point,
    Rectangle,
    ScreenCapture,
    SelectorTarget,
)


class SystemIOController(Protocol):
    @property
    def controller_id(self) -> str: ...

    def permissions(self) -> dict[Capability, PermissionState]: ...

    def displays(self) -> tuple[Display, ...]: ...

    def cursor(self) -> Point: ...

    def capture(self, display: Display, region: Rectangle | None) -> Image.Image: ...

    def move_pointer(self, point: Point) -> None: ...

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None: ...

    def scroll(self, horizontal: float, vertical: float) -> None: ...

    def key(self, key: str, pressed: bool) -> None: ...

    def type_text(self, text: str) -> None: ...

    def focus_window(self, window_id: str) -> None: ...

    def move_window(self, window_id: str, point: Point) -> None: ...

    def resize_window(self, window_id: str, width: int, height: int) -> None: ...

    def minimize_window(self, window_id: str) -> None: ...

    def maximize_window(self, window_id: str) -> None: ...

    def close_window(self, window_id: str) -> None: ...

    def read_clipboard(self) -> str: ...

    def write_clipboard(self, text: str) -> None: ...

    def launch_application(self, application_id: str) -> None: ...


class SystemIOBackend:
    """Shared action contract for native macOS and Windows I/O controllers."""

    def __init__(self, controller: SystemIOController) -> None:
        self._controller = controller
        self._sequence = 0
        self._displays: tuple[Display, ...] = ()
        self._cursor = Point(x=0, y=0)

    @property
    def backend_id(self) -> str:
        return self._controller.controller_id

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            capability
            for capability, permission in self._controller.permissions().items()
            if permission is PermissionState.GRANTED
        )

    def observe(self) -> DesktopObservation:
        now = time()
        self._sequence += 1
        permissions = self._controller.permissions()
        warnings = tuple(
            f"{capability.value} permission is {permission.value}"
            for capability, permission in permissions.items()
            if permission is not PermissionState.GRANTED
        )
        try:
            self._displays = self._controller.displays()
            self._cursor = self._controller.cursor()
        except RuntimeError as error:
            warnings = (*warnings, str(error))
            self._displays = ()
        return DesktopObservation(
            sequence=self._sequence,
            captured_at=now,
            expires_at=now + 5,
            platform=platform.system(),
            capabilities=self.capabilities,
            permissions=permissions,
            displays=self._displays,
            cursor=self._cursor,
            warnings=warnings,
        )

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        capture_permission = self._controller.permissions().get(Capability.SCREEN_CAPTURE)
        if capture_permission is not PermissionState.GRANTED:
            raise RuntimeError("screen capture permission is not granted")
        display = self._display_for_region(region)
        return encode_capture(self._controller.capture(display, region), observation_id, options)

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        required = _required_capability(request.kind)
        permission = self._controller.permissions().get(required) if required else None
        if required is not None and permission is not PermissionState.GRANTED:
            return self._result(
                request,
                ActionStatus.CAPABILITY_UNAVAILABLE,
                f"{required.value} permission is not granted",
                started_at,
            )
        try:
            action_data = self._execute(request)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            return self._result(request, ActionStatus.FAILED, str(error), started_at)
        return self._result(
            request,
            ActionStatus.COMPLETED,
            "Native action executed",
            started_at,
            action_data,
        )

    def cancel_input(self) -> None:
        point = self._controller.cursor()
        for key in ("shift", "control", "alt", "command", "win"):
            self._controller.key(key, False)
        for button in ("left", "middle", "right"):
            self._controller.pointer_button(point, button, False)

    def _execute(self, request: ActionRequest) -> dict[str, object]:
        if request.kind is ActionKind.WAIT:
            sleep(_float_argument(request, "seconds", 0, 0, 10))
            return {}
        if request.kind is ActionKind.FOCUS_WINDOW:
            self._controller.focus_window(_window_id(request))
            return {}
        if request.kind is ActionKind.MOVE_WINDOW:
            self._controller.move_window(
                _window_id(request),
                Point(
                    x=int(_float_argument(request, "left", 0, -100_000, 100_000)),
                    y=int(_float_argument(request, "top", 0, -100_000, 100_000)),
                ),
            )
            return {}
        if request.kind is ActionKind.RESIZE_WINDOW:
            self._controller.resize_window(
                _window_id(request),
                int(_float_argument(request, "width", 0, 1, 100_000)),
                int(_float_argument(request, "height", 0, 1, 100_000)),
            )
            return {}
        if request.kind is ActionKind.MINIMIZE_WINDOW:
            self._controller.minimize_window(_window_id(request))
            return {}
        if request.kind is ActionKind.MAXIMIZE_WINDOW:
            self._controller.maximize_window(_window_id(request))
            return {}
        if request.kind is ActionKind.CLOSE_WINDOW:
            self._controller.close_window(_window_id(request))
            return {}
        if request.kind is ActionKind.READ_CLIPBOARD:
            maximum = int(_float_argument(request, "maximum_characters", 10_000, 1, 100_000))
            text = self._controller.read_clipboard()
            return {
                "text": text[:maximum],
                "characters": min(len(text), maximum),
                "truncated": len(text) > maximum,
            }
        if request.kind is ActionKind.WRITE_CLIPBOARD:
            clipboard_value = request.arguments.get("text")
            if not isinstance(clipboard_value, str) or not 1 <= len(clipboard_value) <= 100_000:
                raise ValueError("clipboard text must contain between 1 and 100000 characters")
            self._controller.write_clipboard(clipboard_value)
            return {"characters": len(clipboard_value)}
        if request.kind is ActionKind.LAUNCH_APPLICATION:
            application_id = request.arguments.get("application_id")
            if not isinstance(application_id, str) or not application_id:
                raise ValueError("application ID is required")
            self._controller.launch_application(application_id)
            return {"application_id": application_id}
        if request.kind is ActionKind.SCROLL:
            vertical = _float_argument(request, "amount", 0, -100, 100)
            horizontal = _float_argument(request, "horizontal", 0, -100, 100)
            if vertical == 0 and horizontal == 0:
                raise ValueError("scroll amount cannot be zero")
            self._controller.scroll(horizontal, vertical)
            return {}
        if request.kind is ActionKind.TYPE_TEXT:
            typing_value = request.arguments.get("text")
            if not isinstance(typing_value, str) or not 1 <= len(typing_value) <= 10_000:
                raise ValueError("text must contain between 1 and 10000 characters")
            self._controller.type_text(typing_value)
            return {}
        if request.kind is ActionKind.PRESS_KEY:
            key = _key_argument(request)
            presses = int(_float_argument(request, "presses", 1, 1, 20))
            for _ in range(presses):
                self._controller.key(key, True)
                self._controller.key(key, False)
            return {}
        if request.kind is ActionKind.HOTKEY:
            raw_keys = request.arguments.get("keys")
            if not isinstance(raw_keys, list) or not 2 <= len(raw_keys) <= 5:
                raise ValueError("keys must contain between 2 and 5 key names")
            keys = tuple(str(key) for key in raw_keys)
            if any(not key for key in keys):
                raise ValueError("hotkey entries cannot be empty")
            for key in keys:
                self._controller.key(key, True)
            for key in reversed(keys):
                self._controller.key(key, False)
            return {}
        if request.kind not in {ActionKind.MOVE_POINTER, ActionKind.CLICK, ActionKind.DRAG}:
            raise ValueError(f"native I/O backend does not support {request.kind}")
        if not isinstance(request.target, CoordinateTarget):
            raise ValueError("native pointer input requires a coordinate target")
        point = request.target.point
        self._display_for_point(point)
        duration = _float_argument(request, "duration", 0.2, 0, 10)
        if request.kind is ActionKind.DRAG:
            button = _button_argument(request)
            current = self._controller.cursor()
            self._controller.pointer_button(current, button, True)
            self._move(current, point, duration)
            self._controller.pointer_button(point, button, False)
        else:
            self._move(
                self._controller.cursor(),
                point,
                duration if request.kind is ActionKind.MOVE_POINTER else 0,
            )
            if request.kind is ActionKind.CLICK:
                button = _button_argument(request)
                clicks = int(_float_argument(request, "clicks", 1, 1, 10))
                interval = _float_argument(request, "interval", 0.1, 0, 2)
                for click in range(clicks):
                    self._controller.pointer_button(point, button, True)
                    self._controller.pointer_button(point, button, False)
                    if click + 1 < clicks:
                        sleep(interval)
        self._cursor = point
        return {}

    def _move(self, start: Point, target: Point, duration: float) -> None:
        if duration <= 0:
            self._controller.move_pointer(target)
            return
        steps = min(120, max(1, round(duration * 60)))
        for step in range(1, steps + 1):
            progress = step / steps
            self._controller.move_pointer(
                Point(
                    x=round(start.x + (target.x - start.x) * progress),
                    y=round(start.y + (target.y - start.y) * progress),
                )
            )
            sleep(duration / steps)

    def _display_for_point(self, point: Point) -> Display:
        displays = self._displays or self._controller.displays()
        display = next(
            (
                candidate
                for candidate in displays
                if candidate.logical_bounds.contains(point)
            ),
            None,
        )
        if display is None:
            raise ValueError("target is outside the available displays")
        return display

    def _display_for_region(self, region: Rectangle | None) -> Display:
        displays = self._displays or self._controller.displays()
        if not displays:
            raise RuntimeError("native display enumeration returned no displays")
        if region is None:
            return next((display for display in displays if display.primary), displays[0])
        start = Point(x=region.left, y=region.top)
        end = Point(x=region.left + region.width - 1, y=region.top + region.height - 1)
        display = next(
            (
                candidate
                for candidate in displays
                if candidate.logical_bounds.contains(start)
                and candidate.logical_bounds.contains(end)
            ),
            None,
        )
        if display is None:
            raise ValueError("capture region must fit within one display")
        return display

    def _result(
        self,
        request: ActionRequest,
        status: ActionStatus,
        message: str,
        started_at: float,
        action_data: dict[str, object] | None = None,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            data={"backend": self.backend_id, **(action_data or {})},
        )


def _required_capability(kind: ActionKind) -> Capability | None:
    if kind in {ActionKind.MOVE_POINTER, ActionKind.CLICK, ActionKind.DRAG, ActionKind.SCROLL}:
        return Capability.POINTER
    if kind in {ActionKind.TYPE_TEXT, ActionKind.PRESS_KEY, ActionKind.HOTKEY}:
        return Capability.KEYBOARD
    if kind in {
        ActionKind.FOCUS_WINDOW,
        ActionKind.MOVE_WINDOW,
        ActionKind.RESIZE_WINDOW,
        ActionKind.MINIMIZE_WINDOW,
        ActionKind.MAXIMIZE_WINDOW,
        ActionKind.CLOSE_WINDOW,
    }:
        return Capability.WINDOWS
    if kind in {ActionKind.READ_CLIPBOARD, ActionKind.WRITE_CLIPBOARD}:
        return Capability.CLIPBOARD
    if kind is ActionKind.LAUNCH_APPLICATION:
        return Capability.WINDOWS
    return None


def _float_argument(
    request: ActionRequest,
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = float(request.arguments.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _key_argument(request: ActionRequest) -> str:
    key = request.arguments.get("key")
    if not isinstance(key, str) or not 1 <= len(key) <= 30:
        raise ValueError("key must contain between 1 and 30 characters")
    return key


def _button_argument(request: ActionRequest) -> str:
    button = str(request.arguments.get("button", "left"))
    if button not in {"left", "middle", "right"}:
        raise ValueError("button must be left, middle, or right")
    return button


def _window_id(request: ActionRequest) -> str:
    if isinstance(request.target, SelectorTarget) and request.target.window_id:
        return request.target.window_id
    if request.expected_window_id:
        return request.expected_window_id
    window_id = request.arguments.get("window_id")
    if isinstance(window_id, str) and window_id:
        return window_id
    raise ValueError("focus_window requires a window ID")
