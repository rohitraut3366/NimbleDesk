# NimbleDesk

NimbleDesk is a local, model-agnostic runtime for AI models that need to see and operate desktop applications. It also includes command-line workflows for finding highlights in long videos and selecting, correcting, and arranging photos.

The current release runs on macOS, Windows, and Linux X11 through a portable PyAutoGUI backend. It separates the model-facing MCP server from the privileged desktop daemon, requires explicit input authority, rejects actions based on stale observations, and records attempted actions in a redacted audit log.

## What works today

- Desktop observation and bounded screenshots.
- Mouse movement, clicks, dragging, scrolling, text entry, key presses, and hotkeys.
- Bounded sessions with pause, resume, stop, application allowlists, action limits, and expiry.
- An authenticated, replay-protected loopback connection between the MCP server and daemon.
- A deterministic simulator that exercises the complete control flow without touching the desktop.
- Generic video highlight detection from motion and audio peaks, plus optional supplied timeline events.
- Rendered, ranked MP4 clips with FFprobe validation and a JSON manifest.
- Recursive photo discovery, perceptual duplicate removal, technical ranking, light correction, a contact sheet, and an optional MP4 slideshow.
- Token-budgeted observations and size-bounded screenshots.

NimbleDesk does not yet infer game-specific kills or clutches from pixels alone, transcribe speech, choose music, generate a complete creative edit plan, control an editor through a native adapter, or provide signed installers and a production UI. Those systems are specified in [PLAN.md](PLAN.md), but commands for them are not present in this release.

## Requirements

- macOS, Windows, or Linux. The portable Linux backend currently requires X11.
- Python 3.11 or newer. Development checks target Python 3.12.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [FFmpeg](https://ffmpeg.org/download.html), including `ffprobe`, for video highlights and photo slideshows.
- Git if installing from source.

Confirm the tools are available:

```bash
python3 --version
uv --version
ffmpeg -version
ffprobe -version
```

Common FFmpeg installation commands are `brew install ffmpeg` on macOS, `winget install Gyan.FFmpeg` on Windows, and `sudo apt install ffmpeg` on Ubuntu or Debian.

## Install

Clone the repository and create its managed virtual environment:

```bash
git clone https://github.com/rohitraut3366/NimbleDesk.git
cd NimbleDesk
uv sync
```

All examples below run from the repository root. `uv run` automatically uses the project environment. To install the development tools as well:

```bash
uv sync --extra dev
```

## Quick start with the simulator

The simulator is the safest way to connect an AI client and exercise every input tool. It returns a synthetic desktop and never moves the real pointer or types on the computer.

Start the daemon in terminal 1:

```bash
NIMBLEDESK_BACKEND=simulator \
NIMBLEDESK_ENABLE_INPUT=1 \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon
```

On Windows PowerShell, set the same values like this:

```powershell
$env:NIMBLEDESK_BACKEND = "simulator"
$env:NIMBLEDESK_ENABLE_INPUT = "1"
$env:NIMBLEDESK_RUNTIME_DIR = "$env:TEMP\nimbledesk-runtime"
uv run nimbledesk-daemon
```

The daemon stays in the foreground. It creates:

- `connection.json`: the port and per-run authentication secret; it is replaced on each start.
- `audit.jsonl`: one redacted, hash-chained JSON record per attempted action.

In terminal 2, test the installed process boundary:

```bash
uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json
```

Stop the daemon with `Ctrl+C` after testing.

## Connect an MCP client

First start a daemon using the simulator or one of the real-desktop modes below. Then add NimbleDesk to any client that supports local stdio MCP servers:

```json
{
  "mcpServers": {
    "nimbledesk": {
      "command": "/absolute/path/to/NimbleDesk/.venv/bin/nimbledesk-mcp",
      "env": {
        "NIMBLEDESK_CONNECTION_FILE": "/tmp/nimbledesk-runtime/connection.json"
      }
    }
  }
}
```

On Windows, the command normally ends in `.venv\\Scripts\\nimbledesk-mcp.exe`, and the connection file should match the PowerShell runtime directory. Restart the MCP client after changing its configuration. The daemon must already be running whenever the client launches or invokes NimbleDesk.

If `NIMBLEDESK_CONNECTION_FILE` is omitted, the MCP server reads `~/.nimbledesk/runtime/connection.json`, which is also the daemon's default location.

### Recommended model workflow

1. Call `health`.
2. Call `session_start` with a clear reason. Set `input_enabled` to `true` only when input is intended.
3. Call `desktop_observe` and retain its `observation_id`, active application, focused window, and display bounds.
4. Call `take_screenshot` with that observation. Prefer a crop when the relevant region is known.
5. Perform one bounded action using the latest observation ID. Include the expected application and window IDs when available.
6. Observe again after each action that can change the interface. Do not reuse an old observation.
7. Call `session_pause` if human intervention is needed, and `session_stop` when finished.

### MCP tool reference

| Tool | Required arguments | Optional arguments and behavior |
| --- | --- | --- |
| `health` | None | Checks daemon availability. |
| `session_start` | `reason` | `input_enabled=false`; `allowed_applications=[]`. Sessions default to 1,000 actions and one hour. |
| `desktop_observe` | `session_id` | `max_estimated_text_tokens=2000` (128–100,000); `max_windows=10` (0–200). |
| `take_screenshot` | `session_id`, `observation_id` | Crop with all of `left`, `top`, `width`, `height`; `image_format=jpeg`; `max_width=1280`; `max_height=800`; `jpeg_quality=75`. |
| `move_mouse` | `session_id`, `observation_id`, `x`, `y` | `duration=0.2`; expected application/window IDs. |
| `click` | `session_id`, `observation_id`, `x`, `y` | `button=left`; `clicks=1`; `interval=0.1`; expected application/window IDs. |
| `drag_to` | `session_id`, `observation_id`, `x`, `y` | Drags from the current pointer; `duration=0.5`; `button=left`; expected application/window IDs. |
| `scroll` | `session_id`, `observation_id`, `amount` | Positive scrolls up and negative scrolls down; expected application/window IDs. |
| `type_text` | `session_id`, `observation_id`, `text` | `interval=0.02`; expected application/window IDs. Text is redacted from the audit record. |
| `press_key` | `session_id`, `observation_id`, `key` | `presses=1`; `interval=0.1`; accepts PyAutoGUI names such as `enter`, `tab`, `escape`, `backspace`, and `f5`. |
| `hotkey` | `session_id`, `observation_id`, `keys` | Example: `["command", "s"]` on macOS or `["ctrl", "s"]` elsewhere. |
| `wait` | `session_id`, `seconds` | Waits for an interface or animation to settle; maximum 10 seconds per call. |
| `session_pause` | `session_id` | Pauses input and releases common modifier keys and mouse buttons. |
| `session_resume` | `session_id` | Restores the original session limits; a stopped session cannot resume. |
| `session_stop` | `session_id` | Permanently stops the session and releases input. |

Coordinates are logical desktop coordinates from `desktop_observe`. The daemon verifies the observation is fresh and, when supplied, that the active application and focused window still match. PyAutoGUI's corner fail-safe remains enabled: move the pointer to a screen corner to interrupt portable automation.

## Use the real desktop

### Read-only screen access

This mode exposes observations and screenshots while the host gate rejects mouse and keyboard actions:

```bash
NIMBLEDESK_BACKEND=portable \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon
```

Run the smoke test without `--test-input` to verify capture safely:

```bash
uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json
```

### Opt-in mouse and keyboard access

Input requires two independent grants: the human must set the daemon's host flag, and the MCP caller must request an input-enabled session.

```bash
NIMBLEDESK_BACKEND=portable \
NIMBLEDESK_ENABLE_INPUT=1 \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon
```

The model then calls `session_start` with `input_enabled=true`. If either gate is false, input actions are rejected.

The optional smoke test moves the pointer by 10 logical pixels and restores it. It never clicks or types:

```bash
uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json \
  --test-input
```

Run real input tests only on a dedicated test desktop with no sensitive or destructive dialog focused.

### Operating-system permissions

**macOS:** Grant the terminal or packaged process **Screen & System Audio Recording** for screenshots and **Accessibility** for mouse and keyboard control in **System Settings → Privacy & Security**. Restart the daemon after changing permissions.

**Windows:** A normal process can control applications running at the same integrity level. It cannot control an application launched as administrator; run both at the same level. No additional permission is normally needed.

**Linux:** The portable backend supports X11. Make sure the process has access to `DISPLAY`. Native Wayland support through PipeWire, the RemoteDesktop portal, and AT-SPI is planned and is not implemented in this release.

## Generate highlight clips

The highlight command samples motion and audio energy, combines optional timeline events, ranks well-separated peaks, adds context before and after each peak, renders H.264/AAC MP4 clips, validates each clip with FFprobe, and writes an editable manifest.

```bash
uv run nimbledesk-highlights gameplay.mp4 output/highlights \
  --count 10 \
  --lead-in 8 \
  --aftermath 12 \
  --minimum-separation 20
```

For a two- or three-hour source, analysis time depends on video resolution, codec, storage speed, and CPU. The source file is only read and is never modified.

| Option | Default | Meaning |
| --- | --- | --- |
| `SOURCE` | Required | Input video file. |
| `OUTPUT_DIRECTORY` | Required | Directory for clips and the manifest; it is created if needed. |
| `--count` | `10` | Number of ranked clips, from 1 to 100. Fewer can be returned when peaks cannot satisfy separation. |
| `--lead-in` | `8` | Seconds retained before the peak, from 0 to 60. |
| `--aftermath` | `12` | Seconds retained after the peak, from 0 to 60. |
| `--minimum-separation` | `20` | Minimum seconds between selected peaks, from 1 to 300. |
| `--output-width` | Source width | Resize clips to this width, from 320 to 7,680, while preserving aspect ratio. |
| `--events` | None | Path to an optional JSON list of timestamped events. |

An events file can come from a game integration, replay parser, telemetry export, or manual markers:

```json
[
  {
    "time_seconds": 125.4,
    "event_type": "grenade_kill",
    "label": "Triple grenade kill",
    "importance": 1.0
  },
  {
    "time_seconds": 488.2,
    "event_type": "clutch",
    "label": "One versus three survival",
    "importance": 0.9
  }
]
```

Use it with:

```bash
uv run nimbledesk-highlights gameplay.mp4 output/highlights \
  --events events.json
```

`time_seconds` must be zero or greater and `importance` must be between 0 and 1. Unknown extra fields are rejected.

The output directory contains:

- `highlight_01_<label>_<timestamp>.mp4`, one validated clip for each ranked moment.
- `highlights.json`, containing source metadata, resolved configuration, ranked candidates, scores, reasons, event labels, and rendered-file metadata.

Without an events file, ranking is based on generic audiovisual activity. It can find energetic moments, but it cannot currently know that a pixel sequence is a kill, grenade kill, clutch, or survival event.

## Create from photos

The photo command recursively scans one image or a directory, applies EXIF orientation, detects perceptual duplicates, scores sharpness, exposure, contrast, and resolution, keeps visually diverse high-scoring images, and creates lightly corrected JPEG copies. Supported source formats are JPEG, PNG, WebP, TIFF, and TIF.

```bash
uv run nimbledesk-photos photos/ output/photos \
  --count 20 \
  --slideshow
```

| Option | Default | Meaning |
| --- | --- | --- |
| `SOURCE` | Required | One supported image or a directory scanned recursively. |
| `OUTPUT_DIRECTORY` | Required | Directory for generated assets and the manifest. |
| `--count` | `20` | Maximum selected photos, from 1 to 1,000. Duplicate and diversity filtering can return fewer. |
| `--slideshow` | Off | Render a silent H.264 MP4 with each selected image shown for three seconds. Requires FFmpeg. |
| `--slideshow-width` | `1920` | Slideshow width, from 320 to 7,680; output uses a 16:9 black-padded canvas. |

The source files remain unchanged. The output directory contains:

- `selected/selected_0001.jpg`, corrected JPEG copies ordered by score.
- `contact_sheet.jpg`, a labeled preview of all selected images.
- `slideshow.mp4` when `--slideshow` is set.
- `photos.json`, containing every source score and duplicate relationship plus the selected source/output mapping.

## Reduce model token usage

Desktop images dominate model cost, so observe and capture progressively:

1. Start with `desktop_observe(max_estimated_text_tokens=512, max_windows=5)`.
2. Use window bounds to crop `take_screenshot` to the relevant application or control.
3. Keep JPEG at the default quality for visual navigation and reduce `max_width`/`max_height` when text remains readable.
4. Use PNG only for small, text-heavy regions where JPEG artifacts prevent reading.
5. Observe again after an action instead of repeatedly capturing an unchanged screen.
6. Preserve application and window IDs in the model's working state; avoid resending entire earlier observations.

Observation responses include a `usage` object with `estimated_text_tokens`, `maximum_text_tokens`, and `truncated_fields`. Required safety fields are preserved even when window titles and lists are shortened. Screenshot limits are 64–4,096 pixels per dimension; JPEG quality accepts 20–95.

## Test and develop

Install development dependencies and run all deterministic checks:

```bash
uv sync --extra dev
uv run --extra dev ruff check .
uv run --extra dev mypy
uv run --extra dev pytest
```

These tests use the simulator and do not touch the real desktop. The installed-process smoke test is separate because it exercises the daemon, connection file, authenticated RPC, screenshot encoding/hash validation, and session shutdown:

```bash
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime uv run nimbledesk-daemon
```

Then, in another terminal:

```bash
uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json
```

See [docs/TESTING.md](docs/TESTING.md) for the complete simulator, OS backend, real-application, media-quality, and release test strategy.

## Configuration reference

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `NIMBLEDESK_BACKEND` | `simulator` | Select `simulator` or `portable`. Unknown values fail at startup. |
| `NIMBLEDESK_ENABLE_INPUT` | Disabled | Host input gate. Truthy values are `1`, `true`, `yes`, or `on`, ignoring case. |
| `NIMBLEDESK_RUNTIME_DIR` | `~/.nimbledesk/runtime` | Directory for `connection.json` and `audit.jsonl`. |
| `NIMBLEDESK_CONNECTION_FILE` | `~/.nimbledesk/runtime/connection.json` | Connection file read by `nimbledesk-mcp`. |

The daemon binds to `127.0.0.1` on an automatically selected port. `connection.json` contains a per-run secret and is written with user-only permissions. Do not share or commit the runtime directory.

## Troubleshooting

**The MCP client cannot connect:** Confirm `nimbledesk-daemon` is still running, the MCP configuration points to the current `connection.json`, and the client was restarted after configuration. A connection file from an earlier daemon run has an invalid secret.

**An input action says desktop input is disabled:** Start the daemon with `NIMBLEDESK_ENABLE_INPUT=1` and start the MCP session with `input_enabled=true`. Both are required.

**An action reports a stale observation:** Call `desktop_observe` again, use its new `observation_id`, and take a new screenshot before retrying.

**The wrong application or window is rejected:** Observe again and pass the current `active_application_id` and `focused_window_id`. Check that the application ID is included in `allowed_applications` if the session uses an allowlist.

**Screenshots are blank or fail on macOS:** Grant Screen & System Audio Recording permission to the exact terminal or process that runs the daemon, restart it, and retry.

**Pointer actions fail on macOS:** Grant Accessibility permission to the daemon process and restart it. A successful screenshot permission does not grant input permission.

**Linux cannot open a display:** Use an X11 session and confirm `DISPLAY` is available to the daemon. The portable backend does not currently control native Wayland sessions.

**Highlight or slideshow rendering cannot find FFmpeg:** Install both `ffmpeg` and `ffprobe`, confirm they are on `PATH`, and rerun the command.

**No photos are found:** Confirm the source exists and uses `.jpg`, `.jpeg`, `.png`, `.webp`, `.tif`, or `.tiff`.

## Architecture and security

The unprivileged MCP gateway has no backend imports and no direct desktop authority. Every call crosses an HMAC-authenticated, replay-protected loopback RPC boundary. The daemon validates strict protocol models, session state, host and session input gates, action budgets, observation freshness, application/window preconditions, and coordinate bounds. Pausing or stopping releases common modifiers and all mouse buttons.

The audit file redacts typed text and links entries with hashes so tampering is detectable. The model never receives a shell through the desktop protocol. Review the [threat model](docs/THREAT_MODEL.md) and the process-boundary and protocol decisions in [docs/adr](docs/adr).

The full target architecture, including native OS providers, semantic accessibility, game/domain packs, transcription, creative planning, music selection, editor adapters, recovery, evaluation, packaging, and staged release gates, is in [PLAN.md](PLAN.md).
