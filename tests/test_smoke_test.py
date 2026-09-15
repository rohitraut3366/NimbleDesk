import base64
import hashlib

import pytest

from nimbledesk.protocol.models import Point
from nimbledesk.testing.smoke import bounded_test_target, points_are_close, validate_capture


def test_capture_validation_checks_hash_and_dimensions() -> None:
    image_bytes = b"small-image"
    capture = {
        "data_base64": base64.b64encode(image_bytes).decode(),
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "width": 640,
        "height": 400,
    }

    validate_capture(capture)

    capture["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        validate_capture(capture)


def test_pointer_target_stays_inside_display() -> None:
    assert bounded_test_target(Point(x=100, y=200), 1440, 900) == Point(x=110, y=210)
    assert bounded_test_target(Point(x=1439, y=899), 1440, 900) == Point(x=1429, y=889)


def test_pointer_comparison_allows_small_os_rounding() -> None:
    assert points_are_close(Point(x=100, y=200), Point(x=102, y=198))
    assert not points_are_close(Point(x=100, y=200), Point(x=103, y=200))
