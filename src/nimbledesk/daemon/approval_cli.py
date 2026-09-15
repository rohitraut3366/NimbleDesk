from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from nimbledesk.client import DaemonClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approve one exact pending NimbleDesk application action"
    )
    parser.add_argument("approval_id")
    parser.add_argument("--connection-file", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    connection_file = arguments.connection_file or (
        Path(configured) if configured else Path.home() / ".nimbledesk/runtime/connection.json"
    )
    result = asyncio.run(
        DaemonClient.from_file(connection_file).call(
            "approval_approve", {"approval_id": arguments.approval_id}
        )
    )
    print(json.dumps(result, indent=2))
