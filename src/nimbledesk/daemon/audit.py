from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any

from nimbledesk.protocol.models import ActionRequest, ActionResult


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._previous_hash = "0" * 64
        self._lock = Lock()

    def record(self, request: ActionRequest, result: ActionResult) -> None:
        if self._path is None:
            return
        payload = {
            "previous_hash": self._previous_hash,
            "request": _redact(request.model_dump(mode="json")),
            "result": _redact(result.model_dump(mode="json")),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        entry_hash = hashlib.sha256(serialized.encode()).hexdigest()
        record = json.dumps({**payload, "entry_hash": entry_hash}, sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as output:
                output.write(record + "\n")
            self._previous_hash = entry_hash


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key in {"approval_token", "text"} else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
