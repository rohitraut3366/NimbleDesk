from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from PIL import Image

from nimbledesk.backends.wayland import (
    PortalStream,
    WaylandPortalBackend,
    XdgDesktopPortalController,
    _keysym,
    _portal_streams,
)
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Capability,
    CoordinateTarget,
    PermissionState,
    Point,
    Rectangle,
)


class FakePortalController:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.started = False
        self.streams = (
            PortalStream(17, Rectangle(left=-800, top=0, width=800, height=600)),
            PortalStream(23, Rectangle(left=0, top=0, width=1280, height=720)),
        )
        self.calls: list[tuple[object, ...]] = []

    def start(self) -> None:
        if self.failure:
            raise RuntimeError(self.failure)
        self.started = True

    def capture(self, stream: PortalStream) -> Image.Image:
        self.calls.append(("capture", stream.node_id))
        return Image.new("RGB", (stream.bounds.width, stream.bounds.height), "navy")

    def pointer_absolute(self, stream: PortalStream, point: Point) -> None:
        self.calls.append(("move", stream.node_id, point.x, point.y))

    def pointer_button(self, button: str, pressed: bool) -> None:
        self.calls.append(("button", button, pressed))

    def pointer_axis(self, horizontal: float, vertical: float) -> None:
        self.calls.append(("axis", horizontal, vertical))

    def keyboard_key(self, key: str, pressed: bool) -> None:
        self.calls.append(("key", key, pressed))

    def close(self) -> None:
        self.calls.append(("close",))


def test_wayland_portal_observes_shared_monitors_and_crops_capture() -> None:
    controller = FakePortalController()
    backend = WaylandPortalBackend(controller)

    observation = backend.observe()
    capture = backend.capture(
        observation.observation_id,
        Rectangle(left=100, top=50, width=320, height=180),
    )

    assert controller.started
    assert observation.displays[0].logical_bounds.left == -800
    assert observation.permissions[Capability.SCREEN_CAPTURE] is PermissionState.GRANTED
    assert capture.width == 320
    assert capture.height == 180
    assert controller.calls == [("capture", 23)]


def test_wayland_portal_executes_pointer_scroll_and_keyboard() -> None:
    controller = FakePortalController()
    backend = WaylandPortalBackend(controller)
    backend.observe()

    click = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.CLICK,
            target=CoordinateTarget(point=Point(x=-100, y=200)),
            arguments={"button": "right", "clicks": 2},
        )
    )
    scroll = backend.execute(
        ActionRequest(session_id="session", kind=ActionKind.SCROLL, arguments={"amount": 3})
    )
    hotkey = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.HOTKEY,
            arguments={"keys": ["control", "s"]},
        )
    )

    assert click.status is ActionStatus.COMPLETED
    assert scroll.status is ActionStatus.COMPLETED
    assert hotkey.status is ActionStatus.COMPLETED
    assert controller.calls == [
        ("move", 17, -100, 200),
        ("button", "right", True),
        ("button", "right", False),
        ("button", "right", True),
        ("button", "right", False),
        ("axis", 0, -30.0),
        ("key", "control", True),
        ("key", "s", True),
        ("key", "s", False),
        ("key", "control", False),
    ]


def test_wayland_portal_reports_denied_consent_without_advertising_capability() -> None:
    backend = WaylandPortalBackend(FakePortalController("sharing was denied"))

    observation = backend.observe()

    assert not observation.capabilities
    assert observation.permissions[Capability.POINTER] is PermissionState.DENIED
    assert observation.warnings == ("sharing was denied",)


def test_wayland_keysym_supports_navigation_function_and_unicode_keys() -> None:
    assert _keysym("enter") == 0xFF0D
    assert _keysym("F12") == 0xFFC9
    assert _keysym("é") == 0xE9
    assert _keysym("€") == 0x010020AC


def test_portal_stream_metadata_preserves_monitor_geometry() -> None:
    class Variant:
        def __init__(self, value: object) -> None:
            self.value = value

    streams = _portal_streams(
        [(41, {"size": Variant((2560, 1440)), "position": Variant((-2560, 0))})]
    )

    assert streams == (
        PortalStream(41, Rectangle(left=-2560, top=0, width=2560, height=1440)),
    )


def test_portal_request_listener_is_registered_before_fast_response() -> None:
    class FakeBus:
        unique_name = ":1.77"

        def __init__(self) -> None:
            self.handler: Any | None = None

        def add_message_handler(self, handler: Any) -> None:
            self.handler = handler

        def remove_message_handler(self, handler: Any) -> None:
            assert handler is self.handler
            self.handler = None

    controller = XdgDesktopPortalController()
    bus = FakeBus()
    controller._bus = bus
    token = "request_token"
    path = f"/org/freedesktop/portal/desktop/request/1_77/{token}"

    async def immediate_response() -> str:
        await asyncio.sleep(0)
        assert bus.handler is not None
        bus.handler(
            SimpleNamespace(
                path=path,
                interface="org.freedesktop.portal.Request",
                member="Response",
                body=[0, {"session_handle": "session"}],
            )
        )
        return path

    result = controller._call(controller._request(immediate_response(), token))
    controller.close()

    assert result == {"session_handle": "session"}
    assert bus.handler is None
