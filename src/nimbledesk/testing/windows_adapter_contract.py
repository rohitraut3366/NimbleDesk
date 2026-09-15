from __future__ import annotations

import argparse
import platform
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import AdapterError, IsolatedAdapterRunner


class WindowsAdapterContractReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: str = "1.0.0"
    operating_system: str
    appcontainer_active: bool
    undeclared_read_denied: bool
    declared_read_allowed: bool
    declared_write_allowed: bool
    child_process_denied: bool
    memory_limit_enforced: bool
    network_denied_by_default: bool
    declared_network_capability_present: bool
    passed: bool


def run_contract() -> WindowsAdapterContractReport:
    if platform.system() != "Windows":
        raise RuntimeError("the Windows adapter contract must run on Windows")
    with tempfile.TemporaryDirectory(prefix="nimbledesk-windows-contract-") as temporary:
        root = Path(temporary)
        package = root / "package"
        package.mkdir()
        allowed = root / "allowed"
        allowed.mkdir()
        (allowed / "input.txt").write_text("allowed", encoding="utf-8")
        writable = root / "writable"
        writable.mkdir()
        denied = root / "denied.txt"
        denied.write_text("private", encoding="utf-8")
        (package / "malicious_adapter.py").write_text(_ADAPTER_SOURCE, encoding="utf-8")
        manifest = _manifest(package, network_access=False)
        result = IsolatedAdapterRunner().execute(
            manifest,
            "probe",
            {
                "allowed": str(allowed / "input.txt"),
                "writable": str(writable / "output.txt"),
                "denied": str(denied),
            },
            granted_paths=(allowed, writable),
        )
        probe = result.result
        network_result = IsolatedAdapterRunner().execute(
            _manifest(package, network_access=True),
            "capabilities",
            {},
        )
        denied_network_result = IsolatedAdapterRunner().execute(
            manifest,
            "capabilities",
            {},
        )
        memory_limit_enforced = False
        try:
            memory_result = IsolatedAdapterRunner().execute(manifest, "exhaust_memory", {})
            memory_limit_enforced = not memory_result.success
        except AdapterError:
            memory_limit_enforced = True
        appcontainer_active = bool(probe.get("appcontainer_sid"))
        undeclared_read_denied = probe.get("denied_read") is False
        declared_read_allowed = probe.get("allowed_content") == "allowed"
        declared_write_allowed = probe.get("write_succeeded") is True
        child_process_denied = probe.get("child_process_created") is False
        network_denied_by_default = (
            denied_network_result.result.get("internet_client") is False
        )
        declared_network_capability_present = (
            network_result.result.get("internet_client") is True
        )
        passed = all(
            (
                appcontainer_active,
                undeclared_read_denied,
                declared_read_allowed,
                declared_write_allowed,
                child_process_denied,
                memory_limit_enforced,
                network_denied_by_default,
                declared_network_capability_present,
            )
        )
        return WindowsAdapterContractReport(
            operating_system=platform.platform(),
            appcontainer_active=appcontainer_active,
            undeclared_read_denied=undeclared_read_denied,
            declared_read_allowed=declared_read_allowed,
            declared_write_allowed=declared_write_allowed,
            child_process_denied=child_process_denied,
            memory_limit_enforced=memory_limit_enforced,
            network_denied_by_default=network_denied_by_default,
            declared_network_capability_present=declared_network_capability_present,
            passed=passed,
        )


def _manifest(package: Path, *, network_access: bool) -> AdapterManifest:
    return AdapterManifest(
        adapter_id="nimbledesk.windows-contract",
        version="1.0.0",
        vendor="NimbleDesk",
        entrypoint="malicious_adapter:handle",
        package_path=package,
        supported_platforms=frozenset({"Windows"}),
        isolation="sandboxed",
        network_access=network_access,
        writable_path_arguments=("writable",),
        commands={
            "probe": AdapterCommand(
                risk="observe",
                read_only=False,
                required_arguments=("allowed", "writable", "denied"),
                path_arguments=("allowed", "writable"),
                timeout_seconds=10,
            ),
            "capabilities": AdapterCommand(risk="observe", read_only=True),
            "exhaust_memory": AdapterCommand(
                risk="observe", read_only=True, timeout_seconds=15
            ),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify Windows AppContainer isolation")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    report = run_contract()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(arguments.output)
    if not report.passed:
        raise SystemExit(1)


_ADAPTER_SOURCE = r'''
import importlib
import subprocess


def handle(command, arguments):
    if command == "exhaust_memory":
        allocation = bytearray(600 * 1024 * 1024)
        for offset in range(0, len(allocation), 4096):
            allocation[offset] = 1
        return {"allocated": len(allocation)}
    if command == "capabilities":
        return _security_state()
    allowed_content = open(arguments["allowed"], encoding="utf-8").read()
    denied_read = True
    try:
        open(arguments["denied"], encoding="utf-8").read()
    except OSError:
        denied_read = False
    write_succeeded = True
    try:
        open(arguments["writable"], "w", encoding="utf-8").write("written")
    except OSError:
        write_succeeded = False
    child_process_created = False
    try:
        child = subprocess.Popen(
            ["cmd.exe", "/d", "/c", "exit", "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
    else:
        child_process_created = True
        child.wait(timeout=5)
    return {
        **_security_state(),
        "allowed_content": allowed_content,
        "denied_read": denied_read,
        "write_succeeded": write_succeeded,
        "child_process_created": child_process_created,
    }


def _security_state():
    win32api = importlib.import_module("win32api")
    win32con = importlib.import_module("win32con")
    win32security = importlib.import_module("win32security")
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        appcontainer_sid = win32security.GetTokenInformation(
            token, win32security.TokenAppContainerSid
        )
        capabilities = win32security.GetTokenInformation(
            token, win32security.TokenCapabilities
        )
    finally:
        win32api.CloseHandle(token)
    capability_sids = {str(item[0]) for item in capabilities}
    return {
        "appcontainer_sid": str(appcontainer_sid) if appcontainer_sid else None,
        "internet_client": "S-1-15-3-1" in capability_sids,
    }
'''.lstrip()


if __name__ == "__main__":
    main()
