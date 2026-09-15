# Application adapters

NimbleDesk adapters expose documented application APIs without adding editor-specific code to the MCP gateway. Each adapter is an installed Python module plus a JSON manifest placed in `~/.nimbledesk/adapters` or the directory named by `NIMBLEDESK_ADAPTER_DIR`.

```json
{
  "adapter_id": "example.video-editor",
  "version": "1.0.0",
  "vendor": "Example",
  "entrypoint": "example_editor.nimbledesk:handle",
  "package_path": ".",
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

`package_path` is required for external adapters. Relative paths resolve from the manifest
directory. The worker adds exactly that directory to its import path, and platform sandboxes grant
it read-only access. This also makes external source packages importable from the standalone
NimbleDesk executable without exposing unrelated Python or user directories.

The entrypoint receives `command: str` and `arguments: dict` and returns a JSON-compatible dictionary. It runs in a separate worker process with a minimal environment, no user-site packages, a temporary working directory, a deadline, cancellation, one-megabyte stdout, and 64-kilobyte stderr limits enforced while the process runs. Duplicate JSON keys, unknown fields, malformed output, oversized output, crashes, and hangs fail the action without crashing the daemon. Declare every file argument in `path_arguments`; the runtime rejects symbolic-link components and paths outside the session's canonical `granted_paths` before starting the worker.

Sandboxed Windows workers also run with maximum token privileges removed inside a one-process,
512 MiB Job Object configured to terminate the worker when the job handle closes. This contains
crashes, child-process attempts, resource exhaustion, and daemon cancellation. Windows
AppContainer path and network brokering remains a release qualification gate; do not install an
unreviewed Windows adapter until that gate is complete.

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

The worker boundary contains crashes, hangs, environment leakage, and accidental undeclared path arguments. Manifests default to `"isolation": "trusted"` for built-in or reviewed packages. Third-party manifests can opt into `"isolation": "sandboxed"`; NimbleDesk then fails closed unless `sandbox-exec` is available on macOS or Bubblewrap is available on Linux. Linux workers receive a new filesystem, process, IPC, UTS, cgroup, and network namespace containing only runtime libraries, package code, scratch space, and explicit session grants. Sandboxed workers have no network by default. Set `"network_access": true` only when the adapter contract requires it, and list command path arguments that require writes in `"writable_path_arguments"`. Windows currently rejects sandboxed third-party adapters because an equivalent restricted-token worker is not yet implemented. Keep unreviewed packages disabled on Windows.
