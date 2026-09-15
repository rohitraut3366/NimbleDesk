from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from nimbledesk.client import DaemonClient
from nimbledesk.protocol.models import ActionKind, ActionRequest, CoordinateTarget, Point


async def run_smoke_test(connection_file: Path, test_input: bool) -> dict[str, Any]:
    client = DaemonClient.from_file(connection_file)
    health = await client.call("health")
    session = await client.call(
        "session_start",
        {
            "reason": "NimbleDesk local smoke test",
            "config": {"input_enabled": test_input, "max_actions": 5},
        },
    )
    session_id = str(session["session_id"])
    pointer_test: dict[str, Any] = {"requested": test_input, "completed": False}
    try:
        observation = await client.call("desktop_observe", {"session_id": session_id})
        capture = await client.call(
            "screen_capture",
            {
                "session_id": session_id,
                "observation_id": observation["observation_id"],
                "options": {
                    "image_format": "jpeg",
                    "max_width": 640,
                    "max_height": 400,
                    "jpeg_quality": 60,
                },
            },
        )
        validate_capture(capture)
        if test_input:
            pointer_test = await _move_and_restore_pointer(client, session_id, observation)
        return {
            "health": health["status"],
            "platform": observation["platform"],
            "capabilities": observation["capabilities"],
            "display_count": len(observation["displays"]),
            "capture": {
                "mime_type": capture["mime_type"],
                "width": capture["width"],
                "height": capture["height"],
                "encoded_bytes": len(base64.b64decode(capture["data_base64"])),
                "sha256": capture["sha256"],
            },
            "pointer_test": pointer_test,
        }
    finally:
        await client.call(
            "session_set_state",
            {"session_id": session_id, "state": "stopped"},
        )


def validate_capture(capture: dict[str, Any]) -> None:
    image_bytes = base64.b64decode(capture["data_base64"], validate=True)
    if not image_bytes:
        raise ValueError("screen capture is empty")
    digest = hashlib.sha256(image_bytes).hexdigest()
    if digest != capture["sha256"]:
        raise ValueError("screen capture hash does not match its payload")
    if int(capture["width"]) > 640 or int(capture["height"]) > 400:
        raise ValueError("screen capture exceeded the smoke-test image budget")


async def _move_and_restore_pointer(
    client: DaemonClient,
    session_id: str,
    observation: dict[str, Any],
) -> dict[str, Any]:
    original = Point.model_validate(observation["cursor"])
    bounds = observation["displays"][0]["logical_bounds"]
    target = bounded_test_target(original, int(bounds["width"]), int(bounds["height"]))
    moved = await _move_pointer(client, session_id, observation, target)
    if moved["status"] != "completed":
        raise RuntimeError(f"pointer move failed: {moved['message']}")
    moved_observation = await client.call("desktop_observe", {"session_id": session_id})
    observed_cursor = Point.model_validate(moved_observation["cursor"])
    restored = await _move_pointer(client, session_id, moved_observation, original)
    if restored["status"] != "completed":
        raise RuntimeError(f"pointer restore failed: {restored['message']}")
    restored_observation = await client.call("desktop_observe", {"session_id": session_id})
    observed_after_restore = Point.model_validate(restored_observation["cursor"])
    if not points_are_close(observed_cursor, target):
        raise RuntimeError(
            "pointer action returned success but the cursor did not move; "
            "grant Accessibility permission to the daemon process"
        )
    if not points_are_close(observed_after_restore, original):
        raise RuntimeError("pointer did not return to its original position")
    return {
        "requested": True,
        "completed": True,
        "original": original.model_dump(),
        "target": target.model_dump(),
        "observed_after_move": observed_cursor.model_dump(),
        "restored": True,
    }


async def _move_pointer(
    client: DaemonClient,
    session_id: str,
    observation: dict[str, Any],
    point: Point,
) -> dict[str, Any]:
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation["observation_id"],
        expected_application_id=observation.get("active_application_id"),
        expected_window_id=observation.get("focused_window_id"),
        kind=ActionKind.MOVE_POINTER,
        target=CoordinateTarget(point=point),
        arguments={"duration": 0.1},
    )
    return await client.call("action_execute", {"action": action.model_dump(mode="json")})


def bounded_test_target(original: Point, width: int, height: int) -> Point:
    if width < 2 or height < 2:
        raise ValueError("display is too small for a pointer smoke test")
    target_x = original.x + 10 if original.x + 10 < width else max(0, original.x - 10)
    target_y = original.y + 10 if original.y + 10 < height else max(0, original.y - 10)
    return Point(x=target_x, y=target_y)


def points_are_close(first: Point, second: Point, tolerance: int = 2) -> bool:
    return abs(first.x - second.x) <= tolerance and abs(first.y - second.y) <= tolerance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test a running NimbleDesk daemon")
    parser.add_argument("--connection-file", required=True, type=Path)
    parser.add_argument(
        "--test-input",
        action="store_true",
        help="Move the pointer by 10 pixels and restore it; host input must already be enabled",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    result = asyncio.run(run_smoke_test(arguments.connection_file, arguments.test_input))
    print(json.dumps(result, indent=2, sort_keys=True))
