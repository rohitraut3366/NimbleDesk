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
    assert action["recovery"] == {"max_reobservations": 0}
    assert action["expected_application_id"] == "fixture.app"
    assert action["expected_window_id"] == "fixture-window"
    assert action["target"]["point"] == {"x": 10, "y": 20}


@pytest.mark.asyncio
async def test_gateway_builds_semantic_element_action(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.click_element(
        session_id="session",
        observation_id="observation",
        element_id="create-button",
        expected_application_id="fixture.app",
    )

    action = recording_client.params["action"]
    assert action["target"] == {
        "target_type": "element",
        "observation_id": "observation",
        "element_id": "create-button",
    }
    assert action["source_observation_id"] == "observation"
    assert action["recovery"] == {"max_reobservations": 1}


@pytest.mark.asyncio
async def test_gateway_builds_signature_bound_visual_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.click_visual(
        session_id="session",
        observation_id="observation",
        left=10,
        top=20,
        width=100,
        height=40,
        signature="a" * 64,
        confidence=0.92,
        expected_window_id="fixture-window",
    )

    action = recording_client.params["action"]
    assert action["target"] == {
        "target_type": "visual",
        "observation_id": "observation",
        "bounds": {"left": 10, "top": 20, "width": 100, "height": 40},
        "signature": "a" * 64,
        "confidence": 0.92,
    }
    assert action["recovery"] == {"max_reobservations": 1}


@pytest.mark.asyncio
async def test_gateway_returns_lossless_region_signature_without_image_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CaptureClient(RecordingClient):
        async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            self.method = method
            self.params = params
            return {
                "sha256": "b" * 64,
                "width": 100,
                "height": 40,
                "data_base64": "private-image",
                "usage": {"encoded_bytes": 123},
            }

    recording_client = CaptureClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    result = await gateway.capture_region_signature(
        "session", "observation", 10, 20, 100, 40
    )

    assert result["signature"] == "b" * 64
    assert "data_base64" not in result
    assert recording_client.params["options"]["image_format"] == "png"


@pytest.mark.asyncio
async def test_gateway_builds_approved_application_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.application_command(
        session_id="session",
        observation_id="observation",
        adapter_id="example.editor",
        command="render",
        arguments={"timeline": "main"},
        approval_token="approved-token",
    )

    action = recording_client.params["action"]
    assert action["kind"] == "app_command"
    assert action["arguments"] == {
        "adapter_id": "example.editor",
        "command": "render",
        "arguments": {"timeline": "main"},
    }
    assert action["approval_token"] == "approved-token"


@pytest.mark.asyncio
async def test_gateway_only_exposes_read_side_of_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.approval_status("approval-1")

    assert recording_client.method == "approval_status"
    assert recording_client.params == {"approval_id": "approval-1"}
