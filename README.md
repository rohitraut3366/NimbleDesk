# NimbleDesk

A local, cross-platform platform that lets a tool-capable AI understand and operate desktop applications. The current scaffold exposes screen capture, cursor position, mouse movement and clicks, scrolling, typing, key presses, and hotkeys through MCP. The final architecture is documented in `PLAN.md`.

Supported platforms:

- macOS
- Windows 10 and 11
- Linux desktops using X11

Wayland intentionally restricts global screen and input access. Some Wayland environments can run the server through XWayland, but full native Wayland support will require a portal or compositor-specific backend.

The server starts with input disabled. Screenshots and screen metadata still work, but mouse and keyboard actions require `LAPTOP_CONTROL_ENABLE_INPUT=1`. PyAutoGUI's corner fail-safe is also enabled: moving the pointer to a screen corner stops automation.

## Install

Python 3.11 or newer is required.

```bash
cd laptop-control-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

### macOS permissions

Grant the terminal or AI client these permissions in **System Settings → Privacy & Security**:

- **Screen & System Audio Recording** for screenshots
- **Accessibility** for mouse and keyboard control

Restart the client after changing permissions.

### Windows permissions

No additional permission is normally required. Run the AI client at the same privilege level as the apps it controls. Windows prevents a normal process from controlling an administrator-elevated app.

### Linux setup

Use an X11 desktop session and install the screenshot helper supplied by your distribution. For example:

```bash
# Ubuntu or Debian
sudo apt install python3-tk python3-dev scrot
```

Check `echo $XDG_SESSION_TYPE`; `x11` is supported by this first version. On Wayland, switch to an X11 login session until a native backend is added.

## Connect an AI client

Add a local MCP server to the client's configuration. Use the full paths on your machine:

```json
{
  "mcpServers": {
    "laptop-control": {
      "command": "/absolute/path/laptop-control-mcp/.venv/bin/laptop-control-mcp",
      "env": {
        "LAPTOP_CONTROL_ENABLE_INPUT": "1"
      }
    }
  }
}
```

Leave out the `env` entry for a read-only setup that can only inspect the display.

An effective model instruction is:

> Use the laptop-control tools to complete the task. Take a screenshot before each action, use screen coordinates from the latest screenshot, make one state-changing action at a time, then take another screenshot to verify the result. Ask me before submitting forms, sending messages, purchasing, deleting data, or entering credentials.

## Available tools

| Tool | Purpose |
| --- | --- |
| `platform_info` | Report the operating system, input mode, and Linux session type |
| `take_screenshot` | Capture the full display or a cropped region |
| `screen_size` | Get display dimensions |
| `cursor_position` | Get the current pointer position |
| `move_mouse` | Move to an absolute coordinate |
| `click` | Left, middle, or right click |
| `scroll` | Scroll up or down |
| `type_text` | Type into the focused control |
| `press_key` | Press a named key one or more times |
| `hotkey` | Press a key combination |
| `wait` | Let the interface settle before inspecting it again |

## Safety boundaries

- Input is opt-in through an environment variable.
- Coordinates and action sizes are bounded and validated.
- Every PyAutoGUI action pauses briefly.
- Moving the pointer to any corner triggers PyAutoGUI's emergency stop.
- The server does not expose a shell, filesystem, clipboard, or network access.

Run the tests with:

```bash
.venv/bin/pytest
```
