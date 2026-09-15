from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from typing import Any

from nimbledesk.adapters.models import AdapterInvocation, AdapterResult

Handler = Callable[[str, dict[str, Any]], dict[str, Any]]


def main() -> None:
    if len(sys.argv) != 2 or ":" not in sys.argv[1]:
        raise SystemExit("adapter worker requires module:function entrypoint")
    module_name, function_name = sys.argv[1].split(":", 1)
    invocation = AdapterInvocation.model_validate_json(sys.stdin.buffer.read(1_000_001))
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
