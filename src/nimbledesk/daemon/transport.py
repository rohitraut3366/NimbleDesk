from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.protocol.models import (
    ActionRequest,
    CaptureOptions,
    Rectangle,
    SessionConfig,
    SessionState,
    Target,
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
            request = RpcRequest.model_validate(_strict_json_object(data))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
            ValueError,
            RecursionError,
        ) as error:
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
            return self._runtime.health()
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
        if method == "session_list":
            return {
                "sessions": [
                    session.model_dump(mode="json")
                    for session in self._runtime.list_sessions()
                ]
            }
        if method == "session_status":
            return self._runtime.session_status(str(params["session_id"])).model_dump(
                mode="json"
            )
        if method == "emergency_stop":
            sessions = self._runtime.emergency_stop()
            return {"status": "stopped", "stopped_sessions": len(sessions)}
        if method == "adapters_list":
            return {"adapters": list(self._runtime.adapter_descriptions())}
        if method == "audit_query":
            return {
                "entries": list(
                    self._runtime.audit_summaries(
                        str(params["session_id"]), int(params.get("limit", 20))
                    )
                ),
                "integrity": self._runtime.audit_integrity(),
            }
        if method == "paths_authorize":
            raw_paths = params.get("paths", [])
            if not isinstance(raw_paths, list) or not raw_paths:
                raise ValueError("paths_authorize requires at least one path")
            self._runtime.authorize_paths(
                str(params["session_id"]), tuple(Path(str(path)) for path in raw_paths)
            )
            return {"authorized": True, "path_count": len(raw_paths)}
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
        if method == "target_resolve":
            target: Target = TypeAdapter(Target).validate_python(params["target"])
            return self._runtime.resolve_target(
                str(params["session_id"]), str(params["observation_id"]), target
            )
        if method == "media_index_open":
            return self._runtime.open_content_index(
                str(params["session_id"]), Path(str(params["index_path"]))
            )
        if method == "media_index_search":
            return self._runtime.search_content_index(
                str(params["session_id"]),
                str(params["index_id"]),
                str(params["query"]),
                int(params.get("maximum_results", 20)),
                int(params.get("maximum_tokens", 2_000)),
            )
        if method == "media_index_detail":
            return self._runtime.content_index_detail(
                str(params["session_id"]),
                str(params["index_id"]),
                str(params["result_id"]),
                int(params.get("maximum_tokens", 2_000)),
            )
        if method == "action_execute":
            result = self._runtime.execute(ActionRequest.model_validate(params["action"]))
            return result.model_dump(mode="json")
        if method == "approval_approve":
            return {"approval_token": self._runtime.approve(str(params["approval_id"]))}
        if method == "approval_reject":
            self._runtime.reject_approval(str(params["approval_id"]))
            return {"status": "rejected"}
        if method == "approval_list":
            return {
                "approvals": [
                    {
                        "approval_id": pending.approval_id,
                        "created_at": pending.created_at,
                        "expires_at": pending.expires_at,
                        "action": pending.action.model_dump(mode="json"),
                    }
                    for pending in self._runtime.list_approvals()
                ]
            }
        if method == "approval_status":
            decision = self._runtime.approval_status(str(params["approval_id"]))
            return {
                "approval_id": decision.approval_id,
                "status": decision.status,
                "expires_at": decision.expires_at,
                "approval_token": decision.token,
            }
        raise ValueError(f"unknown method: {method}")


def write_connection_file(path: Path, connection: ConnectionInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(connection.model_dump_json(indent=2), encoding="utf-8")
    if os.name == "nt":
        domain = os.getenv("USERDOMAIN")
        username = os.getenv("USERNAME")
        if not username:
            raise RuntimeError("Windows user identity is unavailable")
        principal = f"{domain}\\{username}" if domain else username
        completed = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(F)",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            path.unlink(missing_ok=True)
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(message or "could not restrict the connection-file ACL")
    else:
        path.chmod(0o600)


def _error(request_id: str, code: str, message: str) -> RpcResponse:
    return RpcResponse(
        request_id=request_id,
        ok=False,
        error_code=code,
        error_message=message,
    )


def _strict_json_object(data: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"request contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"request contains non-finite number: {value}")

    decoded = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    if not isinstance(decoded, dict):
        raise ValueError("request root must be a JSON object")
    return decoded
