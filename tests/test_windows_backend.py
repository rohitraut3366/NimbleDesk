from __future__ import annotations

from PIL import Image

from nimbledesk.backends.windows import (
    WindowsController,
    _layout_displays,
    _windows_virtual_key,
)
from nimbledesk.protocol.models import Capability, PermissionState, Point, Rectangle


class FakeWindowsAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def displays(self) -> tuple[tuple[int, Rectangle, float, bool], ...]:
        return (
            (1, Rectangle(left=-1920, top=0, width=1920, height=1080), 1.0, False),
            (2, Rectangle(left=0, top=0, width=2560, height=1440), 1.5, True),
        )

    def cursor(self) -> Point:
        return Point(x=300, y=150)

    def capture(self, bounds: Rectangle) -> Image.Image:
        self.calls.append(("capture", bounds))
        return Image.new("RGB", (bounds.width, bounds.height), "blue")

    def move_pointer(self, point: Point) -> None:
        self.calls.append(("move", point))

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None:
        self.calls.append(("button", point, button, pressed))

    def scroll(self, horizontal: float, vertical: float) -> None:
        self.calls.append(("scroll", horizontal, vertical))

    def key(self, virtual_key: int, pressed: bool) -> None:
        self.calls.append(("key", virtual_key, pressed))

    def type_text(self, text: str) -> None:
        self.calls.append(("text", text))

    def focus_window(self, handle: int) -> None:
        self.calls.append(("focus", handle))


def test_windows_layout_preserves_adjacency_across_mixed_dpi() -> None:
    displays = _layout_displays(FakeWindowsAPI().displays())

    assert displays[0].logical_bounds == Rectangle(
        left=-1920, top=0, width=1920, height=1080
    )
    assert displays[1].logical_bounds == Rectangle(
        left=0, top=0, width=1707, height=960
    )
    assert displays[0].logical_bounds.left + displays[0].logical_bounds.width == 0
    assert displays[1].scale == 1.5


def test_windows_controller_transforms_logical_capture_and_input_to_pixels() -> None:
    api = FakeWindowsAPI()
    controller = WindowsController(api)
    displays = controller.displays()

    assert controller.cursor() == Point(x=200, y=100)
    image = controller.capture(
        displays[1], Rectangle(left=100, top=50, width=200, height=100)
    )
    controller.move_pointer(Point(x=200, y=100))
    controller.pointer_button(Point(x=200, y=100), "left", True)
    controller.focus_window("uia-window:4242")

    assert image.size == (300, 150)
    assert api.calls == [
        ("capture", Rectangle(left=150, top=75, width=300, height=150)),
        ("move", Point(x=300, y=150)),
        ("button", Point(x=300, y=150), "left", True),
        ("focus", 4242),
    ]


def test_windows_controller_reports_native_capabilities() -> None:
    permissions = WindowsController(FakeWindowsAPI()).permissions()

    assert permissions == {
        Capability.SCREEN_CAPTURE: PermissionState.GRANTED,
        Capability.POINTER: PermissionState.GRANTED,
        Capability.KEYBOARD: PermissionState.GRANTED,
        Capability.WINDOWS: PermissionState.GRANTED,
    }


def test_windows_virtual_keys_cover_shortcuts_navigation_and_functions() -> None:
    assert _windows_virtual_key("control") == 0x11
    assert _windows_virtual_key("s") == 0x53
    assert _windows_virtual_key("left") == 0x25
    assert _windows_virtual_key("F12") == 0x7B
