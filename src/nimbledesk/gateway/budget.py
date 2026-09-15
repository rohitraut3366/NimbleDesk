from __future__ import annotations

import json
from typing import Any

from nimbledesk.protocol.models import ResponseBudget

MAXIMUM_LABEL_CHARACTERS = 200


def compact_observation(
    observation: dict[str, Any],
    budget: ResponseBudget,
) -> dict[str, Any]:
    windows = sorted(
        observation.get("windows", []),
        key=lambda window: not bool(window.get("focused")),
    )
    truncated_fields: list[str] = []
    if len(windows) > budget.max_windows:
        truncated_fields.append("windows")
    windows = [_compact_window(window) for window in windows[: budget.max_windows]]
    summary = {
        "observation_id": observation["observation_id"],
        "sequence": observation["sequence"],
        "captured_at": observation["captured_at"],
        "expires_at": observation["expires_at"],
        "platform": observation["platform"],
        "capabilities": observation.get("capabilities", []),
        "permissions": observation.get("permissions", {}),
        "displays": observation.get("displays", []),
        "cursor": observation["cursor"],
        "active_application_id": observation.get("active_application_id"),
        "focused_window_id": observation.get("focused_window_id"),
        "windows": windows,
        "warnings": [
            _truncate_text(str(warning)) for warning in observation.get("warnings", [])[:10]
        ],
    }
    _shrink_to_budget(summary, budget.max_estimated_text_tokens, truncated_fields)
    estimated_tokens = estimate_text_tokens(summary)
    return {
        "observation": summary,
        "usage": {
            "estimated_text_tokens": estimated_tokens,
            "maximum_text_tokens": budget.max_estimated_text_tokens,
            "truncated_fields": sorted(set(truncated_fields)),
            "estimator": "utf8_bytes_divided_by_4",
        },
    }


def estimate_text_tokens(value: Any) -> int:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def _compact_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_id": window.get("window_id"),
        "application_id": window.get("application_id"),
        "application_name": _truncate_text(str(window.get("application_name", ""))),
        "title": _truncate_text(str(window.get("title", ""))),
        "bounds": window.get("bounds"),
        "focused": bool(window.get("focused")),
    }


def _shrink_to_budget(
    summary: dict[str, Any],
    maximum_tokens: int,
    truncated_fields: list[str],
) -> None:
    for field in ("windows", "permissions", "warnings"):
        if estimate_text_tokens(summary) <= maximum_tokens:
            return
        value = summary[field]
        if value:
            summary[field] = [] if isinstance(value, list) else {}
            truncated_fields.append(field)
    if estimate_text_tokens(summary) > maximum_tokens:
        raise ValueError("text budget is too small for required observation safety fields")


def _truncate_text(value: str) -> str:
    if len(value) <= MAXIMUM_LABEL_CHARACTERS:
        return value
    return value[: MAXIMUM_LABEL_CHARACTERS - 1] + "…"
