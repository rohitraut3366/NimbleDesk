from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.workflow import CreationWorkflow
from nimbledesk.media.process import ProcessCancelled, run_cancellable


def test_cancellable_process_is_terminated_promptly() -> None:
    cancelled = threading.Event()
    timer = threading.Timer(0.2, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ProcessCancelled):
            run_cancellable(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancelled=cancelled.is_set,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 3


def test_cancellation_terminates_descendant_processes(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    cancelled = threading.Event()
    timer = threading.Timer(0.4, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelled):
            run_cancellable(
                [sys.executable, "-c", parent_code], cancelled=cancelled.is_set
            )
    finally:
        timer.cancel()

    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not psutil.pid_exists(child_pid)


def test_cancelled_workflow_stops_before_creating_outputs(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    output = tmp_path / "output"

    with pytest.raises(ProcessCancelled):
        CreationWorkflow().create(
            tmp_path / "missing.mp4",
            output,
            CreativeBrief(),
            cancellation=token,
        )

    assert not output.exists()
