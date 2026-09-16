from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

from nimbledesk.protocol.models import ActionRequest, ActionResult


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = Lock()
        integrity = _integrity(path)
        if not integrity["valid"]:
            invalid_line = integrity.get("invalid_line", "unknown")
            raise RuntimeError(f"audit log integrity failed at line {invalid_line}")
        self._previous_hash = str(integrity.get("head_hash", "0" * 64))

    def record(self, request: ActionRequest, result: ActionResult) -> None:
        if self._path is None:
            return
        with self._lock:
            payload = {
                "previous_hash": self._previous_hash,
                "request": _redact(request.model_dump(mode="json")),
                "result": _redact(result.model_dump(mode="json")),
            }
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            entry_hash = hashlib.sha256(serialized.encode()).hexdigest()
            record = json.dumps({**payload, "entry_hash": entry_hash}, sort_keys=True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as output:
                output.write(record + "\n")
            self._previous_hash = entry_hash

    def summaries(self, session_id: str, limit: int) -> tuple[dict[str, Any], ...]:
        if self._path is None or not self._path.is_file():
            return ()
        matches: deque[dict[str, Any]] = deque(maxlen=limit)
        with self._lock, self._path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    record = json.loads(line)
                    request = record["request"]
                    result = record["result"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                if request.get("session_id") != session_id:
                    continue
                matches.append(
                    {
                        "action_id": request.get("action_id"),
                        "kind": request.get("kind"),
                        "status": result.get("status"),
                        "message": str(result.get("message", ""))[:300],
                        "started_at": result.get("started_at"),
                        "finished_at": result.get("finished_at"),
                        "entry_hash": record.get("entry_hash"),
                    }
                )
        return tuple(matches)

    def integrity(self) -> dict[str, object]:
        with self._lock:
            return _integrity(self._path)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key in {"approval_token", "text"} else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _integrity(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {"valid": True, "entries": 0, "head_hash": "0" * 64}
    previous_hash = "0" * 64
    entries = 0
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    record = json.loads(line)
                    entry_hash = str(record.pop("entry_hash"))
                    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"))
                except (json.JSONDecodeError, KeyError, TypeError):
                    return {"valid": False, "entries": entries, "invalid_line": line_number}
                expected_hash = hashlib.sha256(serialized.encode()).hexdigest()
                if record.get("previous_hash") != previous_hash or entry_hash != expected_hash:
                    return {"valid": False, "entries": entries, "invalid_line": line_number}
                previous_hash = entry_hash
                entries += 1
    except OSError:
        return {"valid": False, "entries": entries, "invalid_line": entries + 1}
    return {"valid": True, "entries": entries, "head_hash": previous_hash}
