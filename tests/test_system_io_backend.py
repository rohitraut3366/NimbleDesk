from __future__ import annotations

from PIL import Image

from nimbledesk.backends.system_io import SystemIOBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Capability,
    CoordinateTarget,
    Display,
    PermissionState,
    Point,
    Rectangle,
    SelectorTarget,
)


class FakeSystemController:
    controller_id = "fixture-native"

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.pointer = Point(x=-20, y=10)

    def permissions(self) -> dict[Capability, PermissionState]:
        return {
            Capability.SCREEN_CAPTURE: PermissionState.GRANTED,
            Capability.POINTER: PermissionState.GRANTED,
            Capability.KEYBOARD: PermissionState.GRANTED,
            Capability.WINDOWS: PermissionState.GRANTED,
        }

    def displays(self) -> tuple[Display, ...]:
        return (
            Display(
                display_id="left",
                logical_bounds=Rectangle(left=-800, top=0, width=800, height=600),
                physical_bounds=Rectangle(left=-1600, top=0, width=1600, height=1200),
                scale=2,
            ),
            Display(
                display_id="primary",
                logical_bounds=Rectangle(left=0, top=0, width=1280, height=720),
                physical_bounds=Rectangle(left=0, top=0, width=1280, height=720),
                primary=True,
            ),
        )

    def cursor(self) -> Point:
        return self.pointer

    def capture(self, display: Display, region: Rectangle | None) -> Image.Image:
        self.calls.append(("capture", display.display_id, region))
        dimensions = (region.width, region.height) if region else (
            display.physical_bounds.width,
            display.physical_bounds.height,
        )
        return Image.new("RGB", dimensions, "navy")

    def move_pointer(self, point: Point) -> None:
        self.pointer = point
        self.calls.append(("move", point.x, point.y))

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None:
        self.calls.append(("button", point.x, point.y, button, pressed))

    def scroll(self, horizontal: float, vertical: float) -> None:
        self.calls.append(("scroll", horizontal, vertical))

    def key(self, key: str, pressed: bool) -> None:
        self.calls.append(("key", key, pressed))

    def type_text(self, text: str) -> None:
        self.calls.append(("text", text))

    def focus_window(self, window_id: str) -> None:
        self.calls.append(("focus", window_id))


def test_native_io_reports_mixed_scale_displays_and_captures_region() -> None:
    controller = FakeSystemController()
    backend = SystemIOBackend(controller)

    observation = backend.observe()
    capture = backend.capture(
        observation.observation_id,
        Rectangle(left=-700, top=50, width=300, height=200),
    )

    assert observation.displays[0].scale == 2
    assert observation.cursor == Point(x=-20, y=10)
    assert capture.width == 300
    assert capture.height == 200
    assert controller.calls == [
        (
            "capture",
            "left",
            Rectangle(left=-700, top=50, width=300, height=200),
        )
    ]


def test_native_io_executes_pointer_keyboard_scroll_and_focus() -> None:
    controller = FakeSystemController()
    backend = SystemIOBackend(controller)
    backend.observe()

    click = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.CLICK,
            target=CoordinateTarget(point=Point(x=100, y=200)),
            arguments={"button": "right", "clicks": 2, "interval": 0},
        )
    )
    backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.HOTKEY,
            arguments={"keys": ["control", "s"]},
        )
    )
    backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.SCROLL,
            arguments={"amount": -3, "horizontal": 2},
        )
    )
    focus = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.FOCUS_WINDOW,
            target=SelectorTarget(window_id="pid:42:window"),
        )
    )

    assert click.status is ActionStatus.COMPLETED
    assert focus.status is ActionStatus.COMPLETED
    assert controller.calls == [
        ("move", 100, 200),
        ("button", 100, 200, "right", True),
        ("button", 100, 200, "right", False),
        ("button", 100, 200, "right", True),
        ("button", 100, 200, "right", False),
        ("key", "control", True),
        ("key", "s", True),
        ("key", "s", False),
        ("key", "control", False),
        ("scroll", 2.0, -3.0),
        ("focus", "pid:42:window"),
    ]


def test_native_io_rejects_cross_display_capture() -> None:
    backend = SystemIOBackend(FakeSystemController())
    backend.observe()

    try:
        backend.capture("observation", Rectangle(left=-10, top=0, width=20, height=20))
    except ValueError as error:
        assert "one display" in str(error)
    else:
        raise AssertionError("cross-display capture must fail")
