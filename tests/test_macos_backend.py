from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from nimbledesk.backends.macos import MacOSController, _cg_image_to_pil, _mac_key_code
from nimbledesk.protocol.models import Capability, PermissionState


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
