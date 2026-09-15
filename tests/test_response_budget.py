from __future__ import annotations

from nimbledesk.gateway.budget import compact_observation
from nimbledesk.protocol.models import ResponseBudget


def observation_with_windows(window_count: int) -> dict[str, object]:
    return {
        "observation_id": "observation",
        "sequence": 1,
        "captured_at": 1.0,
        "expires_at": 6.0,
        "platform": "simulator",
        "capabilities": ["screen_capture", "pointer"],
        "permissions": {"screen_capture": "granted", "pointer": "granted"},
        "displays": [
            {
                "display_id": "primary",
                "logical_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
                "physical_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
                "scale": 1,
                "rotation_degrees": 0,
                "primary": True,
            }
        ],
        "cursor": {"x": 10, "y": 20},
        "active_application_id": "fixture.app",
        "focused_window_id": "window-0",
        "windows": [
            {
                "window_id": f"window-{index}",
                "application_id": "fixture.app",
                "application_name": "Fixture",
                "title": "Very long window title " * 50,
                "bounds": {"left": 0, "top": 0, "width": 800, "height": 600},
                "focused": index == 0,
            }
            for index in range(window_count)
        ],
        "warnings": [],
    }


def test_observation_limits_windows_and_reports_estimated_usage() -> None:
    result = compact_observation(
        observation_with_windows(50),
        ResponseBudget(max_estimated_text_tokens=1_000, max_windows=3),
    )

    assert len(result["observation"]["windows"]) == 3
    assert result["usage"]["estimated_text_tokens"] <= 1_000
    assert result["usage"]["truncated_fields"] == ["windows"]


def test_small_budget_preserves_action_safety_fields() -> None:
    result = compact_observation(
        observation_with_windows(1),
        ResponseBudget(max_estimated_text_tokens=128, max_windows=1),
    )

    assert result["observation"]["observation_id"] == "observation"
    assert result["observation"]["active_application_id"] == "fixture.app"
    assert result["observation"]["focused_window_id"] == "window-0"
    assert result["usage"]["estimated_text_tokens"] <= 128
