from __future__ import annotations

from time import sleep
from typing import Any


def handle(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if command == "sleep":
        sleep(float(arguments["seconds"]))
        return {"slept": arguments["seconds"]}
    if command == "huge":
        return {"content": "x" * 1_100_000}
    if command != "inspect":
        raise ValueError("unsupported fixture command")
    return {"received": arguments}
