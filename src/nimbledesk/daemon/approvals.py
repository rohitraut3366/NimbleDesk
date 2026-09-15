from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import time
from uuid import uuid4

from nimbledesk.protocol.models import ActionRequest


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    action_fingerprint: str
    action: ActionRequest
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class GrantedApproval:
    token: str
    approval_id: str
    action_fingerprint: str
    expires_at: float


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    status: str
    expires_at: float
    token: str | None = None


class ApprovalManager:
    def __init__(self, ttl_seconds: int = 60, clock: Callable[[], float] = time) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._pending: dict[str, PendingApproval] = {}
        self._grants: dict[str, GrantedApproval] = {}
        self._decisions: dict[str, ApprovalDecision] = {}
        self._lock = Lock()

    def request(self, action: ActionRequest) -> PendingApproval:
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
            return False
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


def _fingerprint(action: ActionRequest) -> str:
    unsigned_action = action.model_copy(update={"approval_token": None})
    return hashlib.sha256(unsigned_action.model_dump_json().encode()).hexdigest()
