# Application adapters

NimbleDesk adapters expose documented application APIs without adding editor-specific code to the MCP gateway. Each adapter is an installed Python module plus a JSON manifest placed in `~/.nimbledesk/adapters` or the directory named by `NIMBLEDESK_ADAPTER_DIR`.

```json
{
  "adapter_id": "example.video-editor",
  "version": "1.0.0",
  "vendor": "Example",
  "entrypoint": "example_editor.nimbledesk:handle",
  "supported_platforms": ["Darwin", "Windows", "Linux"],
  "commands": {
    "render": {
      "risk": "high",
      "read_only": false,
      "reversible": false,
      "timeout_seconds": 3600,
      "required_arguments": ["project", "output"],
      "path_arguments": ["project", "output"]
    }
  }
}
```

The entrypoint receives `command: str` and `arguments: dict` and returns a JSON-compatible dictionary. It runs in a separate worker process with a minimal environment, no user-site packages, a temporary working directory, a deadline, cancellation, one-megabyte stdout, and 64-kilobyte stderr limits enforced while the process runs. Duplicate JSON keys, unknown fields, malformed output, oversized output, crashes, and hangs fail the action without crashing the daemon. Declare every file argument in `path_arguments`; the runtime rejects symbolic-link components and paths outside the session's canonical `granted_paths` before starting the worker.

An MCP client calls `application_command`. The first call returns `confirmation_required` and an `approval_id`. The approval operation is deliberately absent from the model-facing MCP tools. Review the adapter, command, complete arguments, and expiry in NimbleDesk Studio, then select **Approve exact action** or **Reject**. A terminal fallback is available:

```bash
uv run nimbledesk-approve APPROVAL_ID
```

The client polls `approval_status`. An approved response contains the token; it then repeats the byte-equivalent command with `approval_token`. Changed arguments, expired approvals, and token reuse are rejected. Identical requests reuse one pending queue item, and a successfully used token changes to `consumed` so clients stop retrying it.

Use this handler shape:

```python
from typing import Any


def handle(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if command == "render":
        return render_project(arguments["project"], arguments["output"])
    raise ValueError(f"unsupported command: {command}")
```

The worker boundary contains crashes, hangs, environment leakage, and accidental undeclared path arguments. It is not an operating-system filesystem sandbox: third-party adapter packages must remain disabled until reviewed and explicitly installed. Signed package provenance and OS sandbox profiles remain distribution requirements.
