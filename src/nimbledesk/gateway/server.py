from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from nimbledesk.client import DaemonClient
from nimbledesk.gateway.budget import compact_observation
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
                "allowed_applications": allowed_applications or [],
                "granted_paths": granted_paths or [],
            },
        },
    )


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
async def approval_status(approval_id: str) -> dict[str, Any]:
    """Check whether a human approved or rejected a pending application command."""
    return await client().call("approval_status", {"approval_id": approval_id})


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
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


def main() -> None:
    mcp.run(transport="stdio")
