from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

import pytest

from nimbledesk.adapters.command_runner import run_isolated_command


@pytest.mark.skipif(
    platform.system() not in {"Darwin", "Linux"}
    or (platform.system() == "Darwin" and shutil.which("sandbox-exec") is None)
    or (platform.system() == "Linux" and shutil.which("bwrap") is None),
    reason="a supported provider sandbox is not installed",
)
def test_provider_cannot_read_an_undeclared_file(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forbidden = tmp_path / "private.txt"
    forbidden.write_text("private", encoding="utf-8")
    response = allowed / "response.json"
    worker = tmp_path / "provider.py"
    worker.write_text(
        "import pathlib, sys\n"
        "secret = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "pathlib.Path(sys.argv[2]).write_text(secret, encoding='utf-8')\n",
        encoding="utf-8",
    )

    completed = run_isolated_command(
        [sys.executable, str(worker), str(forbidden), str(response)],
        readable_paths=(worker, allowed),
        writable_paths=(allowed,),
        network_access=False,
        timeout_seconds=10,
        code_paths=(worker,),
    )

    assert completed.returncode != 0
    assert not response.exists()
