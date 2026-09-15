from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

import nimbledesk.creative.vision_http as vision_http
from nimbledesk.creative.vision import VisionRequest, VisionSheet


def test_openai_compatible_vision_worker_sends_bounded_sheets_and_validates_events(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"jpeg-fixture")
    request = VisionRequest(
        source_name="game.mp4",
        content_kind="gameplay",
        sheets=(VisionSheet(path=sheet, timestamps_seconds=(0, 4, 8)),),
        requested_events=("kill", "clutch"),
    )
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "events": [
                                            {
                                                "time_seconds": 8,
                                                "event_type": "kill",
                                                "label": "Confirmed elimination",
                                                "confidence": 0.92,
                                                "evidence": "kill feed and elimination marker",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(http_request: Any, timeout: float) -> FakeResponse:
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(vision_http.urllib.request, "urlopen", fake_urlopen)

    result = vision_http.analyze_request(
        request,
        "https://vision.example/v1/chat/completions",
        "vision-model",
        "secret-key",
        30,
    )

    assert result.events[0].event_type == "kill"
    assert captured["timeout"] == 30
    http_request = captured["request"]
    assert http_request.headers["Authorization"] == "Bearer secret-key"
    payload = json.loads(http_request.data)
    assert payload["model"] == "vision-model"
    assert payload["messages"][0]["content"][1]["image_url"]["detail"] == "low"
    assert "Do not infer a kill only from motion" in payload["messages"][0]["content"][0]["text"]


def test_vision_worker_rejects_insecure_remote_endpoint(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"fixture")
    request = VisionRequest(
        source_name="game.mp4",
        content_kind="gameplay",
        sheets=(VisionSheet(path=sheet, timestamps_seconds=(0,)),),
        requested_events=("kill",),
    )

    with pytest.raises(vision_http.VisionHttpError, match="HTTPS or local loopback"):
        vision_http.analyze_request(
            request,
            "http://vision.example/v1/chat/completions",
            "vision-model",
            None,
            30,
        )


def test_vision_worker_accepts_fenced_json_response() -> None:
    decoded = vision_http._decode_json_content('```json\n{"events": []}\n```')

    assert decoded == {"events": []}
