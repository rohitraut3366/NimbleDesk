from __future__ import annotations

import platform
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from nimbledesk.adapters import command_runner, windows_appcontainer
from nimbledesk.adapters.command_runner import run_isolated_command
from nimbledesk.adapters.runner import AdapterError
from nimbledesk.media.process import ProcessCancelled


class CompletedFakeProcess:
    stdin = None
    returncode: int | None = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1

    def close(self) -> None:
        pass


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


@pytest.mark.skipif(
    platform.system() not in {"Darwin", "Linux"}
    or (platform.system() == "Darwin" and shutil.which("sandbox-exec") is None)
    or (platform.system() == "Linux" and shutil.which("bwrap") is None),
    reason="a supported provider sandbox is not installed",
)
def test_provider_stdout_is_bounded(tmp_path: Path) -> None:
    worker = tmp_path / "provider.py"
    worker.write_text("import sys\nsys.stdout.write('x' * 1_100_000)\n", encoding="utf-8")

    with pytest.raises(AdapterError, match="stdout exceeded"):
        run_isolated_command(
            [sys.executable, str(worker)],
            readable_paths=(worker,),
            writable_paths=(),
            network_access=False,
            timeout_seconds=10,
            code_paths=(worker,),
        )


@pytest.mark.skipif(
    platform.system() != "Darwin" or shutil.which("sandbox-exec") is None,
    reason="this process-group contract is exercised on macOS",
)
def test_cancelled_provider_kills_sigterm_ignoring_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "survived.txt"
    child_code = (
        "import pathlib,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(1);"
        f"pathlib.Path({str(marker)!r}).write_text('survived')"
    )
    worker = tmp_path / "provider.py"
    worker.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    cancelled = threading.Event()
    timer = threading.Timer(0.3, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelled):
            run_isolated_command(
                [sys.executable, str(worker)],
                readable_paths=(worker, tmp_path),
                writable_paths=(tmp_path,),
                network_access=False,
                timeout_seconds=10,
                cancelled=cancelled.is_set,
                code_paths=(worker,),
            )
    finally:
        timer.cancel()

    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.skipif(
    platform.system() not in {"Darwin", "Linux"}
    or (platform.system() == "Darwin" and shutil.which("sandbox-exec") is None)
    or (platform.system() == "Linux" and shutil.which("bwrap") is None),
    reason="a supported provider sandbox is not installed",
)
def test_provider_network_requires_an_explicit_grant(tmp_path: Path) -> None:
    worker = tmp_path / "provider.py"
    worker.write_text(
        "import socket,sys\n"
        "connection=socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=1)\n"
        "connection.close()\n",
        encoding="utf-8",
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        port = server.getsockname()[1]

        denied = run_isolated_command(
            [sys.executable, str(worker), str(port)],
            readable_paths=(worker,),
            writable_paths=(),
            network_access=False,
            timeout_seconds=10,
            code_paths=(worker,),
        )
        allowed = run_isolated_command(
            [sys.executable, str(worker), str(port)],
            readable_paths=(worker,),
            writable_paths=(),
            network_access=True,
            timeout_seconds=10,
            code_paths=(worker,),
        )

    assert denied.returncode != 0
    assert allowed.returncode == 0


def test_frozen_windows_provider_uses_appcontainer_with_exact_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "provider.exe"
    executable.write_bytes(b"fixture")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    output = tmp_path / "output"
    output.mkdir()
    captured: dict[str, object] = {}

    def start_fake(command: list[str], **arguments: object) -> CompletedFakeProcess:
        captured["command"] = command
        captured.update(arguments)
        return CompletedFakeProcess()

    monkeypatch.setattr(command_runner.platform, "system", lambda: "Windows")
    monkeypatch.setattr(command_runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        windows_appcontainer, "start_windows_appcontainer_process", start_fake
    )

    completed = run_isolated_command(
        [str(executable), "{request}", "{response}"],
        readable_paths=(source,),
        writable_paths=(output,),
        network_access=True,
        timeout_seconds=10,
        code_paths=(executable,),
    )

    readable_paths = tuple(captured["readable_paths"])
    writable_paths = tuple(captured["writable_paths"])
    assert completed.returncode == 0
    assert captured["network_access"] is True
    assert executable.resolve() in readable_paths
    assert source.resolve() in readable_paths
    assert output.resolve() in readable_paths
    assert output.resolve() in writable_paths


def test_provider_cannot_override_process_control_environment(tmp_path: Path) -> None:
    executable = tmp_path / "provider"
    executable.write_bytes(b"fixture")

    with pytest.raises(AdapterError, match="cannot override.*PYTHONPATH"):
        run_isolated_command(
            [str(executable)],
            readable_paths=(),
            writable_paths=(),
            network_access=False,
            timeout_seconds=10,
            environment_variables=("PYTHONPATH",),
        )
