from __future__ import annotations

import argparse
import atexit
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from nimbledesk.adapters.worker import main as adapter_worker_main
from nimbledesk.console.safety import main as console_main
from nimbledesk.creative.cli import main as create_main
from nimbledesk.creative.davinci_worker import main as davinci_worker_main
from nimbledesk.creative.music_cli import main as music_main
from nimbledesk.creative.revision_cli import main as revise_main
from nimbledesk.creative.vision_http import main as vision_http_main
from nimbledesk.daemon.approval_cli import main as approve_main
from nimbledesk.daemon.server import main as daemon_main
from nimbledesk.daemon.watchdog import main as watchdog_main
from nimbledesk.diagnostics import main as diagnostics_main
from nimbledesk.gateway.server import main as gateway_main
from nimbledesk.media.cli import main as highlights_main
from nimbledesk.media.photo_cli import main as photos_main
from nimbledesk.service import main as service_main
from nimbledesk.testing.adapter_isolation_contract import (
    main as adapter_isolation_contract_main,
)
from nimbledesk.testing.davinci_contract import main as davinci_contract_main
from nimbledesk.testing.fixture_app import main as fixture_main
from nimbledesk.testing.fixture_contract import main as fixture_contract_main
from nimbledesk.testing.qualification import main as qualification_main
from nimbledesk.testing.smoke import main as smoke_main
from nimbledesk.testing.windows_adapter_contract import main as windows_adapter_contract_main
from nimbledesk.ui.server import main as studio_main
from nimbledesk.update import UpdateManager
from nimbledesk.update import main as update_main

COMMANDS: dict[str, Callable[[], None]] = {
    "daemon": daemon_main,
    "watchdog": watchdog_main,
    "console": console_main,
    "mcp": gateway_main,
    "studio": studio_main,
    "create": create_main,
    "revise": revise_main,
    "highlights": highlights_main,
    "photos": photos_main,
    "music-index": music_main,
    "approve": approve_main,
    "smoke": smoke_main,
    "service": service_main,
    "diagnostics": diagnostics_main,
    "qualify": qualification_main,
    "fixture": fixture_main,
    "fixture-contract": fixture_contract_main,
    "davinci-contract": davinci_contract_main,
    "adapter-isolation-contract": adapter_isolation_contract_main,
    "windows-adapter-contract": windows_adapter_contract_main,
    "vision-http": vision_http_main,
    "update": update_main,
    "adapter-worker": adapter_worker_main,
    "davinci-worker": davinci_worker_main,
}


def main(arguments: list[str] | None = None) -> None:
    _handoff_to_managed_version(arguments)
    parser = argparse.ArgumentParser(
        prog="nimbledesk",
        description="Run NimbleDesk desktop automation and creative workflows",
    )
    parser.add_argument(
        "command",
        choices=("start", *COMMANDS),
        help="start Studio and its daemon, or run one component",
    )
    parsed, remaining = parser.parse_known_args(arguments)
    if parsed.command == "start":
        _start(remaining)
        return
    sys.argv = [f"nimbledesk {parsed.command}", *remaining]
    COMMANDS[parsed.command]()


def _handoff_to_managed_version(arguments: list[str] | None) -> None:
    if not getattr(sys, "frozen", False):
        return
    selected_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if selected_arguments[:1] == ["update"]:
        return
    manager = UpdateManager()
    if manager.state() is None:
        return
    executable = manager.executable()
    if executable.resolve() == Path(sys.executable).resolve():
        return
    os.execv(str(executable), [str(executable), *selected_arguments])


def _start(studio_arguments: list[str]) -> None:
    daemon_command = (
        [sys.executable, "daemon"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "nimbledesk.daemon.server"]
    )
    daemon = subprocess.Popen(daemon_command)
    atexit.register(_stop_process, daemon)
    configured_runtime = os.getenv("NIMBLEDESK_RUNTIME_DIR")
    runtime_directory = (
        Path(configured_runtime)
        if configured_runtime
        else Path.home() / ".nimbledesk" / "runtime"
    )
    connection_file = runtime_directory / "connection.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            raise RuntimeError(f"NimbleDesk daemon exited with status {daemon.returncode}")
        if connection_file.is_file() and connection_file.stat().st_mtime >= time.time() - 15:
            break
        time.sleep(0.1)
    else:
        _stop_process(daemon)
        raise RuntimeError("NimbleDesk daemon did not become ready within ten seconds")
    sys.argv = ["nimbledesk studio", *studio_arguments]
    try:
        studio_main()
    finally:
        _stop_process(daemon)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


if __name__ == "__main__":
    main()
