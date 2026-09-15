from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from nimbledesk.backends import PortableDesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    CoordinateTarget,
    Point,
    Rectangle,
)
from tests.fakes import FakeAutomation


def test_capture_can_crop_the_latest_screen() -> None:
    backend = PortableDesktopBackend(FakeAutomation())

    capture = backend.capture("observation", Rectangle(left=100, top=200, width=300, height=250))

    with Image.open(BytesIO(base64.b64decode(capture.data_base64))) as image:
        assert image.size == (300, 250)


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
