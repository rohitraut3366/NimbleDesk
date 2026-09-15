import sys

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
