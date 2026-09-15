from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import suppress
from importlib import import_module
from pathlib import Path

from nimbledesk.backends import (
    NativeDesktopBackend,
    PortableDesktopBackend,
    SimulatorBackend,
    system_semantic_provider,
)
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.daemon.transport import DaemonTransport, write_connection_file
from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.rpc import ConnectionInfo


def runtime_directory() -> Path:
    configured = os.getenv("NIMBLEDESK_RUNTIME_DIR")
    return Path(configured) if configured else Path.home() / ".nimbledesk" / "runtime"


def build_runtime(runtime_dir: Path) -> DesktopRuntime:
    backend_name = os.getenv("NIMBLEDESK_BACKEND", "simulator")
    backend: DesktopBackend
    if backend_name == "portable":
        backend = PortableDesktopBackend(import_module("pyautogui"))
    elif backend_name == "native":
        backend = NativeDesktopBackend(
            PortableDesktopBackend(import_module("pyautogui")),
            system_semantic_provider(),
        )
    elif backend_name == "simulator":
        backend = SimulatorBackend()
    else:
        raise ValueError(f"unknown NIMBLEDESK_BACKEND: {backend_name}")
    return DesktopRuntime(
        backend=backend,
        sessions=SessionManager(),
        policy=ActionPolicy(),
        approvals=ApprovalManager(),
        audit=AuditLog(runtime_dir / "audit.jsonl"),
    )


async def run() -> None:
    runtime_dir = runtime_directory()
    secret = secrets.token_urlsafe(32)
    transport = DaemonTransport(build_runtime(runtime_dir), secret)
    server = await transport.start()
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    write_connection_file(
        runtime_dir / "connection.json",
        ConnectionInfo(port=port, secret=secret),
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run())
