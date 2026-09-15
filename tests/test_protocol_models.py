import pytest
from pydantic import ValidationError

from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    CoordinateTarget,
    Point,
    Rectangle,
    SelectorTarget,
)


def test_rectangle_uses_half_open_bounds() -> None:
    rectangle = Rectangle(left=-100, top=0, width=200, height=100)

    assert rectangle.contains(Point(x=-100, y=0))
    assert rectangle.contains(Point(x=99, y=99))
    assert not rectangle.contains(Point(x=100, y=99))


def test_selector_requires_a_constraint() -> None:
    with pytest.raises(ValidationError, match="at least one selector"):
        SelectorTarget()


def test_action_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ActionRequest(
            session_id="session",
            kind=ActionKind.CLICK,
            target=CoordinateTarget(point=Point(x=10, y=20)),
            unexpected=True,
        )


def test_action_deadline_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ActionRequest(session_id="session", kind=ActionKind.WAIT, deadline_ms=0)
