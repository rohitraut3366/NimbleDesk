from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from nimbledesk.client import DaemonClient


def build_diagnostic_bundle(output: Path, data_directory: Path | None = None) -> Path:
    root = data_directory or Path.home() / ".nimbledesk"
    runtime = root / "runtime"
    report = {
        "diagnostic_version": "1.0.0",
        "created_at": time.time(),
        "nimbledesk_version": _package_version(),
        "platform": platform.system().lower(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "frozen_bundle": bool(getattr(sys, "frozen", False)),
        "session_type": os.getenv("XDG_SESSION_TYPE"),
        "desktop": os.getenv("XDG_CURRENT_DESKTOP"),
        "tools": {
            tool: _tool_status(tool)
            for tool in ("ffmpeg", "ffprobe", "whisper", "tesseract")
        },
        "runtime": _runtime_status(runtime),
        "daemon": _daemon_health(runtime / "connection.json"),
        "audit": _audit_summary(runtime / "audit.jsonl"),
        "jobs": _job_summary(root / "jobs"),
    }
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(report, indent=2, sort_keys=True))
        archive.writestr(
            "README.txt",
            "This bundle contains sanitized environment and status summaries only. "
            "It excludes screenshots, media, transcripts, typed text, file paths, and secrets.\n",
        )
    return destination


def _package_version() -> str:
    try:
        return version("nimbledesk")
    except PackageNotFoundError:
        return "development"


def _tool_status(tool: str) -> dict[str, object]:
    executable = shutil.which(tool)
    if executable is None:
        return {"available": False}
    try:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, check=False, text=True, timeout=5
        )
        first_line = (completed.stdout or completed.stderr).splitlines()[0][:200]
    except (OSError, subprocess.SubprocessError, IndexError):
        first_line = "version unavailable"
    return {"available": True, "version": first_line}


def _runtime_status(runtime: Path) -> dict[str, object]:
    connection = runtime / "connection.json"
    mode = stat.S_IMODE(connection.stat().st_mode) if connection.is_file() else None
    private = (
        _windows_file_is_private(connection)
        if os.name == "nt"
        else mode is None or mode & 0o077 == 0
    )
    return {
        "directory_exists": runtime.is_dir(),
        "connection_exists": connection.is_file(),
        "connection_mode_octal": f"{mode:04o}" if mode is not None else None,
        "connection_private": private,
    }


def _windows_file_is_private(path: Path) -> bool:
    if not path.is_file():
        return True
    completed = subprocess.run(
        ["icacls", str(path)], capture_output=True, check=False, text=True, timeout=5
    )
    if completed.returncode != 0:
        return False
    access_list = completed.stdout.casefold()
    broad_principals = (
        "everyone",
        "authenticated users",
        "builtin\\users",
        "s-1-1-0",
        "s-1-5-11",
        "s-1-5-32-545",
    )
    return not any(principal in access_list for principal in broad_principals)


def _daemon_health(connection: Path) -> dict[str, object]:
    if not connection.is_file():
        return {"reachable": False, "reason": "connection file is absent"}
    try:
        result = asyncio.run(DaemonClient.from_file(connection).call("health"))
        return {"reachable": True, **result}
    except Exception as error:
        return {"reachable": False, "reason": type(error).__name__}


def _audit_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"entries": 0, "chain_valid": True, "statuses": {}}
    previous = "0" * 64
    statuses: dict[str, int] = {}
    entries = 0
    chain_valid = True
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record: dict[str, Any] = json.loads(line)
            supplied_hash = str(record.pop("entry_hash"))
            serialized = json.dumps(record, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(serialized.encode()).hexdigest()
            if record.get("previous_hash") != previous or supplied_hash != expected_hash:
                chain_valid = False
            previous = supplied_hash
            status = str(record.get("result", {}).get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            entries += 1
        except (json.JSONDecodeError, KeyError, TypeError):
            chain_valid = False
    return {"entries": entries, "chain_valid": chain_valid, "statuses": statuses}


def _job_summary(directory: Path) -> dict[str, object]:
    statuses: dict[str, int] = {}
    kinds: dict[str, int] = {}
    invalid = 0
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                status = str(payload.get("status", "unknown"))
                kind = str(payload.get("kind", "unknown"))
                statuses[status] = statuses.get(status, 0) + 1
                kinds[kind] = kinds.get(kind, 0) + 1
            except (json.JSONDecodeError, OSError):
                invalid += 1
    return {"statuses": statuses, "kinds": kinds, "invalid_records": invalid}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a privacy-safe NimbleDesk diagnostic bundle"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "nimbledesk-diagnostics.zip",
    )
    return parser.parse_args()


def main() -> None:
    path = build_diagnostic_bundle(parse_args().output)
    print(json.dumps({"diagnostic_bundle": str(path)}, indent=2))
