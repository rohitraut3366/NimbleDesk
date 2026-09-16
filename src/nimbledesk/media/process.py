from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from importlib import import_module
from typing import Any

psutil: Any = import_module("psutil")

CancellationCheck = Callable[[], bool]


class ProcessCancelled(RuntimeError):
    pass


class ProcessMemoryLimitExceeded(RuntimeError):
    pass


def run_cancellable(
    command: Sequence[str],
    *,
    cancelled: CancellationCheck | None = None,
    text: bool = True,
    timeout_seconds: float | None = None,
    maximum_memory_bytes: int | None = None,
) -> subprocess.CompletedProcess[Any]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        start_new_session=os.name == "posix",
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        ),
    )
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                stop_process(process)
                raise ProcessCancelled("creation was cancelled") from None
            if deadline is not None and time.monotonic() >= deadline:
                stop_process(process)
                raise TimeoutError("process timed out") from None
            if (
                maximum_memory_bytes is not None
                and _process_tree_memory_bytes(process) > maximum_memory_bytes
            ):
                stop_process(process)
                raise ProcessMemoryLimitExceeded(
                    f"process exceeded the {maximum_memory_bytes}-byte memory limit"
                ) from None


def check_cancelled(cancelled: CancellationCheck | None) -> None:
    if cancelled and cancelled():
        raise ProcessCancelled("creation was cancelled")


def check_process_cancelled(
    process: subprocess.Popen[Any], cancelled: CancellationCheck | None
) -> None:
    if cancelled and cancelled():
        stop_process(process)
        raise ProcessCancelled("creation was cancelled")


def stop_process(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
    else:
        _terminate_windows_tree(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            _kill_windows_tree(process)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)


def _process_tree_memory_bytes(process: subprocess.Popen[Any]) -> int:
    try:
        root = psutil.Process(process.pid)
        return sum(
            candidate.memory_info().rss for candidate in (root, *root.children(recursive=True))
        )
    except (psutil.Error, ProcessLookupError):
        return 0


def _terminate_windows_tree(process: subprocess.Popen[Any]) -> None:
    try:
        root = psutil.Process(process.pid)
        for child in root.children(recursive=True):
            with suppress(psutil.Error, ProcessLookupError):
                child.terminate()
    except (psutil.Error, ProcessLookupError):
        pass
    with suppress(ProcessLookupError):
        process.terminate()


def _kill_windows_tree(process: subprocess.Popen[Any]) -> None:
    try:
        root = psutil.Process(process.pid)
        for child in root.children(recursive=True):
            with suppress(psutil.Error, ProcessLookupError):
                child.kill()
    except (psutil.Error, ProcessLookupError):
        pass
    with suppress(ProcessLookupError):
        process.kill()
