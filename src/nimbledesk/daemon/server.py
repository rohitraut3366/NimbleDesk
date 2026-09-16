from __future__ import annotations

import asyncio
import os
import platform
import secrets
import subprocess
import sys
from contextlib import suppress
from importlib import import_module
from pathlib import Path

from nimbledesk.backends import (
    AdapterDesktopBackend,
    MacOSNativeBackend,
    NativeDesktopBackend,
    PortableDesktopBackend,
    SimulatorBackend,
    WaylandPortalBackend,
    WindowsNativeBackend,
    X11DesktopBackend,
    registry_for_host,
    system_semantic_provider,
)
from nimbledesk.daemon.approvals import ApprovalManager
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.daemon.policy import ActionPolicy
from nimbledesk.daemon.runtime import DesktopRuntime
from nimbledesk.daemon.sessions import SessionManager
from nimbledesk.daemon.transport import DaemonTransport, write_connection_file
from nimbledesk.jobs.service import JobService
from nimbledesk.perception.ocr import TesseractOcrProvider
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
        selected_system = platform.system()
        portable_backend: DesktopBackend
        if selected_system == "Darwin":
            portable_backend = MacOSNativeBackend()
        elif selected_system == "Windows":
            portable_backend = WindowsNativeBackend()
        elif (
            selected_system == "Linux"
            and os.getenv("XDG_SESSION_TYPE", "").casefold() == "wayland"
        ):
            portable_backend = WaylandPortalBackend()
        else:
            portable_backend = PortableDesktopBackend(import_module("pyautogui"))
            if selected_system == "Linux":
                portable_backend = X11DesktopBackend(portable_backend)
        backend = NativeDesktopBackend(
            portable_backend,
            system_semantic_provider(),
        )
    elif backend_name == "simulator":
        backend = SimulatorBackend()
    else:
        raise ValueError(f"unknown NIMBLEDESK_BACKEND: {backend_name}")
    adapter_directory = Path(
        os.getenv("NIMBLEDESK_ADAPTER_DIR", str(Path.home() / ".nimbledesk" / "adapters"))
    )
    backend = AdapterDesktopBackend(backend, registry_for_host(adapter_directory))
    runtime = DesktopRuntime(
        backend=backend,
        sessions=SessionManager(),
        policy=ActionPolicy(),
        approvals=ApprovalManager(),
        audit=AuditLog(runtime_dir / "audit.jsonl"),
        ocr_provider=TesseractOcrProvider(),
        jobs=JobService(runtime_dir.parent / "jobs"),
    )
    runtime.recover_startup()
    return runtime


async def run() -> None:
    runtime_dir = runtime_directory()
    secret = secrets.token_urlsafe(32)
    runtime = build_runtime(runtime_dir)
    watchdog = _start_watchdog()
    transport = DaemonTransport(runtime, secret)
    server = await transport.start()
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    write_connection_file(
        runtime_dir / "connection.json",
        ConnectionInfo(port=port, secret=secret),
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        runtime.shutdown()
        if watchdog.stdin is not None:
            watchdog.stdin.close()
        try:
            watchdog.wait(timeout=2)
        except subprocess.TimeoutExpired:
            watchdog.terminate()
        (runtime_dir / "connection.json").unlink(missing_ok=True)


def _start_watchdog() -> subprocess.Popen[bytes]:
    command = (
        [sys.executable, "watchdog"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "nimbledesk.daemon.watchdog"]
    )
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run())
