from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nimbledesk.protocol.rpc import ConnectionInfo, RpcResponse, create_request


class DaemonClientError(RuntimeError):
    pass


class DaemonClient:
    def __init__(
        self,
        connection: ConnectionInfo,
        timeout_seconds: float = 10,
        caller_id: str = "local-client",
    ) -> None:
        self._connection = connection
        self._timeout_seconds = timeout_seconds
        self._caller_id = caller_id

    @classmethod
    def from_file(
        cls, path: Path, *, caller_id: str = "local-client"
    ) -> DaemonClient:
        connection = ConnectionInfo.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(connection, caller_id=caller_id)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request = create_request(
            method,
            params or {},
            self._connection.secret,
            caller_id=self._caller_id,
            deadline_ms=max(1, min(120_000, int(self._timeout_seconds * 1_000))),
        )
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._connection.host, self._connection.port),
            timeout=self._timeout_seconds,
        )
        try:
            writer.write(request.model_dump_json().encode() + b"\n")
            await writer.drain()
            response_data = await asyncio.wait_for(
                reader.readline(),
                timeout=self._timeout_seconds,
            )
        finally:
            writer.close()
            await writer.wait_closed()
        if not response_data:
            raise DaemonClientError("daemon closed the connection without a response")
        response = RpcResponse.model_validate(json.loads(response_data))
        if not response.ok:
            raise DaemonClientError(f"{response.error_code}: {response.error_message}")
        return response.result or {}
