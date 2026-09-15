from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from nimbledesk.client import DaemonClient
from nimbledesk.protocol.models import ActionKind, ActionRequest, ElementTarget
from nimbledesk.testing.fixture_app import FixtureState
from nimbledesk.testing.smoke import _move_and_restore_pointer, validate_capture


class FixtureContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FixtureCase(FixtureContractModel):
    name: str
    passed: bool
    evidence: dict[str, Any]
    error: str | None = None


class FixtureContractReport(FixtureContractModel):
    report_version: str = "1.0.0"
    platform: str
    platform_release: str
    desktop_environment: str | None
    session_type: str | None
    backend: str | None
    started_at: float
    completed_at: float
    capabilities: tuple[str, ...]
    permissions: dict[str, str]
    cases: tuple[FixtureCase, ...]
    passed: bool


async def run_fixture_contract(
    connection_file: Path,
    state_path: Path,
    timeout_seconds: float = 15,
) -> FixtureContractReport:
    if timeout_seconds <= 0:
        raise ValueError("fixture timeout must be positive")
    started_at = time.time()
    client = DaemonClient.from_file(connection_file)
    health = await client.call("health")
    cases: list[FixtureCase] = []
    capabilities: tuple[str, ...] = ()
    permissions: dict[str, str] = {}
    session_id: str | None = None
    try:
        ready = await _wait_for_state(state_path, lambda state: state.ready, timeout_seconds)
        cases.append(FixtureCase(name="fixture_ready", passed=True, evidence=ready.model_dump()))
        session = await client.call(
            "session_start",
            {
                "reason": "NimbleDesk physical backend fixture qualification",
                "config": {"input_enabled": True, "max_actions": 50},
            },
        )
        session_id = str(session["session_id"])
        observation = await client.call("desktop_observe", {"session_id": session_id})
        capabilities = tuple(str(value) for value in observation.get("capabilities", []))
        permissions = {
            str(name): str(value) for name, value in observation.get("permissions", {}).items()
        }
        _require_fixture_window(observation)
        cases.append(
            FixtureCase(
                name="fixture_observed",
                passed=True,
                evidence={
                    "active_application_id": observation.get("active_application_id"),
                    "focused_window_id": observation.get("focused_window_id"),
                    "element_count": len(observation.get("elements", [])),
                },
            )
        )
        capture = await client.call(
            "screen_capture",
            {
                "session_id": session_id,
                "observation_id": observation["observation_id"],
                "options": {
                    "image_format": "jpeg",
                    "max_width": 640,
                    "max_height": 400,
                    "jpeg_quality": 60,
                },
            },
        )
        validate_capture(capture)
        cases.append(
            FixtureCase(
                name="bounded_capture",
                passed=True,
                evidence={
                    "width": capture["width"],
                    "height": capture["height"],
                    "sha256": capture["sha256"],
                },
            )
        )
        pointer = await _move_and_restore_pointer(client, session_id, observation)
        cases.append(FixtureCase(name="pointer_round_trip", passed=True, evidence=pointer))

        observation = await client.call("desktop_observe", {"session_id": session_id})
        typed_text = f"fixture-{platform.system().casefold()}"
        await _execute(
            client,
            ActionRequest(
                session_id=session_id,
                source_observation_id=str(observation["observation_id"]),
                kind=ActionKind.TYPE_TEXT,
                arguments={"text": typed_text, "interval": 0.01},
            ),
        )
        observation = await client.call("desktop_observe", {"session_id": session_id})
        await _click_named_element(client, session_id, observation, "Submit public text")
        submitted = await _wait_for_state(
            state_path, lambda state: state.submitted_text == typed_text, timeout_seconds
        )
        cases.append(
            FixtureCase(
                name="keyboard_and_semantic_submit",
                passed=True,
                evidence={"submitted_characters": len(submitted.submitted_text)},
            )
        )

        observation = await client.call("desktop_observe", {"session_id": session_id})
        initial_counter = submitted.counter
        await _click_named_element(client, session_id, observation, "Increment counter")
        incremented = await _wait_for_state(
            state_path, lambda state: state.counter == initial_counter + 1, timeout_seconds
        )
        cases.append(
            FixtureCase(
                name="semantic_button",
                passed=True,
                evidence={"counter": incremented.counter},
            )
        )

        observation = await client.call("desktop_observe", {"session_id": session_id})
        await _click_named_element(client, session_id, observation, "Enable fixture option")
        checked = await _wait_for_state(
            state_path, lambda state: state.checkbox_enabled, timeout_seconds
        )
        cases.append(
            FixtureCase(
                name="semantic_checkbox",
                passed=True,
                evidence={"checkbox_enabled": checked.checkbox_enabled},
            )
        )
    except Exception as error:
        cases.append(
            FixtureCase(
                name="contract_failure",
                passed=False,
                evidence={},
                error=f"{type(error).__name__}: {error}",
            )
        )
    finally:
        if session_id is not None:
            try:
                await client.call(
                    "session_set_state", {"session_id": session_id, "state": "stopped"}
                )
            except Exception as error:
                cases.append(
                    FixtureCase(
                        name="session_cleanup",
                        passed=False,
                        evidence={},
                        error=f"{type(error).__name__}: {error}",
                    )
                )
    return FixtureContractReport(
        platform=platform.system(),
        platform_release=platform.platform(),
        desktop_environment=os.getenv("XDG_CURRENT_DESKTOP"),
        session_type=os.getenv("XDG_SESSION_TYPE"),
        backend=str(health.get("backend")) if health.get("backend") else None,
        started_at=started_at,
        completed_at=time.time(),
        capabilities=capabilities,
        permissions=permissions,
        cases=tuple(cases),
        passed=bool(cases) and all(case.passed for case in cases),
    )


async def _execute(client: DaemonClient, request: ActionRequest) -> dict[str, Any]:
    result = await client.call("action_execute", {"action": request.model_dump(mode="json")})
    if result.get("status") != "completed":
        raise RuntimeError(f"action failed: {result.get('status')}: {result.get('message')}")
    return result


async def _click_named_element(
    client: DaemonClient,
    session_id: str,
    observation: dict[str, Any],
    name: str,
) -> None:
    matches = [
        element
        for element in observation.get("elements", [])
        if str(element.get("name", "")).strip().casefold() == name.casefold()
        and bool(element.get("enabled", True))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one enabled semantic element named {name!r}, found {len(matches)}"
        )
    element = matches[0]
    await _execute(
        client,
        ActionRequest(
            session_id=session_id,
            source_observation_id=str(observation["observation_id"]),
            expected_application_id=(
                str(observation["active_application_id"])
                if observation.get("active_application_id")
                else None
            ),
            expected_window_id=str(element["window_id"]),
            kind=ActionKind.CLICK,
            target=ElementTarget(
                observation_id=str(observation["observation_id"]),
                element_id=str(element["element_id"]),
            ),
        ),
    )


def _require_fixture_window(observation: dict[str, Any]) -> None:
    windows = observation.get("windows", [])
    if not any("NimbleDesk Backend Fixture" in str(window.get("title", "")) for window in windows):
        raise RuntimeError("the focused NimbleDesk Backend Fixture window was not observed")
    required = {"screen_capture", "pointer", "keyboard", "accessibility"}
    missing = required - {str(value) for value in observation.get("capabilities", [])}
    if missing:
        raise RuntimeError(
            "fixture contract capabilities are unavailable: " + ", ".join(sorted(missing))
        )


async def _wait_for_state(
    path: Path,
    predicate: Callable[[FixtureState], bool],
    timeout_seconds: float,
) -> FixtureState:
    deadline = time.monotonic() + timeout_seconds
    last_state: FixtureState | None = None
    while time.monotonic() < deadline:
        with suppress(OSError, ValueError):
            last_state = FixtureState.model_validate_json(path.read_text(encoding="utf-8"))
        if last_state is not None and predicate(last_state):
            return last_state
        await asyncio.sleep(0.05)
    raise TimeoutError(f"fixture state did not reach the expected value: {last_state}")


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the physical native-backend fixture contract")
    parser.add_argument("--connection-file", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    parsed = parse_args(arguments)
    report = asyncio.run(
        run_fixture_contract(parsed.connection_file, parsed.state, parsed.timeout_seconds)
    )
    parsed.output.parent.mkdir(parents=True, exist_ok=True)
    parsed.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(parsed.output), "passed": report.passed}))
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
