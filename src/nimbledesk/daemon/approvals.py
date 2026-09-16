from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Lock
from time import time
from uuid import uuid4

from nimbledesk.protocol.models import ActionKind, ActionRequest


@dataclass(frozen=True)
class ApprovalEvidence:
    observation_id: str
    mime_type: str
    data_base64: str
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    action_fingerprint: str
    action: ActionRequest
    created_at: float
    expires_at: float
    evidence: ApprovalEvidence | None = None


@dataclass(frozen=True)
class GrantedApproval:
    token: str
    approval_id: str
    action_fingerprint: str
    expires_at: float


@dataclass(frozen=True)
class TemporaryApprovalRule:
    rule_id: str
    scope_fingerprint: str
    session_id: str
    description: str
    expires_at: float
    remaining_uses: int


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    status: str
    expires_at: float
    token: str | None = None
    rule_id: str | None = None


class ApprovalManager:
    def __init__(self, ttl_seconds: int = 60, clock: Callable[[], float] = time) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._pending: dict[str, PendingApproval] = {}
        self._grants: dict[str, GrantedApproval] = {}
        self._decisions: dict[str, ApprovalDecision] = {}
        self._rules: dict[str, TemporaryApprovalRule] = {}
        self._lock = Lock()

    def request(
        self, action: ActionRequest, evidence: ApprovalEvidence | None = None
    ) -> PendingApproval:
        now = self._clock()
        fingerprint = _fingerprint(action)
        with self._lock:
            self._prune()
            duplicate = next(
                (
                    pending
                    for pending in self._pending.values()
                    if secrets.compare_digest(pending.action_fingerprint, fingerprint)
                ),
                None,
            )
            if duplicate:
                return duplicate
            pending = PendingApproval(
                approval_id=str(uuid4()),
                action_fingerprint=fingerprint,
                action=action.model_copy(update={"approval_token": None}),
                created_at=now,
                expires_at=now + self._ttl_seconds,
                evidence=evidence,
            )
            self._pending[pending.approval_id] = pending
            return pending

    def approve(self, approval_id: str) -> str:
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None or self._clock() >= pending.expires_at:
                raise ValueError("approval is unknown or expired")
            token = secrets.token_urlsafe(32)
            self._grants[token] = GrantedApproval(
                token=token,
                approval_id=approval_id,
                action_fingerprint=pending.action_fingerprint,
                expires_at=pending.expires_at,
            )
            self._decisions[approval_id] = ApprovalDecision(
                approval_id=approval_id,
                status="approved",
                expires_at=pending.expires_at,
                token=token,
            )
            return token

    def reject(self, approval_id: str) -> None:
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None or self._clock() >= pending.expires_at:
                raise ValueError("approval is unknown or expired")
            self._decisions[approval_id] = ApprovalDecision(
                approval_id=approval_id,
                status="rejected",
                expires_at=pending.expires_at,
            )

    def approve_temporary(
        self, approval_id: str, duration_seconds: int = 600, maximum_uses: int = 20
    ) -> TemporaryApprovalRule:
        if not 30 <= duration_seconds <= 600:
            raise ValueError("temporary approval duration must be between 30 and 600 seconds")
        if not 1 <= maximum_uses <= 100:
            raise ValueError("temporary approval use limit must be between 1 and 100")
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None or self._clock() >= pending.expires_at:
                raise ValueError("approval is unknown or expired")
            if pending.action.kind is ActionKind.WRITE_CLIPBOARD:
                self._pending[pending.approval_id] = pending
                raise ValueError("clipboard writes require exact approval for each value")
            rule_id = str(uuid4())
            rule = TemporaryApprovalRule(
                rule_id=rule_id,
                scope_fingerprint=_scope_fingerprint(pending.action),
                session_id=pending.action.session_id,
                description=_scope_description(pending.action),
                expires_at=self._clock() + duration_seconds,
                remaining_uses=maximum_uses,
            )
            self._rules[rule_id] = rule
            self._decisions[approval_id] = ApprovalDecision(
                approval_id=approval_id,
                status="approved_rule",
                expires_at=rule.expires_at,
                rule_id=rule_id,
            )
            return rule

    def list_pending(self) -> tuple[PendingApproval, ...]:
        with self._lock:
            self._prune()
            return tuple(sorted(self._pending.values(), key=lambda item: item.created_at))

    def status(self, approval_id: str) -> ApprovalDecision:
        with self._lock:
            self._prune()
            pending = self._pending.get(approval_id)
            if pending:
                return ApprovalDecision(
                    approval_id=approval_id,
                    status="pending",
                    expires_at=pending.expires_at,
                )
            decision = self._decisions.get(approval_id)
            if decision:
                return decision
        raise ValueError("approval is unknown or expired")

    def consume(self, token: str | None, action: ActionRequest) -> bool:
        if token is None:
            return self._consume_rule(action)
        with self._lock:
            grant = self._grants.pop(token, None)
            valid = bool(
                grant
                and self._clock() < grant.expires_at
                and secrets.compare_digest(grant.action_fingerprint, _fingerprint(action))
            )
            if grant:
                self._decisions[grant.approval_id] = ApprovalDecision(
                    approval_id=grant.approval_id,
                    status="consumed" if valid else "invalidated",
                    expires_at=grant.expires_at,
                )
        return valid

    def list_rules(self) -> tuple[TemporaryApprovalRule, ...]:
        with self._lock:
            self._prune()
            return tuple(sorted(self._rules.values(), key=lambda item: item.expires_at))

    def revoke_rule(self, rule_id: str) -> None:
        with self._lock:
            if self._rules.pop(rule_id, None) is None:
                raise ValueError("temporary approval rule is unknown or expired")

    def _consume_rule(self, action: ActionRequest) -> bool:
        with self._lock:
            self._prune()
            fingerprint = _scope_fingerprint(action)
            match = next(
                (
                    rule
                    for rule in self._rules.values()
                    if secrets.compare_digest(rule.scope_fingerprint, fingerprint)
                ),
                None,
            )
            if match is None:
                return False
            if match.remaining_uses == 1:
                del self._rules[match.rule_id]
            else:
                self._rules[match.rule_id] = replace(
                    match, remaining_uses=match.remaining_uses - 1
                )
            return True

    def revoke_all(self) -> None:
        with self._lock:
            now = self._clock()
            for pending in self._pending.values():
                self._decisions[pending.approval_id] = ApprovalDecision(
                    approval_id=pending.approval_id,
                    status="rejected",
                    expires_at=max(now + 1, pending.expires_at),
                )
            for grant in self._grants.values():
                self._decisions[grant.approval_id] = ApprovalDecision(
                    approval_id=grant.approval_id,
                    status="invalidated",
                    expires_at=max(now + 1, grant.expires_at),
                )
            self._pending.clear()
            self._grants.clear()
            self._rules.clear()

    def _prune(self) -> None:
        now = self._clock()
        self._pending = {
            approval_id: pending
            for approval_id, pending in self._pending.items()
            if now < pending.expires_at
        }
        self._decisions = {
            approval_id: decision
            for approval_id, decision in self._decisions.items()
            if now < decision.expires_at
        }
        self._grants = {
            token: grant for token, grant in self._grants.items() if now < grant.expires_at
        }
        self._rules = {
            rule_id: rule
            for rule_id, rule in self._rules.items()
            if now < rule.expires_at and rule.remaining_uses > 0
        }


def _fingerprint(action: ActionRequest) -> str:
    unsigned_action = action.model_copy(update={"approval_token": None})
    return hashlib.sha256(unsigned_action.model_dump_json().encode()).hexdigest()


def _scope_fingerprint(action: ActionRequest) -> str:
    arguments = action.arguments
    scope = "\n".join(
        (
            action.session_id,
            action.kind.value,
            action.expected_application_id or "",
            action.expected_window_id or "",
            str(arguments.get("adapter_id", "")),
            str(arguments.get("command", "")),
            str(arguments.get("application_id", "")),
            str(arguments.get("window_id", "")),
        )
    )
    return hashlib.sha256(scope.encode()).hexdigest()


def _scope_description(action: ActionRequest) -> str:
    parts = [action.kind.value]
    if action.expected_application_id:
        parts.append(f"application={action.expected_application_id}")
    if action.expected_window_id:
        parts.append(f"window={action.expected_window_id}")
    if action.arguments.get("adapter_id"):
        parts.append(f"adapter={action.arguments['adapter_id']}")
    if action.arguments.get("command"):
        parts.append(f"command={action.arguments['command']}")
    return ", ".join(parts)
