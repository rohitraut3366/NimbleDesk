from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from nimbledesk.client import DaemonClient
from nimbledesk.protocol.models import ActionKind, ActionRequest, CoordinateTarget, Point

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
) -> dict[str, Any]:
    """Start a bounded desktop-control session for a clear user-provided reason."""
    return await client().call(
        "session_start",
        {
            "reason": reason,
            "config": {
                "input_enabled": input_enabled,
                "allowed_applications": allowed_applications or [],
            },
        },
    )


@mcp.tool()
async def desktop_observe(session_id: str) -> dict[str, Any]:
    """Observe displays, cursor, focused application, windows, and capabilities."""
    return await client().call("desktop_observe", {"session_id": session_id})


@mcp.tool()
async def take_screenshot(
    session_id: str,
    observation_id: str,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
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
    capture = await client().call(
        "screen_capture",
        {"session_id": session_id, "observation_id": observation_id, "region": region},
    )
    return Image(data=base64.b64decode(capture["data_base64"]), format="png")


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
    target: CoordinateTarget | None = None,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        kind=kind,
        target=target,
        arguments=arguments,
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


def main() -> None:
    mcp.run(transport="stdio")
