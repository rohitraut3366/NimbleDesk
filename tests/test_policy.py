from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    PolicyDecision,
    Session,
    SessionConfig,
    SessionState,
)


def test_model_cannot_enable_input_when_host_disabled() -> None:
    session = Session(
        state=SessionState.ACTIVE,
        reason="test",
        config=SessionConfig(input_enabled=True),
        created_at=0,
        expires_at=100,
    )
    action = ActionRequest(session_id=session.session_id, kind=ActionKind.CLICK)

    outcome = ActionPolicy(host_input_enabled=False).evaluate(session, action, approved=False)

    assert outcome.decision is PolicyDecision.DENY
    assert outcome.reason == "desktop input is disabled by the host"
