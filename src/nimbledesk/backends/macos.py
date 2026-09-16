from __future__ import annotations

import importlib
import importlib.util
import threading
from typing import Any

from PIL import Image

from nimbledesk.backends.system_io import SystemIOBackend
from nimbledesk.protocol.models import Capability, Display, PermissionState, Point, Rectangle


class MacOSNativeBackend(SystemIOBackend):
    def __init__(self, controller: MacOSController | None = None) -> None:
        super().__init__(controller or MacOSController())


class MacOSController:
    controller_id = "macos-screencapturekit-coregraphics-cgevent"

    def __init__(
        self,
        quartz: Any | None = None,
        screen_capture_kit: Any | None = None,
        app_kit: Any | None = None,
        application_services: Any | None = None,
    ) -> None:
        self._quartz = quartz
        self._screen_capture_kit = screen_capture_kit
        self._app_kit = app_kit
        self._application_services = application_services

    @property
    def available(self) -> bool:
        return self._quartz is not None or importlib.util.find_spec("Quartz") is not None

    def permissions(self) -> dict[Capability, PermissionState]:
        quartz = self._load_quartz()
        if quartz is None:
            return {
                Capability.SCREEN_CAPTURE: PermissionState.UNAVAILABLE,
                Capability.POINTER: PermissionState.UNAVAILABLE,
                Capability.KEYBOARD: PermissionState.UNAVAILABLE,
                Capability.WINDOWS: PermissionState.UNAVAILABLE,
            }
        screen_granted = bool(
            quartz.CGPreflightScreenCaptureAccess()
            if hasattr(quartz, "CGPreflightScreenCaptureAccess")
            else True
        )
        application_services = self._load_application_services()
        input_granted = bool(
            application_services and application_services.AXIsProcessTrusted()
        )
        return {
            Capability.SCREEN_CAPTURE: (
                PermissionState.GRANTED if screen_granted else PermissionState.DENIED
            ),
            Capability.POINTER: (
                PermissionState.GRANTED if input_granted else PermissionState.DENIED
            ),
            Capability.KEYBOARD: (
                PermissionState.GRANTED if input_granted else PermissionState.DENIED
            ),
            Capability.WINDOWS: PermissionState.GRANTED,
        }

    def displays(self) -> tuple[Display, ...]:
        quartz = self._require_quartz()
        app_kit = self._load_app_kit()
        display_ids = _active_display_ids(quartz, app_kit)
        scales = _display_scales(app_kit)
        main_display = int(quartz.CGMainDisplayID())
        displays = []
        for display_id in display_ids:
            bounds = quartz.CGDisplayBounds(display_id)
            left, top, width, height = _cg_rectangle(bounds)
            scale = scales.get(display_id, 1.0)
            pixel_width = round(width * scale)
            pixel_height = round(height * scale)
            displays.append(
                Display(
                    display_id=str(display_id),
                    logical_bounds=Rectangle(left=left, top=top, width=width, height=height),
                    physical_bounds=Rectangle(
                        left=round(left * scale),
                        top=round(top * scale),
                        width=pixel_width,
                        height=pixel_height,
                    ),
                    scale=scale,
                    primary=display_id == main_display,
                )
            )
        return tuple(displays)

    def cursor(self) -> Point:
        quartz = self._require_quartz()
        event = quartz.CGEventCreate(None)
        location = quartz.CGEventGetLocation(event)
        return Point(x=round(location.x), y=round(location.y))

    def capture(self, display: Display, region: Rectangle | None) -> Image.Image:
        if self._load_screen_capture_kit() is not None:
            try:
                return self._capture_screen_capture_kit(display, region)
            except RuntimeError:
                pass
        return self._capture_core_graphics(display, region)

    def move_pointer(self, point: Point) -> None:
        self._post_mouse(point, "move", False)

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None:
        self._post_mouse(point, button, pressed)

    def scroll(self, horizontal: float, vertical: float) -> None:
        quartz = self._require_quartz()
        event = quartz.CGEventCreateScrollWheelEvent(
            None,
            quartz.kCGScrollEventUnitPixel,
            2,
            round(vertical * 10),
            round(horizontal * 10),
        )
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)

    def key(self, key: str, pressed: bool) -> None:
        quartz = self._require_quartz()
        key_code = _mac_key_code(key)
        event = quartz.CGEventCreateKeyboardEvent(None, key_code, pressed)
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)

    def type_text(self, text: str) -> None:
        quartz = self._require_quartz()
        for chunk_start in range(0, len(text), 20):
            chunk = text[chunk_start : chunk_start + 20]
            event = quartz.CGEventCreateKeyboardEvent(None, 0, True)
            quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
            release = quartz.CGEventCreateKeyboardEvent(None, 0, False)
            quartz.CGEventKeyboardSetUnicodeString(release, len(chunk), chunk)
            quartz.CGEventPost(quartz.kCGHIDEventTap, release)

    def focus_window(self, window_id: str) -> None:
        try:
            process_id = int(window_id.split(":", 2)[1])
        except (IndexError, ValueError) as error:
            raise ValueError("macOS window ID does not contain a process ID") from error
        app_kit = self._load_app_kit()
        if app_kit is None:
            raise RuntimeError("AppKit is unavailable for application activation")
        application = app_kit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
            process_id
        )
        if application is None or not application.activateWithOptions_(1 << 1):
            raise RuntimeError("macOS refused to activate the target application")

    def _post_mouse(self, point: Point, button: str, pressed: bool) -> None:
        quartz = self._require_quartz()
        if button == "move":
            event_type = quartz.kCGEventMouseMoved
            mouse_button = quartz.kCGMouseButtonLeft
        else:
            event_type = {
                ("left", True): quartz.kCGEventLeftMouseDown,
                ("left", False): quartz.kCGEventLeftMouseUp,
                ("right", True): quartz.kCGEventRightMouseDown,
                ("right", False): quartz.kCGEventRightMouseUp,
                ("middle", True): quartz.kCGEventOtherMouseDown,
                ("middle", False): quartz.kCGEventOtherMouseUp,
            }[(button, pressed)]
            mouse_button = {
                "left": quartz.kCGMouseButtonLeft,
                "right": quartz.kCGMouseButtonRight,
                "middle": quartz.kCGMouseButtonCenter,
            }[button]
        event = quartz.CGEventCreateMouseEvent(
            None, event_type, (point.x, point.y), mouse_button
        )
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)

    def _capture_screen_capture_kit(
        self, display: Display, region: Rectangle | None
    ) -> Image.Image:
        screen_capture_kit = self._load_screen_capture_kit()
        assert screen_capture_kit is not None
        result: dict[str, Any] = {}
        completed = threading.Event()

        def content_ready(content: Any, error: Any) -> None:
            if error is not None:
                result["error"] = str(error)
            else:
                result["content"] = content
            completed.set()

        screen_capture_kit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            False, True, content_ready
        )
        if not completed.wait(15):
            raise RuntimeError("ScreenCaptureKit display enumeration timed out")
        if "error" in result:
            raise RuntimeError(f"ScreenCaptureKit failed: {result['error']}")
        selected = next(
            (
                item
                for item in result["content"].displays()
                if int(item.displayID()) == int(display.display_id)
            ),
            None,
        )
        if selected is None:
            raise RuntimeError("ScreenCaptureKit did not return the selected display")
        configuration = screen_capture_kit.SCStreamConfiguration.alloc().init()
        bounds = display.logical_bounds
        selected_region = region or bounds
        scale = display.scale
        configuration.setWidth_(round(selected_region.width * scale))
        configuration.setHeight_(round(selected_region.height * scale))
        configuration.setShowsCursor_(True)
        if region is not None:
            quartz = self._require_quartz()
            configuration.setSourceRect_(
                quartz.CGRectMake(
                    region.left - bounds.left,
                    region.top - bounds.top,
                    region.width,
                    region.height,
                )
            )
        content_filter = (
            screen_capture_kit.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
                selected, []
            )
        )
        completed.clear()

        def image_ready(image: Any, error: Any) -> None:
            if error is not None:
                result["error"] = str(error)
            else:
                result["image"] = image
            completed.set()

        screen_capture_kit.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
            content_filter, configuration, image_ready
        )
        if not completed.wait(15):
            raise RuntimeError("ScreenCaptureKit capture timed out")
        if "error" in result:
            raise RuntimeError(f"ScreenCaptureKit failed: {result['error']}")
        return _cg_image_to_pil(self._require_quartz(), result["image"])

    def _capture_core_graphics(
        self, display: Display, region: Rectangle | None
    ) -> Image.Image:
        quartz = self._require_quartz()
        selected = region or display.logical_bounds
        image = quartz.CGWindowListCreateImage(
            quartz.CGRectMake(selected.left, selected.top, selected.width, selected.height),
            quartz.kCGWindowListOptionOnScreenOnly,
            quartz.kCGNullWindowID,
            quartz.kCGWindowImageDefault,
        )
        if image is None:
            raise RuntimeError("Core Graphics returned no screen image")
        return _cg_image_to_pil(quartz, image)

    def _load_quartz(self) -> Any | None:
        if self._quartz is None and self.available:
            self._quartz = importlib.import_module("Quartz")
        return self._quartz

    def _require_quartz(self) -> Any:
        quartz = self._load_quartz()
        if quartz is None:
            raise RuntimeError("pyobjc-framework-Quartz is unavailable")
        return quartz

    def _load_screen_capture_kit(self) -> Any | None:
        if self._screen_capture_kit is None and importlib.util.find_spec("ScreenCaptureKit"):
            self._screen_capture_kit = importlib.import_module("ScreenCaptureKit")
        return self._screen_capture_kit

    def _load_app_kit(self) -> Any | None:
        if self._app_kit is None and importlib.util.find_spec("AppKit"):
            self._app_kit = importlib.import_module("AppKit")
        return self._app_kit

    def _load_application_services(self) -> Any | None:
        if self._application_services is None and importlib.util.find_spec(
            "ApplicationServices"
        ):
            self._application_services = importlib.import_module("ApplicationServices")
        return self._application_services


def _active_display_ids(quartz: Any, app_kit: Any | None) -> tuple[int, ...]:
    response = quartz.CGGetActiveDisplayList(32, None, None)
    if isinstance(response, tuple):
        values = next(
            (item for item in response if isinstance(item, (list, tuple))),
            (),
        )
        display_ids = tuple(int(value) for value in values)
        if display_ids:
            return display_ids
    if app_kit is not None:
        display_ids = tuple(
            int(screen.deviceDescription()["NSScreenNumber"])
            for screen in app_kit.NSScreen.screens()
        )
        if display_ids:
            return display_ids
    main_display = int(quartz.CGMainDisplayID())
    if main_display:
        return (main_display,)
    raise RuntimeError("Core Graphics returned invalid display metadata")


def _display_scales(app_kit: Any | None) -> dict[int, float]:
    if app_kit is None:
        return {}
    return {
        int(screen.deviceDescription()["NSScreenNumber"]): float(screen.backingScaleFactor())
        for screen in app_kit.NSScreen.screens()
    }


def _cg_rectangle(bounds: Any) -> tuple[int, int, int, int]:
    origin = bounds.origin if hasattr(bounds, "origin") else bounds[0]
    size = bounds.size if hasattr(bounds, "size") else bounds[1]
    return round(origin.x), round(origin.y), round(size.width), round(size.height)


def _cg_image_to_pil(quartz: Any, image: Any) -> Image.Image:
    width = int(quartz.CGImageGetWidth(image))
    height = int(quartz.CGImageGetHeight(image))
    stride = int(quartz.CGImageGetBytesPerRow(image))
    provider = quartz.CGImageGetDataProvider(image)
    data = bytes(quartz.CGDataProviderCopyData(provider))
    return Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", stride, 1).convert(
        "RGB"
    )


def _mac_key_code(key: str) -> int:
    normalized = key.casefold()
    codes = {
        "a": 0x00,
        "s": 0x01,
        "d": 0x02,
        "f": 0x03,
        "h": 0x04,
        "g": 0x05,
        "z": 0x06,
        "x": 0x07,
        "c": 0x08,
        "v": 0x09,
        "b": 0x0B,
        "q": 0x0C,
        "w": 0x0D,
        "e": 0x0E,
        "r": 0x0F,
        "y": 0x10,
        "t": 0x11,
        "1": 0x12,
        "2": 0x13,
        "3": 0x14,
        "4": 0x15,
        "6": 0x16,
        "5": 0x17,
        "=": 0x18,
        "9": 0x19,
        "7": 0x1A,
        "-": 0x1B,
        "8": 0x1C,
        "0": 0x1D,
        "]": 0x1E,
        "o": 0x1F,
        "u": 0x20,
        "[": 0x21,
        "i": 0x22,
        "p": 0x23,
        "enter": 0x24,
        "l": 0x25,
        "j": 0x26,
        "'": 0x27,
        "k": 0x28,
        ";": 0x29,
        "\\": 0x2A,
        ",": 0x2B,
        "/": 0x2C,
        "n": 0x2D,
        "m": 0x2E,
        ".": 0x2F,
        "tab": 0x30,
        "space": 0x31,
        "backspace": 0x33,
        "escape": 0x35,
        "command": 0x37,
        "win": 0x37,
        "shift": 0x38,
        "capslock": 0x39,
        "alt": 0x3A,
        "control": 0x3B,
        "rightshift": 0x3C,
        "rightalt": 0x3D,
        "rightcontrol": 0x3E,
        "left": 0x7B,
        "right": 0x7C,
        "down": 0x7D,
        "up": 0x7E,
    }
    if normalized.startswith("f") and normalized[1:].isdigit():
        function_codes = {
            1: 0x7A,
            2: 0x78,
            3: 0x63,
            4: 0x76,
            5: 0x60,
            6: 0x61,
            7: 0x62,
            8: 0x64,
            9: 0x65,
            10: 0x6D,
            11: 0x67,
            12: 0x6F,
        }
        function = int(normalized[1:])
        if function in function_codes:
            return function_codes[function]
    if normalized not in codes:
        raise ValueError(f"unsupported macOS key: {key}")
    return codes[normalized]
