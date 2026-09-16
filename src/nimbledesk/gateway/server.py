from __future__ import annotations

import asyncio
import base64
import os
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from nimbledesk.client import DaemonClient
from nimbledesk.gateway.budget import compact_observation, estimate_text_tokens
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    CaptureOptions,
    CoordinateTarget,
    ElementTarget,
    Point,
    RecoveryOptions,
    Rectangle,
    ResponseBudget,
    Target,
    TextTarget,
    VisualTarget,
)

mcp = FastMCP("NimbleDesk")


@lru_cache(maxsize=1)
def client() -> DaemonClient:
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    default_path = Path.home() / ".nimbledesk" / "runtime" / "connection.json"
    path = Path(configured) if configured else default_path
    return DaemonClient.from_file(path)


@mcp.tool()
async def health() -> dict[str, Any]:
    """Check whether the local NimbleDesk daemon is available."""
    return await client().call("health")


@mcp.tool()
async def session_start(
    reason: str,
    input_enabled: bool = False,
    clipboard_enabled: bool = False,
    allowed_applications: list[str] | None = None,
    granted_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Start a bounded desktop-control session for a clear user-provided reason."""
    return await client().call(
        "session_start",
        {
            "reason": reason,
            "config": {
                "input_enabled": input_enabled,
                "clipboard_enabled": clipboard_enabled,
                "allowed_applications": allowed_applications or [],
                "granted_paths": granted_paths or [],
            },
        },
    )


@mcp.tool()
async def session_status(session_id: str) -> dict[str, Any]:
    """Return the current state, limits, and action usage for one session."""
    return await client().call("session_status", {"session_id": session_id})


@mcp.tool()
async def capabilities_get() -> dict[str, Any]:
    """Return the selected backend and currently advertised runtime capabilities."""
    return await client().call("health")


@mcp.tool()
async def permissions_get(session_id: str) -> dict[str, Any]:
    """Probe current OS permission states through a fresh bounded observation."""
    observation = await client().call("desktop_observe", {"session_id": session_id})
    return {
        "observation_id": observation["observation_id"],
        "permissions": observation.get("permissions", {}),
        "capabilities": observation.get("capabilities", []),
        "warnings": observation.get("warnings", [])[:10],
    }


@mcp.tool()
async def desktop_observe(
    session_id: str,
    max_estimated_text_tokens: int = 2_000,
    max_windows: int = 10,
    max_elements: int = 100,
) -> dict[str, Any]:
    """Observe displays, cursor, focused application, windows, and capabilities."""
    observation = await client().call("desktop_observe", {"session_id": session_id})
    budget = ResponseBudget(
        max_estimated_text_tokens=max_estimated_text_tokens,
        max_windows=max_windows,
        max_elements=max_elements,
    )
    return compact_observation(observation, budget)


@mcp.tool()
async def ui_find(
    session_id: str,
    role: str | None = None,
    name: str | None = None,
    exact_name: bool = False,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Find enabled accessibility elements server-side without returning the full UI tree."""
    if not role and not name:
        raise ValueError("ui_find requires a role or name")
    if not 1 <= maximum_results <= 100 or not 128 <= maximum_tokens <= 100_000:
        raise ValueError("UI result or token budget is outside allowed bounds")
    observation = await client().call("desktop_observe", {"session_id": session_id})
    normalized_role = role.casefold() if role else None
    normalized_name = name.casefold() if name else None
    matches = []
    for element in observation.get("elements", []):
        element_name = str(element.get("name", ""))
        role_matches = (
            normalized_role is None
            or str(element.get("role", "")).casefold() == normalized_role
        )
        name_matches = normalized_name is None or (
            element_name.casefold() == normalized_name
            if exact_name
            else normalized_name in element_name.casefold()
        )
        if role_matches and name_matches and element.get("enabled", True):
            matches.append(
                {
                    key: element.get(key)
                    for key in (
                        "element_id",
                        "window_id",
                        "role",
                        "name",
                        "bounds",
                        "focused",
                        "actions",
                    )
                }
            )
    returned = matches[:maximum_results]
    while returned and estimate_text_tokens(returned) > maximum_tokens:
        returned.pop()
    return {
        "observation_id": observation["observation_id"],
        "matches": returned,
        "usage": {
            "estimated_text_tokens": estimate_text_tokens(returned),
            "maximum_text_tokens": maximum_tokens,
            "total_matches": len(matches),
            "truncated": len(returned) < len(matches),
        },
    }


@mcp.tool()
async def target_resolve(
    session_id: str, observation_id: str, target: Target
) -> dict[str, Any]:
    """Resolve one coordinate, semantic, selector, visual, or OCR target without input."""
    return await client().call(
        "target_resolve",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "target": target.model_dump(mode="json"),
        },
    )


@mcp.tool()
async def action_execute(action: ActionRequest) -> dict[str, Any]:
    """Execute one complete governed action protocol request through the desktop daemon."""
    return await client().call(
        "action_execute", {"action": action.model_dump(mode="json")}
    )


@mcp.tool()
async def condition_wait(
    session_id: str,
    condition_type: Literal[
        "application_active", "window_focused", "element_present", "element_absent"
    ],
    value: str,
    role: str | None = None,
    timeout_seconds: float = 10,
    poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Wait internally for an application, window, or accessible element state change."""
    if not value:
        raise ValueError("condition value cannot be empty")
    if not 0.1 <= timeout_seconds <= 60 or not 0.05 <= poll_interval_seconds <= 2:
        raise ValueError("condition wait timing is outside allowed bounds")
    deadline = monotonic() + timeout_seconds
    observations = 0
    while True:
        observation = await client().call("desktop_observe", {"session_id": session_id})
        observations += 1
        if _condition_matches(observation, condition_type, value, role):
            return {
                "matched": True,
                "condition_type": condition_type,
                "value": value,
                "observation_id": observation["observation_id"],
                "observations": observations,
            }
        remaining = deadline - monotonic()
        if remaining <= 0:
            return {
                "matched": False,
                "condition_type": condition_type,
                "value": value,
                "observation_id": observation["observation_id"],
                "observations": observations,
                "reason": "condition wait timed out",
            }
        await asyncio.sleep(min(poll_interval_seconds, remaining))


@mcp.tool()
async def media_index_open(session_id: str, index_path: str) -> dict[str, Any]:
    """Open a content index within the session's explicit file grants and return a stable handle."""
    return await client().call(
        "media_index_open", {"session_id": session_id, "index_path": index_path}
    )


@mcp.tool()
async def media_index_search(
    session_id: str,
    index_id: str,
    query: str,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Search time-aligned labels, transcript text, and evidence with a bounded response."""
    return await client().call(
        "media_index_search",
        {
            "session_id": session_id,
            "index_id": index_id,
            "query": query,
            "maximum_results": maximum_results,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def media_index_detail(
    session_id: str,
    index_id: str,
    result_id: str,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Retrieve one media search result by stable ID within a text-token budget."""
    return await client().call(
        "media_index_detail",
        {
            "session_id": session_id,
            "index_id": index_id,
            "result_id": result_id,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def take_screenshot(
    session_id: str,
    observation_id: str,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    image_format: Literal["png", "jpeg"] = "jpeg",
    max_width: int = 1280,
    max_height: int = 800,
    jpeg_quality: int = 75,
) -> Image:
    """Capture the screen for the latest observation, optionally cropped to a region."""
    region_values = (left, top, width, height)
    if any(value is not None for value in region_values) and not all(
        value is not None for value in region_values
    ):
        raise ValueError("left, top, width, and height must be provided together")
    region = None
    if all(value is not None for value in region_values):
        region = {"left": left, "top": top, "width": width, "height": height}
    options = CaptureOptions(
        image_format=image_format,
        max_width=max_width,
        max_height=max_height,
        jpeg_quality=jpeg_quality,
    )
    capture = await client().call(
        "screen_capture",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "region": region,
            "options": options.model_dump(mode="json"),
        },
    )
    return Image(
        data=base64.b64decode(capture["data_base64"]),
        format="jpeg" if capture["mime_type"] == "image/jpeg" else "png",
    )


@mcp.tool()
async def capture_region_signature(
    session_id: str,
    observation_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Capture a lossless target crop and return its execution signature without image bytes."""
    bounds = {"left": left, "top": top, "width": width, "height": height}
    capture = await client().call(
        "screen_capture",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "region": bounds,
            "options": CaptureOptions(
                image_format="png",
                max_width=max(64, min(4096, width)),
                max_height=max(64, min(4096, height)),
            ).model_dump(mode="json"),
        },
    )
    return {
        "observation_id": observation_id,
        "bounds": bounds,
        "signature": capture["sha256"],
        "width": capture["width"],
        "height": capture["height"],
        "usage": capture.get("usage"),
    }


@mcp.tool()
async def click(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    button: Literal["left", "middle", "right"] = "left",
    clicks: int = 1,
    interval: float = 0.1,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Click a coordinate from the latest observation after stale-state validation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": button, "clicks": clicks, "interval": interval},
    )


@mcp.tool()
async def click_element(
    session_id: str,
    observation_id: str,
    element_id: str,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Invoke a semantic UI element from the latest accessibility observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=ElementTarget(observation_id=observation_id, element_id=element_id),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def click_visual(
    session_id: str,
    observation_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
    signature: str,
    confidence: float,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Click a visual crop only if a lossless recapture still has the supplied signature."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=VisualTarget(
            observation_id=observation_id,
            bounds=Rectangle(left=left, top=top, width=width, height=height),
            signature=signature,
            confidence=confidence,
        ),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def click_text(
    session_id: str,
    observation_id: str,
    text: str,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    exact: bool = False,
    minimum_confidence: float = 0.75,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Locate visible text with local OCR and click only an unambiguous fresh match."""
    values = (left, top, width, height)
    if any(value is not None for value in values) and not all(
        value is not None for value in values
    ):
        raise ValueError("left, top, width, and height must be provided together")
    bounds = None
    if all(value is not None for value in values):
        assert left is not None and top is not None
        assert width is not None and height is not None
        bounds = Rectangle(left=left, top=top, width=width, height=height)
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=TextTarget(
            observation_id=observation_id,
            text=text,
            search_bounds=bounds,
            exact=exact,
            minimum_confidence=minimum_confidence,
        ),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def move_mouse(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    duration: float = 0.2,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Move the pointer to a coordinate from the latest observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.MOVE_POINTER,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"duration": duration},
    )


@mcp.tool()
async def drag_to(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    duration: float = 0.5,
    button: Literal["left", "middle", "right"] = "left",
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Drag from the current pointer position to a coordinate."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.DRAG,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"duration": duration, "button": button},
    )


@mcp.tool()
async def scroll(
    session_id: str,
    observation_id: str,
    amount: int,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Scroll vertically; positive values move up and negative values move down."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.SCROLL,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"amount": amount},
    )


@mcp.tool()
async def type_text(
    session_id: str,
    observation_id: str,
    text: str,
    interval: float = 0.02,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Type text into the currently focused control."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.TYPE_TEXT,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"text": text, "interval": interval},
    )


@mcp.tool()
async def press_key(
    session_id: str,
    observation_id: str,
    key: str,
    presses: int = 1,
    interval: float = 0.1,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Press a named key such as enter, tab, escape, backspace, or f5."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.PRESS_KEY,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"key": key, "presses": presses, "interval": interval},
    )


@mcp.tool()
async def hotkey(
    session_id: str,
    observation_id: str,
    keys: list[str],
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Press a key combination such as ['command', 's'] or ['ctrl', 's']."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.HOTKEY,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"keys": keys},
    )


@mcp.tool()
async def focus_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    expected_application_id: str | None = None,
) -> dict[str, Any]:
    """Focus a native window from the latest observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.FOCUS_WINDOW,
        expected_application_id=expected_application_id,
        arguments={"window_id": window_id},
    )


@mcp.tool()
async def move_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    left: int,
    top: int,
) -> dict[str, Any]:
    """Move a native window to logical desktop coordinates."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MOVE_WINDOW, {"left": left, "top": top}
    )


@mcp.tool()
async def resize_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Resize a native window in logical desktop units."""
    return await _window_action(
        session_id,
        observation_id,
        window_id,
        ActionKind.RESIZE_WINDOW,
        {"width": width, "height": height},
    )


@mcp.tool()
async def minimize_window(
    session_id: str, observation_id: str, window_id: str
) -> dict[str, Any]:
    """Minimize a native window."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MINIMIZE_WINDOW
    )


@mcp.tool()
async def maximize_window(
    session_id: str, observation_id: str, window_id: str
) -> dict[str, Any]:
    """Maximize a native window."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MAXIMIZE_WINDOW
    )


@mcp.tool()
async def close_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Request a native window close after exact-action approval."""
    return await _window_action(
        session_id,
        observation_id,
        window_id,
        ActionKind.CLOSE_WINDOW,
        approval_token=approval_token,
    )


@mcp.tool()
async def read_clipboard(
    session_id: str,
    observation_id: str,
    maximum_characters: int = 10_000,
) -> dict[str, Any]:
    """Read bounded text from the clipboard when host and session access are enabled."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.READ_CLIPBOARD,
        arguments={"maximum_characters": maximum_characters},
    )


@mcp.tool()
async def write_clipboard(
    session_id: str,
    observation_id: str,
    text: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Write clipboard text after an exact-action approval."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.WRITE_CLIPBOARD,
        arguments={"text": text},
        approval_token=approval_token,
    )


@mcp.tool()
async def launch_application(
    session_id: str,
    observation_id: str,
    application_id: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Launch an allowlisted bundle, executable, or AppUserModel ID after approval."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.LAUNCH_APPLICATION,
        arguments={"application_id": application_id},
        approval_token=approval_token,
    )


@mcp.tool()
async def wait(session_id: str, seconds: float) -> dict[str, Any]:
    """Wait up to ten seconds for an application or animation to settle."""
    return await _execute_action(
        session_id=session_id,
        observation_id=None,
        kind=ActionKind.WAIT,
        arguments={"seconds": seconds},
    )


@mcp.tool()
async def application_command(
    session_id: str,
    observation_id: str,
    adapter_id: str,
    command: str,
    arguments: dict[str, Any],
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Run an installed adapter command; first call returns an exact-action approval request."""
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={"adapter_id": adapter_id, "command": command, "arguments": arguments},
        approval_token=approval_token,
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


@mcp.tool()
async def adapters_list() -> dict[str, Any]:
    """List installed application adapters and their bounded command contracts."""
    return await client().call("adapters_list")


@mcp.tool()
async def adapter_describe(adapter_id: str) -> dict[str, Any]:
    """Describe one installed adapter without exposing its local package path."""
    response = await client().call("adapters_list")
    adapter = next(
        (item for item in response.get("adapters", []) if item.get("adapter_id") == adapter_id),
        None,
    )
    if not isinstance(adapter, dict):
        raise ValueError("adapter is not installed")
    return dict(adapter)


@mcp.tool()
async def approval_status(approval_id: str) -> dict[str, Any]:
    """Check whether a human approved or rejected a pending exact action."""
    return await client().call("approval_status", {"approval_id": approval_id})


@mcp.tool()
async def audit_query(session_id: str, limit: int = 20) -> dict[str, Any]:
    """Return bounded redacted action summaries for one session."""
    return await client().call("audit_query", {"session_id": session_id, "limit": limit})


@mcp.tool()
async def session_pause(session_id: str) -> dict[str, Any]:
    """Pause a session and release any held desktop input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "paused"},
    )


@mcp.tool()
async def session_resume(session_id: str) -> dict[str, Any]:
    """Resume a paused session without changing its original permissions or budgets."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "active"},
    )


@mcp.tool()
async def session_stop(session_id: str) -> dict[str, Any]:
    """Stop a desktop-control session permanently and release input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "stopped"},
    )


async def _execute_action(
    session_id: str,
    observation_id: str | None,
    kind: ActionKind,
    arguments: dict[str, Any],
    target: Target | None = None,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recovery: RecoveryOptions | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        kind=kind,
        target=target,
        arguments=arguments,
        recovery=recovery or RecoveryOptions(),
        approval_token=approval_token,
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


async def _window_action(
    session_id: str,
    observation_id: str,
    window_id: str,
    kind: ActionKind,
    arguments: dict[str, Any] | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=kind,
        arguments={"window_id": window_id, **(arguments or {})},
        expected_window_id=window_id,
        approval_token=approval_token,
    )


def _condition_matches(
    observation: dict[str, Any],
    condition_type: str,
    value: str,
    role: str | None,
) -> bool:
    if condition_type == "application_active":
        return observation.get("active_application_id") == value
    if condition_type == "window_focused":
        return observation.get("focused_window_id") == value or any(
            window.get("focused") and value.casefold() in str(window.get("title", "")).casefold()
            for window in observation.get("windows", [])
        )
    matching_element = any(
        value.casefold() in str(element.get("name", "")).casefold()
        and (role is None or str(element.get("role", "")).casefold() == role.casefold())
        for element in observation.get("elements", [])
    )
    if condition_type == "element_present":
        return matching_element
    if condition_type == "element_absent":
        return not matching_element
    raise ValueError(f"unknown condition type: {condition_type}")


def main() -> None:
    mcp.run(transport="stdio")
