from __future__ import annotations

import importlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nimbledesk.adapters.models import AdapterInvocation, AdapterResult

Handler = Callable[[str, dict[str, Any]], dict[str, Any]]


def main() -> None:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        raise SystemExit("adapter worker requires module:function entrypoint")
    module_name, function_name = sys.argv[1].split(":", 1)
    package_path = os.getenv("NIMBLEDESK_ADAPTER_PACKAGE")
    if package_path:
        sys.path.insert(0, package_path)
    request_path = os.getenv("NIMBLEDESK_ADAPTER_REQUEST")
    payload = (
        Path(request_path).read_bytes()
        if request_path
        else sys.stdin.buffer.read(1_000_001)
    )
    if len(payload) > 1_000_000:
        raise SystemExit("adapter request exceeded the one-megabyte limit")
    invocation = AdapterInvocation.model_validate_json(payload)
    module = importlib.import_module(module_name)
    handler: Handler = getattr(module, function_name)
    try:
        value = handler(invocation.command, invocation.arguments)
        response = AdapterResult(success=True, result=value)
    except Exception as error:
        response = AdapterResult(success=False, error=str(error))
    sys.stdout.write(json.dumps(response.model_dump(mode="json"), separators=(",", ":")))


if __name__ == "__main__":
    main()
