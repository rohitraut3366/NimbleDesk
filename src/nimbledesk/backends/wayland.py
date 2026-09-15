from __future__ import annotations

import asyncio
import importlib
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import sleep, time
from typing import Any, Protocol, TypeVar
from uuid import uuid4

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
)

T = TypeVar("T")


@dataclass(frozen=True)
class PortalStream:
    node_id: int
    bounds: Rectangle


class PortalController(Protocol):
    @property
    def streams(self) -> tuple[PortalStream, ...]: ...

    def start(self) -> None: ...

    def capture(self, stream: PortalStream) -> Image.Image: ...

    def pointer_absolute(self, stream: PortalStream, point: Point) -> None: ...

    def pointer_button(self, button: str, pressed: bool) -> None: ...

    def pointer_axis(self, horizontal: float, vertical: float) -> None: ...

    def keyboard_key(self, key: str, pressed: bool) -> None: ...

    def close(self) -> None: ...


class WaylandPortalBackend:
    """Wayland capture and input through user-approved XDG desktop portals."""

    def __init__(self, controller: PortalController | None = None) -> None:
        self._controller = controller or XdgDesktopPortalController()
        self._sequence = 0
        self._started = False
        self._permission = PermissionState.NOT_DETERMINED
        self._warning: str | None = None
        self._cursor = Point(x=0, y=0)

    @property
    def backend_id(self) -> str:
        return "linux-wayland-xdg-portal"

    @property
    def capabilities(self) -> frozenset[Capability]:
        if not self._started:
            return frozenset()
        return frozenset(
            {Capability.SCREEN_CAPTURE, Capability.POINTER, Capability.KEYBOARD}
        )

    def observe(self) -> DesktopObservation:
        if self._permission is not PermissionState.DENIED:
            with suppress(RuntimeError):
                self._ensure_started()
        now = time()
        self._sequence += 1
        streams = self._controller.streams if self._started else ()
        displays = tuple(
            Display(
                display_id=f"portal-stream-{stream.node_id}",
                logical_bounds=stream.bounds,
                physical_bounds=stream.bounds,
                primary=index == 0,
            )
            for index, stream in enumerate(streams)
        )
        permission_capabilities = (
            Capability.SCREEN_CAPTURE,
            Capability.POINTER,
            Capability.KEYBOARD,
        )
        warnings = tuple(filter(None, (self._warning,)))
        return DesktopObservation(
            sequence=self._sequence,
            captured_at=now,
            expires_at=now + 5,
            platform=platform.system(),
            capabilities=self.capabilities,
            permissions={capability: self._permission for capability in permission_capabilities},
            displays=displays,
            cursor=self._cursor,
            warnings=warnings,
        )

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        self._ensure_started()
        stream = self._stream_for_region(region)
        image = self._controller.capture(stream)
        if region is not None:
            horizontal_scale = image.width / stream.bounds.width
            vertical_scale = image.height / stream.bounds.height
            relative_left = region.left - stream.bounds.left
            relative_top = region.top - stream.bounds.top
            image = image.crop(
                (
                    round(relative_left * horizontal_scale),
                    round(relative_top * vertical_scale),
                    round((relative_left + region.width) * horizontal_scale),
                    round((relative_top + region.height) * vertical_scale),
                )
            )
        return encode_capture(image, observation_id, options)

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        if request.kind in {ActionKind.FOCUS_WINDOW, ActionKind.APP_COMMAND}:
            return self._result(
                request,
                ActionStatus.CAPABILITY_UNAVAILABLE,
                f"Wayland portal backend does not support {request.kind}",
                started_at,
            )
        if not isinstance(request.target, CoordinateTarget) and request.kind not in {
            ActionKind.SCROLL,
            ActionKind.TYPE_TEXT,
            ActionKind.PRESS_KEY,
            ActionKind.HOTKEY,
            ActionKind.WAIT,
        }:
            return self._result(
                request,
                ActionStatus.CAPABILITY_UNAVAILABLE,
                "Wayland portal input requires a coordinate target",
                started_at,
            )
        try:
            self._ensure_started()
            self._execute(request)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            return self._result(request, ActionStatus.FAILED, str(error), started_at)
        return self._result(request, ActionStatus.COMPLETED, "Portal action executed", started_at)

    def cancel_input(self) -> None:
        if not self._started:
            return
        for key in ("shift", "control", "alt", "super"):
            self._controller.keyboard_key(key, False)
        for button in ("left", "middle", "right"):
            self._controller.pointer_button(button, False)

    def close(self) -> None:
        self.cancel_input()
        self._controller.close()
        self._started = False

    def _ensure_started(self) -> None:
        if self._started:
            return
        try:
            self._controller.start()
        except RuntimeError as error:
            self._permission = PermissionState.DENIED
            self._warning = str(error)
            raise
        self._started = True
        self._permission = PermissionState.GRANTED
        self._warning = None

    def _execute(self, request: ActionRequest) -> None:
        if request.kind is ActionKind.WAIT:
            sleep(_float_argument(request, "seconds", 0, 0, 10))
            return
        if request.kind is ActionKind.SCROLL:
            amount = _float_argument(request, "amount", 0, -100, 100)
            if amount == 0:
                raise ValueError("scroll amount cannot be zero")
            self._controller.pointer_axis(0, -amount * 10)
            return
        if request.kind is ActionKind.TYPE_TEXT:
            text = request.arguments.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 10_000:
                raise ValueError("text must contain between 1 and 10000 characters")
            for character in text:
                self._controller.keyboard_key(character, True)
                self._controller.keyboard_key(character, False)
            return
        if request.kind is ActionKind.PRESS_KEY:
            key = _key_argument(request, "key")
            presses = int(_float_argument(request, "presses", 1, 1, 20))
            for _ in range(presses):
                self._controller.keyboard_key(key, True)
                self._controller.keyboard_key(key, False)
            return
        if request.kind is ActionKind.HOTKEY:
            raw_keys = request.arguments.get("keys")
            if not isinstance(raw_keys, list) or not 2 <= len(raw_keys) <= 5:
                raise ValueError("keys must contain between 2 and 5 key names")
            keys = tuple(str(key) for key in raw_keys)
            if any(not key for key in keys):
                raise ValueError("hotkey entries cannot be empty")
            for key in keys:
                self._controller.keyboard_key(key, True)
            for key in reversed(keys):
                self._controller.keyboard_key(key, False)
            return
        assert isinstance(request.target, CoordinateTarget)
        stream = self._stream_for_point(request.target.point)
        duration = _float_argument(request, "duration", 0.2, 0, 10)
        if request.kind is ActionKind.DRAG:
            button = _button_argument(request)
            self._controller.pointer_button(button, True)
            self._move_pointer(stream, request.target.point, duration)
            self._controller.pointer_button(button, False)
        else:
            self._move_pointer(
                stream,
                request.target.point,
                duration if request.kind is ActionKind.MOVE_POINTER else 0,
            )
            if request.kind is ActionKind.CLICK:
                button = _button_argument(request)
                clicks = int(_float_argument(request, "clicks", 1, 1, 10))
                for _ in range(clicks):
                    self._controller.pointer_button(button, True)
                    self._controller.pointer_button(button, False)
        self._cursor = request.target.point

    def _move_pointer(self, stream: PortalStream, target: Point, duration: float) -> None:
        if duration <= 0 or not stream.bounds.contains(self._cursor):
            self._controller.pointer_absolute(stream, target)
            return
        steps = min(120, max(1, round(duration * 60)))
        start = self._cursor
        delay = duration / steps
        for step in range(1, steps + 1):
            progress = step / steps
            point = Point(
                x=round(start.x + (target.x - start.x) * progress),
                y=round(start.y + (target.y - start.y) * progress),
            )
            self._controller.pointer_absolute(stream, point)
            sleep(delay)

    def _stream_for_point(self, point: Point) -> PortalStream:
        stream = next(
            (
                candidate
                for candidate in self._controller.streams
                if candidate.bounds.contains(point)
            ),
            None,
        )
        if stream is None:
            raise ValueError("target is outside the shared Wayland displays")
        return stream

    def _stream_for_region(self, region: Rectangle | None) -> PortalStream:
        if not self._controller.streams:
            raise RuntimeError("the desktop portal returned no shared display")
        if region is None:
            return self._controller.streams[0]
        start = Point(x=region.left, y=region.top)
        end = Point(x=region.left + region.width - 1, y=region.top + region.height - 1)
        stream = next(
            (
                candidate
                for candidate in self._controller.streams
                if candidate.bounds.contains(start) and candidate.bounds.contains(end)
            ),
            None,
        )
        if stream is None:
            raise ValueError("capture region must fit within one shared Wayland display")
        return stream

    def _result(
        self,
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
            data={"backend": self.backend_id},
        )


class XdgDesktopPortalController:
    """Persistent RemoteDesktop/ScreenCast portal session backed by PipeWire."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._bus: Any | None = None
        self._remote: Any | None = None
        self._screen_cast: Any | None = None
        self._session: str | None = None
        self._pipewire_fd: int | None = None
        self._streams: tuple[PortalStream, ...] = ()

    @property
    def streams(self) -> tuple[PortalStream, ...]:
        return self._streams

    def start(self) -> None:
        if self._session is not None:
            return
        if shutil.which("gst-launch-1.0") is None:
            raise RuntimeError(
                "Wayland capture requires GStreamer with the PipeWire plugin (gst-launch-1.0)"
            )
        try:
            self._call(self._start())
        except RuntimeError:
            if self._session and self._bus:
                with suppress(RuntimeError):
                    self._call(self._close_session())
            self._session = None
            self._streams = ()
            raise

    def capture(self, stream: PortalStream) -> Image.Image:
        if self._pipewire_fd is None:
            raise RuntimeError("PipeWire portal connection is unavailable")
        with tempfile.TemporaryDirectory(prefix="nimbledesk-wayland-") as temporary:
            output = Path(temporary) / "capture.png"
            inherited_fd = os.dup(self._pipewire_fd)
            try:
                completed = subprocess.run(
                    [
                        "gst-launch-1.0",
                        "-q",
                        "pipewiresrc",
                        f"fd={inherited_fd}",
                        f"path={stream.node_id}",
                        "num-buffers=1",
                        "!",
                        "videoconvert",
                        "!",
                        "pngenc",
                        "snapshot=true",
                        "!",
                        "filesink",
                        f"location={output}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    pass_fds=(inherited_fd,),
                )
            finally:
                os.close(inherited_fd)
            if completed.returncode != 0 or not output.is_file():
                raise RuntimeError(
                    completed.stderr.strip() or "PipeWire did not produce a desktop frame"
                )
            with Image.open(output) as captured:
                image: Image.Image = captured.convert("RGB")
                return image.copy()

    def pointer_absolute(self, stream: PortalStream, point: Point) -> None:
        if self._remote is None or self._session is None:
            raise RuntimeError("Wayland input portal is unavailable")
        relative_x = point.x - stream.bounds.left
        relative_y = point.y - stream.bounds.top
        self._call(
            self._remote.call_notify_pointer_motion_absolute(
                self._session, {}, stream.node_id, float(relative_x), float(relative_y)
            )
        )

    def pointer_button(self, button: str, pressed: bool) -> None:
        if self._remote is None or self._session is None:
            raise RuntimeError("Wayland input portal is unavailable")
        button_code = {"left": 272, "right": 273, "middle": 274}[button]
        self._call(
            self._remote.call_notify_pointer_button(
                self._session, {}, button_code, 1 if pressed else 0
            )
        )

    def pointer_axis(self, horizontal: float, vertical: float) -> None:
        if self._remote is None or self._session is None:
            raise RuntimeError("Wayland input portal is unavailable")
        self._call(
            self._remote.call_notify_pointer_axis(
                self._session, {}, float(horizontal), float(vertical)
            )
        )

    def keyboard_key(self, key: str, pressed: bool) -> None:
        if self._remote is None or self._session is None:
            raise RuntimeError("Wayland input portal is unavailable")
        keysym = _keysym(key)
        self._call(
            self._remote.call_notify_keyboard_keysym(
                self._session, {}, keysym, 1 if pressed else 0
            )
        )

    def close(self) -> None:
        if self._session and self._bus:
            with suppress(RuntimeError):
                self._call(self._close_session())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        if self._pipewire_fd is not None:
            os.close(self._pipewire_fd)
            self._pipewire_fd = None

    async def _start(self) -> None:
        try:
            MessageBus = importlib.import_module("dbus_next.aio").MessageBus
            Variant = importlib.import_module("dbus_next.signature").Variant
        except ImportError as error:
            raise RuntimeError("Wayland portals require the dbus-next package") from error
        self._bus = await MessageBus().connect()
        destination = "org.freedesktop.portal.Desktop"
        desktop_path = "/org/freedesktop/portal/desktop"
        introspection = await self._bus.introspect(destination, desktop_path)
        desktop = self._bus.get_proxy_object(destination, desktop_path, introspection)
        self._remote = desktop.get_interface("org.freedesktop.portal.RemoteDesktop")
        self._screen_cast = desktop.get_interface("org.freedesktop.portal.ScreenCast")
        session_token = f"nimbledesk_session_{uuid4().hex}"
        request_token = f"nimbledesk_{uuid4().hex}"
        result = await self._request(
            self._remote.call_create_session(
                {
                    "handle_token": Variant("s", request_token),
                    "session_handle_token": Variant("s", session_token),
                }
            ),
            request_token,
        )
        self._session = str(_variant_value(result["session_handle"]))
        request_token = f"nimbledesk_{uuid4().hex}"
        await self._request(
            self._remote.call_select_devices(
                self._session,
                {
                    "handle_token": Variant("s", request_token),
                    "types": Variant("u", 3),
                },
            ),
            request_token,
        )
        request_token = f"nimbledesk_{uuid4().hex}"
        await self._request(
            self._screen_cast.call_select_sources(
                self._session,
                {
                    "handle_token": Variant("s", request_token),
                    "types": Variant("u", 1),
                    "multiple": Variant("b", True),
                    "cursor_mode": Variant("u", 2),
                },
            ),
            request_token,
        )
        request_token = f"nimbledesk_{uuid4().hex}"
        started = await self._request(
            self._remote.call_start(
                self._session,
                "",
                {"handle_token": Variant("s", request_token)},
            ),
            request_token,
        )
        self._streams = _portal_streams(_variant_value(started.get("streams", [])))
        if not self._streams:
            raise RuntimeError("the desktop portal granted no monitor streams")
        self._pipewire_fd = int(
            await self._screen_cast.call_open_pipe_wire_remote(self._session, {})
        )

    async def _request(
        self, request_call: Coroutine[Any, Any, str], request_token: str
    ) -> dict[str, Any]:
        assert self._bus is not None
        response: asyncio.Future[tuple[int, dict[str, Any]]] = self._loop.create_future()
        sender = str(self._bus.unique_name).removeprefix(":").replace(".", "_")
        expected_path = f"/org/freedesktop/portal/desktop/request/{sender}/{request_token}"

        def received(message: Any) -> None:
            if (
                message.path == expected_path
                and message.interface == "org.freedesktop.portal.Request"
                and message.member == "Response"
                and not response.done()
            ):
                code, results = message.body
                response.set_result((int(code), dict(results)))

        self._bus.add_message_handler(received)
        try:
            returned_path = await request_call
            if returned_path != expected_path:
                raise RuntimeError("desktop portal returned an unexpected request handle")
            code, results = await asyncio.wait_for(response, timeout=120)
        finally:
            self._bus.remove_message_handler(received)
        if code == 1:
            raise RuntimeError("Wayland screen and input sharing was cancelled")
        if code != 0:
            raise RuntimeError(f"Wayland desktop portal denied the request ({code})")
        return results

    async def _close_session(self) -> None:
        assert self._bus is not None and self._session is not None
        destination = "org.freedesktop.portal.Desktop"
        introspection = await self._bus.introspect(destination, self._session)
        session = self._bus.get_proxy_object(destination, self._session, introspection)
        await session.get_interface("org.freedesktop.portal.Session").call_close()

    def _call(self, coroutine: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=130)
        except Exception as error:
            future.cancel()
            raise RuntimeError(str(error) or type(error).__name__) from error


def _portal_streams(raw_streams: Any) -> tuple[PortalStream, ...]:
    streams: list[PortalStream] = []
    for index, (raw_node, raw_properties) in enumerate(raw_streams):
        properties = {
            str(key): _variant_value(value) for key, value in dict(raw_properties).items()
        }
        raw_size = properties.get("size", (1920, 1080))
        raw_position = properties.get("position", (index * int(raw_size[0]), 0))
        width, height = int(raw_size[0]), int(raw_size[1])
        left, top = int(raw_position[0]), int(raw_position[1])
        streams.append(
            PortalStream(
                node_id=int(raw_node),
                bounds=Rectangle(left=left, top=top, width=width, height=height),
            )
        )
    return tuple(streams)


def _variant_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _keysym(key: str) -> int:
    normalized = key.casefold()
    named = {
        "backspace": 0xFF08,
        "tab": 0xFF09,
        "enter": 0xFF0D,
        "escape": 0xFF1B,
        "home": 0xFF50,
        "left": 0xFF51,
        "up": 0xFF52,
        "right": 0xFF53,
        "down": 0xFF54,
        "pageup": 0xFF55,
        "pagedown": 0xFF56,
        "end": 0xFF57,
        "insert": 0xFF63,
        "delete": 0xFFFF,
        "shift": 0xFFE1,
        "control": 0xFFE3,
        "ctrl": 0xFFE3,
        "alt": 0xFFE9,
        "super": 0xFFEB,
        "win": 0xFFEB,
        "command": 0xFFEB,
        "space": 0x20,
    }
    if normalized in named:
        return named[normalized]
    if len(key) == 1:
        codepoint = ord(key)
        return codepoint if codepoint <= 0xFF else 0x01000000 | codepoint
    if normalized.startswith("f") and normalized[1:].isdigit():
        function = int(normalized[1:])
        if 1 <= function <= 35:
            return 0xFFBD + function
    raise ValueError(f"unsupported Wayland key: {key}")


def _float_argument(
    request: ActionRequest, name: str, default: float, minimum: float, maximum: float
) -> float:
    value = float(request.arguments.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _key_argument(request: ActionRequest, name: str) -> str:
    value = request.arguments.get(name)
    if not isinstance(value, str) or not 1 <= len(value) <= 30:
        raise ValueError(f"{name} must contain between 1 and 30 characters")
    return value


def _button_argument(request: ActionRequest) -> str:
    value = str(request.arguments.get("button", "left"))
    if value not in {"left", "middle", "right"}:
        raise ValueError("button must be left, middle, or right")
    return value
