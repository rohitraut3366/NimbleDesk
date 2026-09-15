from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from nimbledesk.backends import PortableDesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    CaptureOptions,
    CoordinateTarget,
    ElementTarget,
    Point,
    Rectangle,
)
from tests.fakes import FakeAutomation


def test_capture_can_crop_the_latest_screen() -> None:
    backend = PortableDesktopBackend(FakeAutomation())

    capture = backend.capture("observation", Rectangle(left=100, top=200, width=300, height=250))

    with Image.open(BytesIO(base64.b64decode(capture.data_base64))) as image:
        assert image.size == (300, 250)


def test_capture_respects_image_budget() -> None:
    backend = PortableDesktopBackend(FakeAutomation())

    capture = backend.capture(
        "observation",
        options=CaptureOptions(max_width=360, max_height=225, jpeg_quality=60),
    )

    assert capture.mime_type == "image/jpeg"
    assert capture.width == 360
    assert capture.height == 225


def test_click_validates_coordinates_before_input() -> None:
    automation = FakeAutomation()
    backend = PortableDesktopBackend(automation)
    request = ActionRequest(
        session_id="session",
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=1440, y=20)),
    )

    result = backend.execute(request)

    assert result.status is ActionStatus.FAILED
    assert automation.calls == []


def test_click_reaches_automation_backend() -> None:
    automation = FakeAutomation()
    backend = PortableDesktopBackend(automation)
    request = ActionRequest(
        session_id="session",
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=100, y=200)),
        arguments={"button": "right", "clicks": 2},
    )

    result = backend.execute(request)

    assert result.status is ActionStatus.COMPLETED
    assert automation.calls == [
        ("click", (100, 200), {"clicks": 2, "interval": 0.1, "button": "right"})
    ]


def test_semantic_element_reports_unavailable_without_native_provider() -> None:
    backend = PortableDesktopBackend(FakeAutomation())
    request = ActionRequest(
        session_id="session",
        kind=ActionKind.CLICK,
        target=ElementTarget(observation_id="observation", element_id="button"),
    )

    result = backend.execute(request)

    assert result.status is ActionStatus.CAPABILITY_UNAVAILABLE
    assert "semantic accessibility" in result.message
