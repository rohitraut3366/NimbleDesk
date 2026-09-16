from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pytest import MonkeyPatch

import nimbledesk.testing.adapter_isolation_contract as isolation_contract
from nimbledesk.adapters.models import AdapterManifest, AdapterResult
from nimbledesk.adapters.runner import AdapterError
from nimbledesk.testing.release_evidence import ISOLATION_CASES


def test_isolation_contract_records_every_required_case(
    monkeypatch: MonkeyPatch,
) -> None:
    invocations: list[tuple[str, bool, tuple[Path, ...]]] = []

    class FakeRunner:
        def execute(
            self,
            manifest: AdapterManifest,
            command: str,
            _arguments: dict[str, object],
            granted_paths: tuple[Path, ...] = (),
            **_kwargs: object,
        ) -> AdapterResult:
            invocations.append((command, manifest.network_access, granted_paths))
            if command == "probe":
                return _result(
                    {
                        "allowed_content": "declared",
                        "declared_write": {"succeeded": True},
                        "denied_read": {"succeeded": False},
                        "denied_write": {"succeeded": False},
                        "child_process": False,
                    }
                )
            if command == "network":
                return _result({"connected": manifest.network_access})
            if command == "exhaust_memory":
                raise AdapterError("adapter exceeded the 512 MiB memory limit")
            if command == "sleep":
                raise AdapterError("adapter execution timed out")
            if command == "benign":
                return _result({"status": "ready"})
            raise AssertionError(command)

    monkeypatch.setattr(isolation_contract, "IsolatedAdapterRunner", FakeRunner)
    monkeypatch.setattr(isolation_contract, "_local_listener", _fake_listener)
    monkeypatch.setattr(isolation_contract.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        isolation_contract.platform, "platform", lambda: "macOS-qualified"
    )

    report = isolation_contract.run_contract("macos-arm64")

    assert report.passed
    assert report.target_id == "macos-arm64"
    assert {case.name for case in report.cases} == ISOLATION_CASES
    assert len(report.malicious_fixture_sha256) == 64
    probe_call = next(call for call in invocations if call[0] == "probe")
    assert len(probe_call[2]) == 2
    assert ("network", False, ()) in invocations
    assert ("network", True, ()) in invocations


def _result(result: dict[str, object]) -> AdapterResult:
    return AdapterResult(success=True, result=result)


@contextmanager
def _fake_listener() -> Iterator[tuple[str, int]]:
    yield "127.0.0.1", 43123
