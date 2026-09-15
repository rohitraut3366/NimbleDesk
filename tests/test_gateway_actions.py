from __future__ import annotations

from typing import Any

import pytest

import nimbledesk.gateway.server as gateway
from nimbledesk.protocol.models import ActionKind, CoordinateTarget, Point


class RecordingClient:
    def __init__(self) -> None:
        self.method = ""
        self.params: dict[str, Any] = {}

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.method = method
        self.params = params
        return {"status": "completed"}


@pytest.mark.asyncio
async def test_gateway_builds_observation_bound_action(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    result = await gateway._execute_action(
        session_id="session",
        observation_id="observation",
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=10, y=20)),
        expected_application_id="fixture.app",
        expected_window_id="fixture-window",
        arguments={"button": "left"},
    )

    action = recording_client.params["action"]
    assert result == {"status": "completed"}
    assert recording_client.method == "action_execute"
    assert action["source_observation_id"] == "observation"
    assert action["expected_application_id"] == "fixture.app"
    assert action["expected_window_id"] == "fixture-window"
    assert action["target"]["point"] == {"x": 10, "y": 20}
