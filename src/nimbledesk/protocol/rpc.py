from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from time import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from nimbledesk.protocol.models import ProtocolModel


class RpcRequest(ProtocolModel):
    protocol_version: Literal["1.0.0"] = "1.0.0"
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    caller_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    session_id: str | None = Field(default=None, max_length=128)
    timestamp_ms: int
    deadline_at_ms: int
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    method: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    params: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def deadline_is_bounded(self) -> RpcRequest:
        if self.deadline_at_ms <= self.timestamp_ms:
            raise ValueError("RPC deadline must be after its timestamp")
        if self.deadline_at_ms - self.timestamp_ms > 120_000:
            raise ValueError("RPC deadline cannot exceed 120 seconds")
        parameter_session = self.params.get("session_id")
        expected_session = str(parameter_session) if parameter_session is not None else None
        if self.session_id != expected_session:
            raise ValueError("RPC envelope session does not match request parameters")
        return self


class RpcResponse(ProtocolModel):
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class ConnectionInfo(ProtocolModel):
    protocol: Literal["nimbledesk-jsonrpc-v1"] = "nimbledesk-jsonrpc-v1"
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(ge=1, le=65_535)
    secret: str = Field(min_length=43)


def create_request(
    method: str,
    params: dict[str, Any],
    secret: str,
    *,
    caller_id: str = "local-client",
    deadline_ms: int = 10_000,
) -> RpcRequest:
    if not 1 <= deadline_ms <= 120_000:
        raise ValueError("RPC deadline must be between 1 and 120000 milliseconds")
    request_id = str(uuid4())
    timestamp_ms = int(time() * 1_000)
    deadline_at_ms = timestamp_ms + deadline_ms
    session_value = params.get("session_id")
    session_id = str(session_value) if session_value is not None else None
    nonce = secrets.token_urlsafe(24)
    signature = _signature(
        secret,
        "1.0.0",
        request_id,
        caller_id,
        session_id,
        timestamp_ms,
        deadline_at_ms,
        nonce,
        method,
        params,
    )
    return RpcRequest(
        request_id=request_id,
        caller_id=caller_id,
        session_id=session_id,
        timestamp_ms=timestamp_ms,
        deadline_at_ms=deadline_at_ms,
        nonce=nonce,
        method=method,
        params=params,
        signature=signature,
    )


class RequestAuthenticator:
    def __init__(
        self,
        secret: str,
        clock: Callable[[], float] = time,
        maximum_age_seconds: int = 30,
    ) -> None:
        self._secret = secret
        self._clock = clock
        self._maximum_age_ms = maximum_age_seconds * 1_000
        self._seen_nonces: dict[str, int] = {}

    def verify(self, request: RpcRequest) -> tuple[bool, str]:
        current_time_ms = int(self._clock() * 1_000)
        self._seen_nonces = {
            nonce: timestamp
            for nonce, timestamp in self._seen_nonces.items()
            if current_time_ms - timestamp <= self._maximum_age_ms
        }
        age_ms = abs(current_time_ms - request.timestamp_ms)
        if age_ms > self._maximum_age_ms:
            return False, "request timestamp is outside the allowed window"
        if current_time_ms >= request.deadline_at_ms:
            return False, "request deadline has expired"
        if request.nonce in self._seen_nonces:
            return False, "request nonce has already been used"
        expected = _signature(
            self._secret,
            request.protocol_version,
            request.request_id,
            request.caller_id,
            request.session_id,
            request.timestamp_ms,
            request.deadline_at_ms,
            request.nonce,
            request.method,
            request.params,
        )
        if not hmac.compare_digest(request.signature, expected):
            return False, "invalid request signature"
        self._seen_nonces[request.nonce] = request.timestamp_ms
        return True, "authenticated"


def _signature(
    secret: str,
    protocol_version: str,
    request_id: str,
    caller_id: str,
    session_id: str | None,
    timestamp_ms: int,
    deadline_at_ms: int,
    nonce: str,
    method: str,
    params: dict[str, Any],
) -> str:
    canonical_params = json.dumps(
        params,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    message = "\n".join(
        (
            protocol_version,
            request_id,
            caller_id,
            session_id or "",
            str(timestamp_ms),
            str(deadline_at_ms),
            nonce,
            method,
            canonical_params,
        )
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
