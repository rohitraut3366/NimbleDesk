from __future__ import annotations

import os
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
    def __init__(
        self,
        host_input_enabled: bool | None = None,
        host_clipboard_enabled: bool | None = None,
    ) -> None:
        self._host_input_enabled = (
            _environment_flag("NIMBLEDESK_ENABLE_INPUT")
            if host_input_enabled is None
            else host_input_enabled
        )
        self._host_clipboard_enabled = (
            _environment_flag("NIMBLEDESK_ENABLE_CLIPBOARD")
            if host_clipboard_enabled is None
            else host_clipboard_enabled
        )

    def evaluate(self, session: Session, action: ActionRequest, approved: bool) -> PolicyOutcome:
        if session.state is not SessionState.ACTIVE:
            return PolicyOutcome(PolicyDecision.DENY, f"session is {session.state}")
        if action.kind is not ActionKind.WAIT and not self._host_input_enabled:
            return PolicyOutcome(PolicyDecision.DENY, "desktop input is disabled by the host")
        if action.kind is not ActionKind.WAIT and not session.config.input_enabled:
            return PolicyOutcome(PolicyDecision.DENY, "desktop input is disabled for this session")
        if action.kind in {ActionKind.READ_CLIPBOARD, ActionKind.WRITE_CLIPBOARD}:
            if not self._host_clipboard_enabled:
                return PolicyOutcome(
                    PolicyDecision.DENY,
                    "clipboard access is disabled by the host",
                )
            if not session.config.clipboard_enabled:
                return PolicyOutcome(
                    PolicyDecision.DENY,
                    "clipboard access is disabled for this session",
                )
        if action.kind is ActionKind.LAUNCH_APPLICATION:
            application_id = action.arguments.get("application_id")
            if not isinstance(application_id, str) or not application_id:
                return PolicyOutcome(PolicyDecision.DENY, "application ID is required")
            if application_id not in session.config.allowed_applications:
                return PolicyOutcome(
                    PolicyDecision.DENY,
                    "application is outside the session allowlist",
                )
        if (
            action.expected_application_id
            and session.config.allowed_applications
            and action.expected_application_id not in session.config.allowed_applications
        ):
            return PolicyOutcome(
                PolicyDecision.DENY,
                "application is outside the session allowlist",
            )
        if action.kind in {
            ActionKind.APP_COMMAND,
            ActionKind.LAUNCH_APPLICATION,
            ActionKind.WRITE_CLIPBOARD,
        } and not approved:
            return PolicyOutcome(
                PolicyDecision.REQUIRE_CONFIRMATION,
                "action requires exact-action approval",
            )
        return PolicyOutcome(PolicyDecision.ALLOW, "action allowed")


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
