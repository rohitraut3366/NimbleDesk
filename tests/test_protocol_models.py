import pytest
from pydantic import ValidationError

from nimbledesk.protocol.models import (
    ActionCondition,
    ActionKind,
    ActionRequest,
    ConditionKind,
    CoordinateTarget,
    Point,
    Rectangle,
    SelectorTarget,
    VisualTarget,
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


def test_action_contract_accepts_explicit_conditions_and_execution_policy() -> None:
    action = ActionRequest(
        session_id="session",
        kind=ActionKind.CLICK,
        preconditions=(
            ActionCondition(kind=ConditionKind.ACTIVE_APPLICATION, value="editor.app"),
        ),
        postconditions=(
            ActionCondition(kind=ConditionKind.ELEMENT_PRESENT, value="Export complete"),
        ),
        maximum_retries=2,
        idempotency="idempotent",
        capture_after=True,
        risk_context="Exports into the granted project directory",
    )

    assert action.maximum_retries == 2
    assert action.preconditions[0].value == "editor.app"
    assert action.postconditions[0].kind is ConditionKind.ELEMENT_PRESENT


def test_visual_target_requires_sha256_signature() -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        VisualTarget(
            observation_id="observation",
            bounds=Rectangle(left=0, top=0, width=10, height=10),
            signature="not-a-signature",
            confidence=0.9,
        )
