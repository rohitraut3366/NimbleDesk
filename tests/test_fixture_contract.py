import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

import nimbledesk.testing.fixture_contract as fixture_contract
from nimbledesk.testing.fixture_app import FixtureState


@pytest.mark.asyncio
async def test_fixture_contract_records_verified_native_workflow(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(FixtureState(ready=True).model_dump_json(), encoding="utf-8")
    image = b"fixture-capture"

    class FakeClient:
        cursor = {"x": 10, "y": 20}
        typed_text = ""
        sequence = 0

        @classmethod
        def from_file(cls, _path: Path) -> Any:
            return cls()

        async def call(
            self, method: str, parameters: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            parameters = parameters or {}
            if method == "health":
                return {"status": "ok", "backend": "native:fixture"}
            if method == "session_start":
                return {"session_id": "session-1"}
            if method == "session_set_state":
                return {"state": "stopped"}
            if method == "desktop_observe":
                self.sequence += 1
                return {
                    "observation_id": f"observation-{self.sequence}",
                    "platform": "test",
                    "capabilities": [
                        "screen_capture",
                        "pointer",
                        "keyboard",
                        "accessibility",
                    ],
                    "permissions": {"accessibility": "granted"},
                    "active_application_id": "fixture.app",
                    "focused_window_id": "fixture-window",
                    "windows": [
                        {
                            "window_id": "fixture-window",
                            "title": "NimbleDesk Backend Fixture",
                        }
                    ],
                    "elements": [
                        {
                            "element_id": "submit",
                            "window_id": "fixture-window",
                            "name": "Submit public text",
                            "enabled": True,
                        },
                        {
                            "element_id": "increment",
                            "window_id": "fixture-window",
                            "name": "Increment counter",
                            "enabled": True,
                        },
                        {
                            "element_id": "checkbox",
                            "window_id": "fixture-window",
                            "name": "Enable fixture option",
                            "enabled": True,
                        },
                    ],
                    "cursor": self.cursor,
                    "displays": [
                        {
                            "logical_bounds": {
                                "left": 0,
                                "top": 0,
                                "width": 1440,
                                "height": 900,
                            }
                        }
                    ],
                }
            if method == "screen_capture":
                return {
                    "data_base64": base64.b64encode(image).decode(),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "width": 640,
                    "height": 400,
                    "mime_type": "image/jpeg",
                }
            if method == "action_execute":
                action = parameters["action"]
                kind = action["kind"]
                if kind == "move_pointer":
                    self.cursor = action["target"]["point"]
                elif kind == "type_text":
                    self.typed_text = action["arguments"]["text"]
                elif kind == "click":
                    state = FixtureState.model_validate_json(state_path.read_text())
                    element_id = action["target"]["element_id"]
                    if element_id == "submit":
                        state.submitted_text = self.typed_text
                    elif element_id == "increment":
                        state.counter += 1
                    elif element_id == "checkbox":
                        state.checkbox_enabled = True
                    state_path.write_text(state.model_dump_json(), encoding="utf-8")
                return {"status": "completed", "message": "ok"}
            raise AssertionError(method)

    monkeypatch.setattr(fixture_contract, "DaemonClient", FakeClient)

    report = await fixture_contract.run_fixture_contract(
        tmp_path / "connection.json", state_path, timeout_seconds=1
    )

    assert report.passed
    assert report.backend == "native:fixture"
    assert {case.name for case in report.cases} == {
        "fixture_ready",
        "fixture_observed",
        "bounded_capture",
        "pointer_round_trip",
        "keyboard_and_semantic_submit",
        "semantic_button",
        "semantic_checkbox",
    }


def test_fixture_window_requires_native_contract_capabilities() -> None:
    observation = {
        "windows": [{"title": "NimbleDesk Backend Fixture"}],
        "capabilities": ["screen_capture"],
    }

    with pytest.raises(RuntimeError, match="accessibility, keyboard, pointer"):
        fixture_contract._require_fixture_window(observation)
