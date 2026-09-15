# NimbleDesk

NimbleDesk is a local, model-agnostic runtime for AI agents that understand and operate desktop applications. The product architecture covers native macOS, Windows, and Linux automation, semantic UI inspection, media intelligence, creative planning, application adapters, approvals, and auditable execution.

The current implementation establishes the secure runtime foundation:

- Strict, versioned protocol models and target types.
- Separate privileged daemon and unprivileged MCP gateway.
- HMAC-authenticated, replay-protected loopback RPC.
- Bounded sessions with host and session input gates.
- Fresh-observation and active-window preconditions.
- Exact-action, expiring, single-use approvals.
- Hash-chained audit events with sensitive argument redaction.
- Deterministic simulator backend.
- Explicitly selected portable PyAutoGUI fallback for screenshots and input.

See [PLAN.md](PLAN.md) for the full end-to-end architecture and implementation gates.

## Development

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev mypy
uv run --extra dev ruff check .
```

See [docs/TESTING.md](docs/TESTING.md) for simulator, real-screen, bounded pointer, OS contract, editor, and creative-quality testing.

## Run safely with the simulator

The daemon uses the simulator by default. It cannot control the real desktop.

```bash
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime uv run nimbledesk-daemon
```

Configure an MCP client in a second process:

```json
{
  "mcpServers": {
    "nimbledesk": {
      "command": "/absolute/path/NimbleDesk/.venv/bin/nimbledesk-mcp",
      "env": {
        "NIMBLEDESK_CONNECTION_FILE": "/tmp/nimbledesk-runtime/connection.json"
      }
    }
  }
}
```

Implemented MCP tools cover health, session start/pause/resume/stop, observation, screenshots, mouse movement, clicks, dragging, scrolling, text entry, key presses, hotkeys, and bounded waits.

Observations are progressively disclosed: window results are capped, long labels are shortened, required safety fields are preserved, and every response reports its estimated text-token usage and truncation. Screenshots default to a bounded 1280×800 JPEG; callers can request a smaller image, a crop, different JPEG quality, or lossless PNG for text-heavy regions.

## Enable the portable desktop backend

Real desktop input is disabled unless the human starts the daemon with both the portable backend and host input authority:

```bash
NIMBLEDESK_BACKEND=portable \
NIMBLEDESK_ENABLE_INPUT=1 \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon
```

The MCP caller must also request an input-enabled session. A caller cannot override a disabled host gate.

PyAutoGUI's corner fail-safe is enabled. Moving the pointer to a screen corner interrupts automation. Pausing or stopping a session releases common modifier keys and all mouse buttons.

### macOS permissions

Grant the daemon's terminal or packaged application **Screen & System Audio Recording** and **Accessibility** in **System Settings → Privacy & Security**.

### Windows permissions

No additional permission is normally required. Windows prevents a normal process from controlling an administrator-elevated application.

### Linux permissions

The portable backend initially supports X11. Native PipeWire, RemoteDesktop portal, and AT-SPI providers are planned for Wayland.

## Security boundary

The MCP gateway has no backend imports and no direct desktop authority. The daemon validates all requests and binds only to `127.0.0.1`. Its generated connection file contains a per-run secret and is written with user-only permissions. The simulator remains the default backend so running development commands cannot accidentally move the pointer or type.
