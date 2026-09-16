import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import nimbledesk.cli as cli


def test_unified_cli_dispatches_component_arguments(monkeypatch: MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setitem(cli.COMMANDS, "smoke", lambda: called.append(sys.argv.copy()))

    cli.main(["smoke", "--test-input"])

    assert called == [["nimbledesk smoke", "--test-input"]]


def test_unified_cli_lists_service_management() -> None:
    assert "service" in cli.COMMANDS


def test_unified_cli_lists_diagnostics() -> None:
    assert "diagnostics" in cli.COMMANDS


def test_unified_cli_lists_signed_updates() -> None:
    assert "update" in cli.COMMANDS


def test_start_uses_configured_runtime_connection_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "custom-runtime"
    runtime.mkdir()
    connection_file = runtime / "connection.json"
    connection_file.write_text("{}", encoding="utf-8")

    class ReadyDaemon:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

    daemon = ReadyDaemon()
    monkeypatch.setenv("NIMBLEDESK_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda _command: daemon)
    monkeypatch.setattr(cli, "studio_main", lambda: None)

    cli._start([])

    assert daemon.returncode == 0


def test_frozen_launcher_hands_commands_to_managed_version(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "managed" / "nimbledesk"
    executable.parent.mkdir()
    executable.write_bytes(b"managed")
    calls: list[tuple[str, list[str]]] = []

    class FakeManager:
        def state(self) -> object:
            return object()

        def executable(self) -> Path:
            return executable

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli, "UpdateManager", FakeManager)
    monkeypatch.setattr(cli.os, "execv", lambda path, arguments: calls.append((path, arguments)))

    cli._handoff_to_managed_version(["studio", "--port", "9000"])

    assert calls == [
        (str(executable), [str(executable), "studio", "--port", "9000"]),
    ]


def test_frozen_launcher_keeps_rollback_on_stable_executable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        cli.os,
        "execv",
        lambda path, arguments: pytest.fail(f"unexpected handoff to {path}: {arguments}"),
    )

    cli._handoff_to_managed_version(["update", "rollback"])
