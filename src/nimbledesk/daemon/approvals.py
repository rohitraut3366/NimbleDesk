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
    expires_at: float


@dataclass(frozen=True)
class GrantedApproval:
    token: str
    action_fingerprint: str
    expires_at: float


class ApprovalManager:
    def __init__(self, ttl_seconds: int = 60, clock: Callable[[], float] = time) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._pending: dict[str, PendingApproval] = {}
        self._grants: dict[str, GrantedApproval] = {}
        self._lock = Lock()

    def request(self, action: ActionRequest) -> PendingApproval:
        pending = PendingApproval(
            approval_id=str(uuid4()),
            action_fingerprint=_fingerprint(action),
            expires_at=self._clock() + self._ttl_seconds,
        )
        with self._lock:
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
                action_fingerprint=pending.action_fingerprint,
                expires_at=pending.expires_at,
            )
            return token

    def consume(self, token: str | None, action: ActionRequest) -> bool:
        if token is None:
            return False
        with self._lock:
            grant = self._grants.pop(token, None)
        return bool(
            grant
            and self._clock() < grant.expires_at
            and secrets.compare_digest(grant.action_fingerprint, _fingerprint(action))
        )


def _fingerprint(action: ActionRequest) -> str:
    unsigned_action = action.model_copy(update={"approval_token": None})
    return hashlib.sha256(unsigned_action.model_dump_json().encode()).hexdigest()
