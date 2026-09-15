from __future__ import annotations

import importlib
import subprocess
from time import sleep
from typing import Any


def handle(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if command == "sleep":
        sleep(float(arguments["seconds"]))
        return {"slept": arguments["seconds"]}
    if command == "huge":
        return {"content": "x" * 1_100_000}
    if command == "windows_security":
        return _windows_security_state()
    if command != "inspect":
        raise ValueError("unsupported fixture command")
    return {"received": arguments}


def _windows_security_state() -> dict[str, Any]:
    win32api = importlib.import_module("win32api")
    win32con = importlib.import_module("win32con")
    win32security = importlib.import_module("win32security")

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        privileges = win32security.GetTokenInformation(
            token, win32security.TokenPrivileges
        )
        enabled_privileges = [
            win32security.LookupPrivilegeName(None, luid)
            for luid, attributes in privileges
            if attributes & win32security.SE_PRIVILEGE_ENABLED
        ]
    finally:
        win32api.CloseHandle(token)
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
        "enabled_privileges": sorted(enabled_privileges),
        "child_process_created": child_process_created,
    }
