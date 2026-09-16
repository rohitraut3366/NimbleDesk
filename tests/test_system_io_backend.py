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
            Capability.CLIPBOARD: PermissionState.GRANTED,
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

    def move_window(self, window_id: str, point: Point) -> None:
        self.calls.append(("move-window", window_id, point))

    def resize_window(self, window_id: str, width: int, height: int) -> None:
        self.calls.append(("resize-window", window_id, width, height))

    def minimize_window(self, window_id: str) -> None:
        self.calls.append(("minimize-window", window_id))

    def maximize_window(self, window_id: str) -> None:
        self.calls.append(("maximize-window", window_id))

    def close_window(self, window_id: str) -> None:
        self.calls.append(("close-window", window_id))

    def read_clipboard(self) -> str:
        self.calls.append(("clipboard-read",))
        return "fixture clipboard"

    def write_clipboard(self, text: str) -> None:
        self.calls.append(("clipboard-write", text))

    def launch_application(self, application_id: str) -> None:
        self.calls.append(("launch", application_id))


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


def test_native_io_bounds_clipboard_and_launches_application() -> None:
    controller = FakeSystemController()
    backend = SystemIOBackend(controller)

    clipboard = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.READ_CLIPBOARD,
            arguments={"maximum_characters": 7},
        )
    )
    written = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.WRITE_CLIPBOARD,
            arguments={"text": "new value"},
        )
    )
    launched = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.LAUNCH_APPLICATION,
            arguments={"application_id": "com.example.Editor"},
        )
    )

    assert clipboard.data["text"] == "fixture"
    assert clipboard.data["truncated"] is True
    assert written.data["characters"] == 9
    assert launched.status is ActionStatus.COMPLETED
    assert controller.calls == [
        ("clipboard-read",),
        ("clipboard-write", "new value"),
        ("launch", "com.example.Editor"),
    ]


def test_native_io_controls_window_lifecycle() -> None:
    controller = FakeSystemController()
    backend = SystemIOBackend(controller)
    window_id = "pid:42:window"

    requests = (
        ActionRequest(
            session_id="session",
            kind=ActionKind.MOVE_WINDOW,
            arguments={"window_id": window_id, "left": -20, "top": 30},
        ),
        ActionRequest(
            session_id="session",
            kind=ActionKind.RESIZE_WINDOW,
            arguments={"window_id": window_id, "width": 900, "height": 700},
        ),
        *(
            ActionRequest(
                session_id="session", kind=kind, arguments={"window_id": window_id}
            )
            for kind in (
                ActionKind.MINIMIZE_WINDOW,
                ActionKind.MAXIMIZE_WINDOW,
                ActionKind.CLOSE_WINDOW,
            )
        ),
    )

    assert all(backend.execute(request).status is ActionStatus.COMPLETED for request in requests)
    assert controller.calls == [
        ("move-window", window_id, Point(x=-20, y=30)),
        ("resize-window", window_id, 900, 700),
        ("minimize-window", window_id),
        ("maximize-window", window_id),
        ("close-window", window_id),
    ]
