from __future__ import annotations

from typing import Any

import pytest

import nimbledesk.gateway.server as gateway
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    CoordinateTarget,
    Point,
    SelectorTarget,
)


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
async def test_gateway_exposes_generic_action_and_target_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)
    action = ActionRequest(
        session_id="session", source_observation_id="observation", kind=ActionKind.WAIT
    )

    await gateway.action_execute(action)
    assert recording_client.method == "action_execute"
    assert recording_client.params["action"]["kind"] == "wait"

    await gateway.target_resolve(
        "session", "observation", SelectorTarget(role="button", name="Save")
    )
    assert recording_client.method == "target_resolve"
    assert recording_client.params["target"]["target_type"] == "selector"


@pytest.mark.asyncio
async def test_gateway_starts_compact_session_scoped_creative_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()

    class FakeJob:
        def response(self) -> dict[str, Any]:
            return {
                "job_id": "job",
                "session_id": "session",
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "request": {"source": "/project/source.mp4"},
                "result": None,
                "artifacts": [],
                "variant_options": [],
                "automatic_capabilities": [],
            }

    class FakeJobService:
        request: Any = None
        session_id: str | None = None

        def submit(self, request: Any, session_id: str | None = None) -> FakeJob:
            self.request = request
            self.session_id = session_id
            return FakeJob()

    jobs = FakeJobService()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)
    monkeypatch.setattr(gateway, "JOB_SERVICE", jobs)

    result = await gateway.edit_plan_generate(
        "session",
        "/project/source.mp4",
        "/project/output",
        gateway.CreativeBrief(title="Highlights"),
    )

    assert recording_client.method == "paths_authorize"
    assert recording_client.params["paths"] == [
        "/project/source.mp4",
        "/project/output",
    ]
    assert jobs.session_id == "session"
    assert jobs.request.ffmpeg_render is False
    assert "request" not in result
    assert "result" not in result


@pytest.mark.asyncio
async def test_gateway_exposes_bounded_moment_query_and_domain_packs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.moments_find("session", "index", "grenade kill", 5, 500)
    shooter = await gateway.domain_pack_describe("generic-shooter")

    assert recording_client.method == "media_index_search"
    assert recording_client.params == {
        "session_id": "session",
        "index_id": "index",
        "query": "grenade kill",
        "maximum_results": 5,
        "maximum_tokens": 500,
    }
    assert "grenade_kill" in shooter["importance"]
    assert {pack["pack_id"] for pack in (await gateway.domain_packs_list())["domain_packs"]} == {
        "generic-shooter",
        "general-editorial",
    }


@pytest.mark.asyncio
async def test_gateway_builds_observation_bound_window_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.close_window(
        "session", "observation", "fixture-window", approval_token="approval-token"
    )

    action = recording_client.params["action"]
    assert action["kind"] == "close_window"
    assert action["source_observation_id"] == "observation"
    assert action["expected_window_id"] == "fixture-window"
    assert action["arguments"] == {"window_id": "fixture-window"}
    assert action["approval_token"] == "approval-token"


@pytest.mark.asyncio
async def test_gateway_finds_ui_with_bounded_server_side_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ObservationClient(RecordingClient):
        async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "observation_id": "observation",
                "elements": [
                    {
                        "element_id": "save",
                        "window_id": "editor",
                        "role": "button",
                        "name": "Save project",
                        "enabled": True,
                        "actions": ["press"],
                    },
                    {
                        "element_id": "cancel",
                        "window_id": "editor",
                        "role": "button",
                        "name": "Cancel",
                        "enabled": True,
                    },
                ],
            }

    monkeypatch.setattr(gateway, "client", ObservationClient)

    result = await gateway.ui_find("session", role="button", name="save")

    assert result["observation_id"] == "observation"
    assert [match["element_id"] for match in result["matches"]] == ["save"]
    assert result["usage"]["total_matches"] == 1


@pytest.mark.asyncio
async def test_gateway_condition_wait_reuses_observations_internally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChangingClient(RecordingClient):
        calls = 0

        async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            type(self).calls += 1
            return {
                "observation_id": f"observation-{self.calls}",
                "active_application_id": "editor" if self.calls >= 2 else "launcher",
                "windows": [],
                "elements": [],
            }

    changing = ChangingClient()
    monkeypatch.setattr(gateway, "client", lambda: changing)

    result = await gateway.condition_wait(
        "session",
        "application_active",
        "editor",
        timeout_seconds=1,
        poll_interval_seconds=0.05,
    )

    assert result["matched"] is True
    assert result["observations"] == 2
    assert result["observation_id"] == "observation-2"


@pytest.mark.asyncio
async def test_gateway_discovers_session_and_adapter_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DiscoveryClient(RecordingClient):
        async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            if method == "session_status":
                return {"session_id": "session", "state": "active"}
            return {"adapters": [{"adapter_id": "editor.adapter", "commands": {}}]}

    monkeypatch.setattr(gateway, "client", DiscoveryClient)

    session = await gateway.session_status("session")
    adapter = await gateway.adapter_describe("editor.adapter")

    assert session["state"] == "active"
    assert adapter == {"adapter_id": "editor.adapter", "commands": {}}


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


@pytest.mark.asyncio
async def test_gateway_passes_media_grants_and_bounded_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.session_start(
        "inspect edit", granted_paths=["/projects/edit/analysis"]
    )
    assert recording_client.params["config"]["granted_paths"] == [  # type: ignore[index]
        "/projects/edit/analysis"
    ]

    await gateway.media_index_search("session", "index-1", "clutch", 5, 512)
    assert recording_client.method == "media_index_search"
    assert recording_client.params["maximum_results"] == 5
    assert recording_client.params["maximum_tokens"] == 512


@pytest.mark.asyncio
async def test_gateway_exposes_bounded_clipboard_and_allowlisted_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_client = RecordingClient()
    monkeypatch.setattr(gateway, "client", lambda: recording_client)

    await gateway.session_start(
        "edit",
        input_enabled=True,
        clipboard_enabled=True,
        allowed_applications=["com.example.Editor"],
    )
    assert recording_client.params["config"]["clipboard_enabled"] is True

    await gateway.read_clipboard("session", "observation", maximum_characters=512)
    assert recording_client.params["action"]["kind"] == "read_clipboard"
    assert recording_client.params["action"]["arguments"] == {
        "maximum_characters": 512
    }

    await gateway.write_clipboard(
        "session", "observation", "edit title", approval_token="write-token"
    )
    assert recording_client.params["action"]["approval_token"] == "write-token"

    await gateway.launch_application(
        "session",
        "observation",
        "com.example.Editor",
        approval_token="launch-token",
    )
    assert recording_client.params["action"]["kind"] == "launch_application"
    assert recording_client.params["action"]["approval_token"] == "launch-token"
