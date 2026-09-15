from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from time import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from nimbledesk.protocol.models import ProtocolModel


class RpcRequest(ProtocolModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp_ms: int
    nonce: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    signature: str


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


def create_request(method: str, params: dict[str, Any], secret: str) -> RpcRequest:
    request_id = str(uuid4())
    timestamp_ms = int(time() * 1_000)
    nonce = secrets.token_urlsafe(24)
    signature = _signature(secret, request_id, timestamp_ms, nonce, method, params)
    return RpcRequest(
        request_id=request_id,
        timestamp_ms=timestamp_ms,
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
        if request.nonce in self._seen_nonces:
            return False, "request nonce has already been used"
        expected = _signature(
            self._secret,
            request.request_id,
            request.timestamp_ms,
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
    request_id: str,
    timestamp_ms: int,
    nonce: str,
    method: str,
    params: dict[str, Any],
) -> str:
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    message = "\n".join((request_id, str(timestamp_ms), nonce, method, canonical_params))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
