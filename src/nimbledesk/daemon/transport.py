from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.protocol.models import (
    ActionRequest,
    CaptureOptions,
    Rectangle,
    SessionConfig,
    SessionState,
)
from nimbledesk.protocol.rpc import ConnectionInfo, RequestAuthenticator, RpcRequest, RpcResponse

MAXIMUM_REQUEST_BYTES = 1_048_576


class DaemonTransport:
    def __init__(self, runtime: DesktopRuntime, secret: str) -> None:
        self._runtime = runtime
        self._authenticator = RequestAuthenticator(secret)

    async def start(self, port: int = 0) -> asyncio.Server:
        return await asyncio.start_server(
            self._handle_connection,
            host="127.0.0.1",
            port=port,
            limit=MAXIMUM_REQUEST_BYTES + 1,
        )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            data = await reader.readline()
            if len(data) > MAXIMUM_REQUEST_BYTES:
                response = _error("unknown", "request_too_large", "request exceeds size limit")
            else:
                response = self._process(data)
        except Exception as error:
            response = _error("unknown", "internal_error", str(error))
        writer.write(response.model_dump_json().encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _process(self, data: bytes) -> RpcResponse:
        try:
            request = RpcRequest.model_validate(json.loads(data))
        except (json.JSONDecodeError, ValidationError) as error:
            return _error("unknown", "invalid_request", str(error))
        authenticated, reason = self._authenticator.verify(request)
        if not authenticated:
            return _error(request.request_id, "authentication_failed", reason)
        try:
            result = self._dispatch(request.method, request.params)
        except (KeyError, ValueError, RuntimeError, ValidationError) as error:
            return _error(request.request_id, "request_failed", str(error))
        return RpcResponse(request_id=request.request_id, ok=True, result=result)

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "health":
            return {"status": "ok"}
        if method == "session_start":
            session = self._runtime.start_session(
                reason=str(params["reason"]),
                config=SessionConfig.model_validate(params.get("config", {})),
            )
            return session.model_dump(mode="json")
        if method == "session_set_state":
            session = self._runtime.set_session_state(
                str(params["session_id"]),
                SessionState(str(params["state"])),
            )
            return session.model_dump(mode="json")
        if method == "desktop_observe":
            observation = self._runtime.observe(str(params["session_id"]))
            return observation.model_dump(mode="json")
        if method == "screen_capture":
            region_data = params.get("region")
            region = Rectangle.model_validate(region_data) if region_data else None
            options = CaptureOptions.model_validate(params.get("options", {}))
            capture = self._runtime.capture(
                str(params["session_id"]),
                str(params["observation_id"]),
                region,
                options,
            )
            return capture.model_dump(mode="json")
        if method == "action_execute":
            result = self._runtime.execute(ActionRequest.model_validate(params["action"]))
            return result.model_dump(mode="json")
        if method == "approval_approve":
            return {"approval_token": self._runtime.approve(str(params["approval_id"]))}
        raise ValueError(f"unknown method: {method}")


def write_connection_file(path: Path, connection: ConnectionInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(connection.model_dump_json(indent=2), encoding="utf-8")
    path.chmod(0o600)


def _error(request_id: str, code: str, message: str) -> RpcResponse:
    return RpcResponse(
        request_id=request_id,
        ok=False,
        error_code=code,
        error_message=message,
    )
