from __future__ import annotations

import argparse
import hashlib
import json
import platform
import socket
import tempfile
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal, cast

from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import AdapterError, IsolatedAdapterRunner
from nimbledesk.testing.release_evidence import (
    AdapterIsolationReport,
    PhysicalEvidenceCase,
)


def run_contract(target_id: str) -> AdapterIsolationReport:
    cases: list[PhysicalEvidenceCase] = []
    with tempfile.TemporaryDirectory(prefix="nimbledesk-isolation-contract-") as temporary:
        root = Path(temporary).resolve()
        package = root / "package"
        package.mkdir()
        fixture = package / "malicious_adapter.py"
        fixture.write_text(_MALICIOUS_ADAPTER, encoding="utf-8")
        allowed = root / "allowed"
        allowed.mkdir()
        allowed_file = allowed / "input.txt"
        allowed_file.write_text("declared", encoding="utf-8")
        writable = root / "writable"
        writable.mkdir()
        denied_read = root / "private.txt"
        denied_read.write_text("private", encoding="utf-8")
        denied_write = root / "private-output.txt"

        runner = IsolatedAdapterRunner()
        probe = runner.execute(
            _manifest(package, network_access=False),
            "probe",
            {
                "allowed": str(allowed_file),
                "writable": str(writable / "output.txt"),
                "denied_read": str(denied_read),
                "denied_write": str(denied_write),
            },
            granted_paths=(allowed, writable),
        )
        probe_result = probe.result
        cases.extend(
            (
                _case(
                    "undeclared_read_denied",
                    not bool(probe_result.get("denied_read", {}).get("succeeded")),
                    probe_result.get("denied_read"),
                ),
                _case(
                    "undeclared_write_denied",
                    not bool(probe_result.get("denied_write", {}).get("succeeded")),
                    probe_result.get("denied_write"),
                ),
                _case(
                    "declared_read_allowed",
                    probe_result.get("allowed_content") == "declared",
                ),
                _case(
                    "declared_write_allowed",
                    bool(probe_result.get("declared_write", {}).get("succeeded")),
                    probe_result.get("declared_write"),
                ),
                _case("child_process_denied", probe_result.get("child_process") is False),
            )
        )

        if platform.system() == "Windows":
            denied_network = runner.execute(
                _manifest(package, network_access=False), "network_capability", {}
            ).result
            allowed_network = runner.execute(
                _manifest(package, network_access=True), "network_capability", {}
            ).result
            cases.extend(
                (
                    _case(
                        "network_denied_by_default",
                        denied_network.get("internet_client") is False,
                    ),
                    _case(
                        "network_allowed_when_declared",
                        allowed_network.get("internet_client") is True,
                    ),
                )
            )
        else:
            with _local_listener() as endpoint:
                arguments = {"host": endpoint[0], "port": endpoint[1]}
                denied_network = runner.execute(
                    _manifest(package, network_access=False), "network", arguments
                ).result
                allowed_network = runner.execute(
                    _manifest(package, network_access=True), "network", arguments
                ).result
            cases.extend(
                (
                    _case(
                        "network_denied_by_default",
                        denied_network.get("connected") is False,
                    ),
                    _case(
                        "network_allowed_when_declared",
                        allowed_network.get("connected") is True,
                    ),
                )
            )

        memory_limited = False
        try:
            memory_result = runner.execute(
                _manifest(package, network_access=False), "exhaust_memory", {}
            )
            memory_limited = not memory_result.success
        except AdapterError as error:
            memory_limited = "memory" in str(error).casefold() or "exited" in str(error).casefold()
        cases.append(_case("memory_limit_enforced", memory_limited))

        timeout_enforced = False
        try:
            runner.execute(_manifest(package, network_access=False), "sleep", {})
        except AdapterError as error:
            timeout_enforced = "timed out" in str(error)
        cases.append(_case("timeout_cancellation", timeout_enforced))

        host_survived = runner.execute(
            _manifest(package, network_access=False), "benign", {}
        )
        cases.append(
            _case(
                "worker_host_survived",
                host_survived.success and host_survived.result.get("status") == "ready",
            )
        )
        fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    return AdapterIsolationReport(
        target_id=target_id,
        platform=platform.system(),
        platform_release=platform.platform(),
        adapter_id="nimbledesk.malicious-fixture",
        malicious_fixture_sha256=fixture_hash,
        cases=tuple(cases),
        passed=bool(cases) and all(case.passed for case in cases),
    )


def _manifest(package: Path, *, network_access: bool) -> AdapterManifest:
    current_platform = cast(Literal["Darwin", "Windows", "Linux"], platform.system())
    return AdapterManifest(
        adapter_id="nimbledesk.malicious-fixture",
        version="1.0.0",
        vendor="NimbleDesk",
        entrypoint="malicious_adapter:handle",
        package_path=package,
        supported_platforms=frozenset({current_platform}),
        isolation="sandboxed",
        network_access=network_access,
        writable_path_arguments=("writable",),
        commands={
            "probe": AdapterCommand(
                risk="observe",
                required_arguments=(
                    "allowed",
                    "writable",
                    "denied_read",
                    "denied_write",
                ),
                path_arguments=("allowed", "writable"),
            ),
            "network": AdapterCommand(risk="observe", read_only=True),
            "network_capability": AdapterCommand(risk="observe", read_only=True),
            "exhaust_memory": AdapterCommand(
                risk="observe", read_only=True, timeout_seconds=15
            ),
            "sleep": AdapterCommand(risk="observe", read_only=True, timeout_seconds=0.1),
            "benign": AdapterCommand(risk="observe", read_only=True),
        },
    )


@contextmanager
def _local_listener() -> Iterator[tuple[str, int]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)

    def accept_connection() -> None:
        try:
            connection, _address = listener.accept()
            with closing(connection):
                connection.recv(1)
        except OSError:
            pass

    thread = threading.Thread(target=accept_connection, daemon=True)
    thread.start()
    host, port = listener.getsockname()
    try:
        yield str(host), int(port)
    finally:
        listener.close()
        thread.join(timeout=2)


def _case(name: str, passed: bool, evidence: object = None) -> PhysicalEvidenceCase:
    return PhysicalEvidenceCase(
        name=name,
        passed=passed,
        evidence={"probe": evidence} if evidence is not None else {},
        error=None if passed else f"{name} did not produce the required isolation result",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the physical malicious-adapter isolation contract"
    )
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    report = run_contract(arguments.target_id)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(arguments.output), "passed": report.passed}))
    if not report.passed:
        raise SystemExit(1)


_MALICIOUS_ADAPTER = r'''
import importlib
import os
import socket
import subprocess
import sys
import time


def handle(command, arguments):
    if command == "probe":
        allowed_content = open(arguments["allowed"], encoding="utf-8").read()
        declared_write = _write(arguments["writable"])
        denied_read = _read(arguments["denied_read"])
        denied_write = _write(arguments["denied_write"])
        child_process = _spawn_child()
        return {
            "allowed_content": allowed_content,
            "declared_write": declared_write,
            "denied_read": denied_read,
            "denied_write": denied_write,
            "child_process": child_process,
        }
    if command == "network":
        connected = False
        try:
            with socket.create_connection(
                (arguments["host"], int(arguments["port"])), timeout=2
            ) as connection:
                connection.sendall(b"x")
            connected = True
        except OSError:
            pass
        return {"connected": connected}
    if command == "network_capability":
        return {"internet_client": _internet_client_capability()}
    if command == "exhaust_memory":
        allocation = bytearray(600 * 1024 * 1024)
        for offset in range(0, len(allocation), 4096):
            allocation[offset] = 1
        return {"allocated": len(allocation)}
    if command == "sleep":
        time.sleep(5)
        return {"slept": True}
    if command == "benign":
        return {"status": "ready"}
    raise ValueError(command)


def _read(path):
    try:
        open(path, encoding="utf-8").read()
        return {"succeeded": True, "error": None}
    except OSError as error:
        return {"succeeded": False, "error": str(error)}


def _write(path):
    try:
        open(path, "w", encoding="utf-8").write("written")
        return {"succeeded": True, "error": None}
    except OSError as error:
        return {"succeeded": False, "error": str(error)}


def _spawn_child():
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    child.wait(timeout=5)
    return True


def _internet_client_capability():
    if os.name != "nt":
        return False
    win32api = importlib.import_module("win32api")
    win32con = importlib.import_module("win32con")
    win32security = importlib.import_module("win32security")
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        capabilities = win32security.GetTokenInformation(
            token, win32security.TokenCapabilities
        )
    finally:
        win32api.CloseHandle(token)
    return "S-1-15-3-1" in {str(item[0]) for item in capabilities}
'''.lstrip()


if __name__ == "__main__":
    main()
