from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from nimbledesk.backends.macos import MacOSController, _cg_image_to_pil, _mac_key_code
from nimbledesk.protocol.models import Capability, PermissionState, Point


class PermissionQuartz:
    @staticmethod
    def CGPreflightScreenCaptureAccess() -> bool:
        return False



class PermissionApplicationServices:
    @staticmethod
    def AXIsProcessTrusted() -> bool:
        return True


def test_macos_permissions_keep_capture_and_input_independent() -> None:
    permissions = MacOSController(
        quartz=PermissionQuartz(),
        application_services=PermissionApplicationServices(),
    ).permissions()

    assert permissions[Capability.SCREEN_CAPTURE] is PermissionState.DENIED
    assert permissions[Capability.POINTER] is PermissionState.GRANTED
    assert permissions[Capability.KEYBOARD] is PermissionState.GRANTED


def test_macos_key_map_supports_modifiers_navigation_and_functions() -> None:
    assert _mac_key_code("command") == 0x37
    assert _mac_key_code("left") == 0x7B
    assert _mac_key_code("F12") == 0x6F


def test_core_graphics_image_conversion_honors_row_stride() -> None:
    class Quartz:
        @staticmethod
        def CGImageGetWidth(_image: object) -> int:
            return 2

        @staticmethod
        def CGImageGetHeight(_image: object) -> int:
            return 1

        @staticmethod
        def CGImageGetBytesPerRow(_image: object) -> int:
            return 12

        @staticmethod
        def CGImageGetDataProvider(_image: object) -> object:
            return object()

        @staticmethod
        def CGDataProviderCopyData(_provider: object) -> bytes:
            return bytes((0, 0, 255, 255, 0, 255, 0, 255, 9, 9, 9, 9))

    converted = _cg_image_to_pil(Quartz(), SimpleNamespace())

    assert isinstance(converted, Image.Image)
    assert converted.getpixel((0, 0)) == (255, 0, 0)
    assert converted.getpixel((1, 0)) == (0, 255, 0)


def test_macos_clipboard_uses_native_pasteboard() -> None:
    class Pasteboard:
        value = "existing"

        @classmethod
        def generalPasteboard(cls) -> Pasteboard:
            return cls()

        def stringForType_(self, _type: str) -> str:
            return self.value

        def clearContents(self) -> None:
            self.value = ""

        def writeObjects_(self, values: list[str]) -> bool:
            type(self).value = values[0]
            return True

    class AppKit:
        NSPasteboard = Pasteboard
        NSPasteboardTypeString = "public.utf8-plain-text"

    controller = MacOSController(app_kit=AppKit())

    assert controller.read_clipboard() == "existing"
    controller.write_clipboard("updated")
    assert controller.read_clipboard() == "updated"


def test_macos_window_lifecycle_uses_accessibility_api() -> None:
    class Accessibility:
        kAXFocusedWindowAttribute = "focused"
        kAXValueCGPointType = "point"
        kAXValueCGSizeType = "size"
        kAXPositionAttribute = "position"
        kAXSizeAttribute = "size-attribute"
        kAXMinimizedAttribute = "minimized"
        kAXCloseAction = "close"
        calls: list[tuple[object, ...]] = []

        @staticmethod
        def AXIsProcessTrusted() -> bool:
            return True

        @classmethod
        def AXUIElementCreateApplication(cls, process_id: int) -> object:
            cls.calls.append(("application", process_id))
            return "application"

        @staticmethod
        def AXUIElementCopyAttributeValue(
            _application: object, _attribute: str, _error: object
        ) -> tuple[int, str]:
            return 0, "window"

        @staticmethod
        def AXValueCreate(value_type: str, value: tuple[int, int]) -> tuple[object, ...]:
            return value_type, *value

        @classmethod
        def AXUIElementSetAttributeValue(
            cls, window: object, attribute: object, value: object
        ) -> int:
            cls.calls.append(("set", window, attribute, value))
            return 0

        @classmethod
        def AXUIElementPerformAction(cls, window: object, action: object) -> int:
            cls.calls.append(("action", window, action))
            return 0

    controller = MacOSController(application_services=Accessibility())

    controller.move_window("pid:42:window", Point(x=10, y=20))
    controller.resize_window("pid:42:window", 800, 600)
    controller.minimize_window("pid:42:window")
    controller.maximize_window("pid:42:window")
    controller.close_window("pid:42:window")

    assert ("set", "window", "position", ("point", 10, 20)) in Accessibility.calls
    assert ("set", "window", "size-attribute", ("size", 800, 600)) in Accessibility.calls
    assert ("set", "window", "minimized", True) in Accessibility.calls
    assert ("set", "window", "AXFullScreen", True) in Accessibility.calls
    assert ("action", "window", "close") in Accessibility.calls
