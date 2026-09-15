from __future__ import annotations

from dataclasses import dataclass

from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    PolicyDecision,
    Session,
    SessionState,
)


@dataclass(frozen=True)
class PolicyOutcome:
    decision: PolicyDecision
    reason: str


class ActionPolicy:
    def evaluate(self, session: Session, action: ActionRequest, approved: bool) -> PolicyOutcome:
        if session.state is not SessionState.ACTIVE:
            return PolicyOutcome(PolicyDecision.DENY, f"session is {session.state}")
        if action.kind is not ActionKind.WAIT and not session.config.input_enabled:
            return PolicyOutcome(PolicyDecision.DENY, "desktop input is disabled for this session")
        if (
            action.expected_application_id
            and session.config.allowed_applications
            and action.expected_application_id not in session.config.allowed_applications
        ):
            return PolicyOutcome(
                PolicyDecision.DENY,
                "application is outside the session allowlist",
            )
        if action.kind is ActionKind.APP_COMMAND and not approved:
            return PolicyOutcome(
                PolicyDecision.REQUIRE_CONFIRMATION,
                "application command requires exact-action approval",
            )
        return PolicyOutcome(PolicyDecision.ALLOW, "action allowed")
