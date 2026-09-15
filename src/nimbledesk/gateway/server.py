from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

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
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Click a coordinate from the latest observation after stale-state validation."""
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=x, y=y)),
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


@mcp.tool()
async def session_pause(session_id: str) -> dict[str, Any]:
    """Pause a session and release any held desktop input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "paused"},
    )


@mcp.tool()
async def session_stop(session_id: str) -> dict[str, Any]:
    """Stop a desktop-control session permanently and release input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "stopped"},
    )


def main() -> None:
    mcp.run(transport="stdio")
