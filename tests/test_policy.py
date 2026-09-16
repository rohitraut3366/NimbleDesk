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


def test_clipboard_requires_host_and_session_gates() -> None:
    action = ActionRequest(session_id="session", kind=ActionKind.READ_CLIPBOARD)
    host_disabled = Session(
        state=SessionState.ACTIVE,
        reason="test",
        config=SessionConfig(input_enabled=True, clipboard_enabled=True),
        created_at=0,
        expires_at=100,
    )
    session_disabled = host_disabled.model_copy(
        update={"config": SessionConfig(input_enabled=True)}
    )

    assert (
        ActionPolicy(host_input_enabled=True, host_clipboard_enabled=False)
        .evaluate(host_disabled, action, approved=False)
        .reason
        == "clipboard access is disabled by the host"
    )
    assert (
        ActionPolicy(host_input_enabled=True, host_clipboard_enabled=True)
        .evaluate(session_disabled, action, approved=False)
        .reason
        == "clipboard access is disabled for this session"
    )


def test_clipboard_write_and_application_launch_require_exact_approval() -> None:
    session = Session(
        state=SessionState.ACTIVE,
        reason="test",
        config=SessionConfig(
            input_enabled=True,
            clipboard_enabled=True,
            allowed_applications=frozenset({"com.example.Editor"}),
        ),
        created_at=0,
        expires_at=100,
    )
    policy = ActionPolicy(host_input_enabled=True, host_clipboard_enabled=True)
    write = ActionRequest(
        session_id=session.session_id,
        kind=ActionKind.WRITE_CLIPBOARD,
        arguments={"text": "value"},
    )
    launch = ActionRequest(
        session_id=session.session_id,
        kind=ActionKind.LAUNCH_APPLICATION,
        arguments={"application_id": "com.example.Editor"},
    )

    assert (
        policy.evaluate(session, write, approved=False).decision
        is PolicyDecision.REQUIRE_CONFIRMATION
    )
    assert (
        policy.evaluate(session, launch, approved=False).decision
        is PolicyDecision.REQUIRE_CONFIRMATION
    )
    assert policy.evaluate(session, write, approved=True).decision is PolicyDecision.ALLOW
    assert policy.evaluate(session, launch, approved=True).decision is PolicyDecision.ALLOW


def test_application_launch_requires_an_explicit_allowlist_match() -> None:
    session = Session(
        state=SessionState.ACTIVE,
        reason="test",
        config=SessionConfig(input_enabled=True),
        created_at=0,
        expires_at=100,
    )
    action = ActionRequest(
        session_id=session.session_id,
        kind=ActionKind.LAUNCH_APPLICATION,
        arguments={"application_id": "com.example.Editor"},
    )

    outcome = ActionPolicy(host_input_enabled=True).evaluate(session, action, approved=True)

    assert outcome.decision is PolicyDecision.DENY
    assert outcome.reason == "application is outside the session allowlist"


def test_window_close_requires_exact_approval() -> None:
    session = Session(
        state=SessionState.ACTIVE,
        reason="test",
        config=SessionConfig(input_enabled=True),
        created_at=0,
        expires_at=100,
    )
    action = ActionRequest(
        session_id=session.session_id,
        kind=ActionKind.CLOSE_WINDOW,
        arguments={"window_id": "fixture-window"},
    )
    policy = ActionPolicy(host_input_enabled=True)

    assert (
        policy.evaluate(session, action, approved=False).decision
        is PolicyDecision.REQUIRE_CONFIRMATION
    )
    assert policy.evaluate(session, action, approved=True).decision is PolicyDecision.ALLOW


def test_policy_summary_exposes_enforced_gates_without_secrets() -> None:
    summary = ActionPolicy(
        host_input_enabled=True, host_clipboard_enabled=False
    ).summary()

    assert summary["host_input_enabled"] is True
    assert summary["host_clipboard_enabled"] is False
    assert summary["session_path_grants_enforced"] is True
    assert summary["exact_approval_actions"] == [
        "app_command",
        "close_window",
        "launch_application",
        "write_clipboard",
    ]
