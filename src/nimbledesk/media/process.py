from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from typing import Any

CancellationCheck = Callable[[], bool]


class ProcessCancelled(RuntimeError):
    pass


def run_cancellable(
    command: Sequence[str],
    *,
    cancelled: CancellationCheck | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                stop_process(process)
                raise ProcessCancelled("creation was cancelled") from None


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
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
