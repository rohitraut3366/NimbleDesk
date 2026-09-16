from __future__ import annotations

import json
from typing import Any

from nimbledesk.protocol.models import ResponseBudget

MAXIMUM_LABEL_CHARACTERS = 200


def compact_observation(
    observation: dict[str, Any],
    budget: ResponseBudget,
    *,
    window_offset: int = 0,
    element_offset: int = 0,
    omit_unchanged: bool = True,
) -> dict[str, Any]:
    if window_offset < 0 or element_offset < 0:
        raise ValueError("observation offsets cannot be negative")
    windows = sorted(
        observation.get("windows", []),
        key=lambda window: not bool(window.get("focused")),
    )
    truncated_fields: list[str] = []
    total_windows = len(windows)
    if total_windows > window_offset + budget.max_windows:
        truncated_fields.append("windows")
    windows = [
        _compact_window(window)
        for window in windows[window_offset : window_offset + budget.max_windows]
    ]
    elements = sorted(
        observation.get("elements", []),
        key=lambda element: (
            not bool(element.get("focused")),
            not bool(element.get("enabled", True) and element.get("actions")),
        ),
    )
    total_elements = len(elements)
    if total_elements > element_offset + budget.max_elements:
        truncated_fields.append("elements")
    elements = [
        _compact_element(element)
        for element in elements[element_offset : element_offset + budget.max_elements]
    ]
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
        "screenshot_sha256": observation.get("screenshot_sha256"),
        "windows_sha256": observation.get("windows_sha256"),
        "ui_tree_sha256": observation.get("ui_tree_sha256"),
        "change_summary": observation.get("change_summary"),
        "windows": windows,
        "elements": elements,
        "warnings": [
            _truncate_text(str(warning)) for warning in observation.get("warnings", [])[:10]
        ],
    }
    changes = summary.get("change_summary")
    if omit_unchanged and isinstance(changes, dict) and changes.get("unchanged"):
        summary["displays"] = []
        summary["windows"] = []
        summary["elements"] = []
        summary["permissions"] = {}
        summary["warnings"] = []
        truncated_fields.extend(
            ("unchanged:displays", "unchanged:windows", "unchanged:elements")
        )
    for field in (
        "change_summary",
        "screenshot_sha256",
        "windows_sha256",
        "ui_tree_sha256",
    ):
        if summary.get(field) is None:
            summary.pop(field, None)
    _shrink_to_budget(summary, budget.max_estimated_text_tokens, truncated_fields)
    estimated_tokens = estimate_text_tokens(summary)
    returned_windows = len(summary["windows"])
    returned_elements = len(summary["elements"])
    continuation = None
    unchanged_omitted = bool(
        omit_unchanged and isinstance(changes, dict) and changes.get("unchanged")
    )
    if not unchanged_omitted and (
        window_offset + returned_windows < total_windows
        or element_offset + returned_elements < total_elements
    ):
        continuation = {
            "observation_id": observation["observation_id"],
            "window_offset": window_offset + returned_windows,
            "element_offset": element_offset + returned_elements,
        }
    return {
        "observation": summary,
        "continuation": continuation,
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
    for field in (
        "change_summary",
        "screenshot_sha256",
        "windows_sha256",
        "ui_tree_sha256",
    ):
        if estimate_text_tokens(summary) <= maximum_tokens:
            return
        if field in summary:
            summary.pop(field)
            truncated_fields.append(field)
    for field in ("elements", "windows", "permissions", "warnings"):
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


def _compact_element(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "element_id": element.get("element_id"),
        "window_id": element.get("window_id"),
        "role": _truncate_text(str(element.get("role", ""))),
        "name": _truncate_text(str(element.get("name", ""))),
        "bounds": element.get("bounds"),
        "value": _truncate_text(str(element["value"])) if element.get("value") else None,
        "enabled": bool(element.get("enabled", True)),
        "focused": bool(element.get("focused")),
        "actions": element.get("actions", []),
    }
