from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, cast

from nimbledesk.adapters.limits import MAXIMUM_WORKER_MEMORY_BYTES
from nimbledesk.adapters.models import AdapterCommand, AdapterManifest
from nimbledesk.adapters.runner import (
    AdapterError,
    AdapterProcess,
    _close_process,
    _kill_process_group,
    _process_tree_memory_bytes,
    _sandboxed_worker_command,
    _terminate_process,
    _windows_appcontainer_paths,
)
from nimbledesk.media.process import CancellationCheck, ProcessCancelled

MAXIMUM_STDERR_BYTES = 65_536


def run_isolated_command(
    command: list[str],
    *,
    readable_paths: tuple[Path, ...],
    writable_paths: tuple[Path, ...],
    network_access: bool,
    timeout_seconds: float,
    cancelled: CancellationCheck | None = None,
    environment_variables: tuple[str, ...] = (),
    code_paths: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a configured provider with only its declared files and optional network access."""
    if not command:
        raise AdapterError("isolated command cannot be empty")
    executable = Path(command[0]).expanduser()
    if executable.exists():
        command = [str(executable.resolve()), *command[1:]]
    all_readable_paths = tuple(
        dict.fromkeys(
            path.expanduser().resolve()
            for path in (*readable_paths, *writable_paths, executable, *code_paths)
        )
    )
    writable_paths = tuple(path.expanduser().resolve() for path in writable_paths)
    command = [
        _canonical_command_argument(argument, all_readable_paths) for argument in command
    ]
    path_arguments = tuple(f"writable_{index}" for index in range(len(writable_paths)))
    arguments: dict[str, object] = {
        name: str(path.resolve()) for name, path in zip(path_arguments, writable_paths, strict=True)
    }
    current_platform = platform.system()
    if current_platform not in {"Darwin", "Windows", "Linux"}:
        raise AdapterError(f"isolated providers are unavailable on {current_platform}")
    supported_platform = cast(Literal["Darwin", "Windows", "Linux"], current_platform)
    manifest = AdapterManifest(
        adapter_id="nimbledesk-analysis-provider",
        version="1.0.0",
        vendor="NimbleDesk",
        entrypoint="nimbledesk.adapters.fixture:handle",
        supported_platforms=frozenset({supported_platform}),
        commands={
            "execute": AdapterCommand(
                risk="medium",
                timeout_seconds=timeout_seconds,
                path_arguments=path_arguments,
            )
        },
        isolation="sandboxed",
        network_access=network_access,
        writable_path_arguments=path_arguments,
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in environment_variables:
        if name in os.environ:
            environment[name] = os.environ[name]
    with (
        tempfile.TemporaryDirectory(prefix="nimbledesk-provider-") as scratch_name,
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        scratch = Path(scratch_name).resolve()
        environment.update(
            {
                "HOME": str(scratch),
                "TMPDIR": str(scratch),
                "TEMP": str(scratch),
                "TMP": str(scratch),
            }
        )
        isolated_command = _sandboxed_worker_command(
            command, manifest, arguments, all_readable_paths, scratch
        )
        process: AdapterProcess
        if platform.system() == "Windows":
            if getattr(sys, "frozen", False):
                from nimbledesk.adapters.windows_appcontainer import (
                    start_windows_appcontainer_process,
                )

                windows_readable, windows_writable = _windows_appcontainer_paths(
                    command, manifest, arguments, all_readable_paths, scratch
                )
                process = start_windows_appcontainer_process(
                    command,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    environment=environment,
                    cwd=scratch,
                    readable_paths=windows_readable,
                    writable_paths=windows_writable,
                    network_access=network_access,
                )
            else:
                from nimbledesk.adapters.windows_process import start_windows_restricted_process

                process = start_windows_restricted_process(
                    command,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    environment=environment,
                    cwd=scratch,
                )
        else:
            process = subprocess.Popen(
                isolated_command,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=scratch,
                start_new_session=True,
            )
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if cancelled and cancelled():
                _stop_process(process)
                raise ProcessCancelled("creation was cancelled")
            if time.monotonic() >= deadline:
                _stop_process(process)
                raise TimeoutError("isolated provider timed out")
            if _process_tree_memory_bytes(process) > MAXIMUM_WORKER_MEMORY_BYTES:
                _stop_process(process)
                raise AdapterError("provider exceeded the 512 MiB memory limit")
            if os.fstat(stderr_file.fileno()).st_size > MAXIMUM_STDERR_BYTES:
                _stop_process(process)
                raise AdapterError("provider stderr exceeded the 64-kilobyte limit")
            time.sleep(0.02)
        _kill_process_group(process)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read(MAXIMUM_STDERR_BYTES + 1).decode(
            "utf-8", errors="replace"
        )
        return_code = process.returncode or 0
        _close_process(process)
        if len(stderr.encode("utf-8")) > MAXIMUM_STDERR_BYTES:
            raise AdapterError("provider stderr exceeded the 64-kilobyte limit")
        return subprocess.CompletedProcess(command, return_code, stdout, stderr)


def _stop_process(process: AdapterProcess) -> None:
    _terminate_process(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    finally:
        _close_process(process)


def _canonical_command_argument(argument: str, granted_paths: tuple[Path, ...]) -> str:
    candidate = Path(argument).expanduser()
    if not candidate.is_absolute():
        return argument
    resolved = candidate.resolve(strict=False)
    if any(
        resolved == grant or (grant.is_dir() and grant in resolved.parents)
        for grant in granted_paths
    ):
        return str(resolved)
    return argument
