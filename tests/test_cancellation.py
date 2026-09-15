from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

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
