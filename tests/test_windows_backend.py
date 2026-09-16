from __future__ import annotations

import numpy as np
from PIL import Image

from nimbledesk.backends.windows import (
    WindowsCaptureProvider,
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

    def move_window(self, handle: int, point: Point) -> None:
        self.calls.append(("move-window", handle, point))

    def resize_window(self, handle: int, width: int, height: int) -> None:
        self.calls.append(("resize-window", handle, width, height))

    def minimize_window(self, handle: int) -> None:
        self.calls.append(("minimize-window", handle))

    def maximize_window(self, handle: int) -> None:
        self.calls.append(("maximize-window", handle))

    def close_window(self, handle: int) -> None:
        self.calls.append(("close-window", handle))

    def read_clipboard(self) -> str:
        self.calls.append(("clipboard-read",))
        return "windows clipboard"

    def write_clipboard(self, text: str) -> None:
        self.calls.append(("clipboard-write", text))

    def launch_application(self, application_id: str) -> None:
        self.calls.append(("launch", application_id))


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
    controller.move_window("uia-window:4242", Point(x=-40, y=20))
    controller.resize_window("uia-window:4242", 900, 700)
    controller.minimize_window("uia-window:4242")
    controller.maximize_window("uia-window:4242")
    controller.close_window("uia-window:4242")

    assert image.size == (300, 150)
    assert api.calls == [
        ("capture", Rectangle(left=150, top=75, width=300, height=150)),
        ("move", Point(x=300, y=150)),
        ("button", Point(x=300, y=150), "left", True),
        ("focus", 4242),
        ("move-window", 4242, Point(x=-40, y=20)),
        ("resize-window", 4242, 900, 700),
        ("minimize-window", 4242),
        ("maximize-window", 4242),
        ("close-window", 4242),
    ]


def test_windows_controller_reports_native_capabilities() -> None:
    permissions = WindowsController(FakeWindowsAPI()).permissions()

    assert permissions == {
        Capability.SCREEN_CAPTURE: PermissionState.GRANTED,
        Capability.POINTER: PermissionState.GRANTED,
        Capability.KEYBOARD: PermissionState.GRANTED,
        Capability.WINDOWS: PermissionState.GRANTED,
        Capability.CLIPBOARD: PermissionState.GRANTED,
    }


def test_windows_virtual_keys_cover_shortcuts_navigation_and_functions() -> None:
    assert _windows_virtual_key("control") == 0x11
    assert _windows_virtual_key("s") == 0x53
    assert _windows_virtual_key("left") == 0x25
    assert _windows_virtual_key("F12") == 0x7B


def test_windows_capture_prefers_winrt_and_uses_display_local_region() -> None:
    class Camera:
        region: tuple[int, int, int, int] | None = None

        @classmethod
        def grab(cls, *, region: tuple[int, int, int, int]) -> np.ndarray:
            cls.region = region
            return np.zeros((100, 200, 3), dtype=np.uint8)

    class Dxcam:
        selected_output: int | None = None
        selected_backend: str | None = None

        @classmethod
        def create(cls, **arguments: object) -> Camera:
            cls.selected_output = int(arguments["output_idx"])
            cls.selected_backend = str(arguments["backend"])
            return Camera()

    displays = FakeWindowsAPI().displays()
    capture = WindowsCaptureProvider(Dxcam()).capture(
        Rectangle(left=100, top=50, width=200, height=100), displays
    )

    assert Dxcam.selected_output == 1
    assert Dxcam.selected_backend == "winrt"
    assert Camera.region == (100, 50, 300, 150)
    assert capture.size == (200, 100)


def test_windows_capture_falls_back_from_winrt_to_desktop_duplication() -> None:
    class Camera:
        @staticmethod
        def grab(*, region: tuple[int, int, int, int]) -> np.ndarray:
            return np.zeros((region[3] - region[1], region[2] - region[0], 3), dtype=np.uint8)

    class Dxcam:
        attempts: list[str] = []

        @classmethod
        def create(cls, **arguments: object) -> Camera:
            backend = str(arguments["backend"])
            cls.attempts.append(backend)
            if backend == "winrt":
                raise RuntimeError("Windows Graphics Capture unavailable")
            return Camera()

    image = WindowsCaptureProvider(Dxcam()).capture(
        Rectangle(left=-100, top=20, width=50, height=40),
        FakeWindowsAPI().displays(),
    )

    assert Dxcam.attempts == ["winrt", "dxgi"]
    assert image.size == (50, 40)
