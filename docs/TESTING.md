# Testing NimbleDesk

NimbleDesk needs evidence at five levels. Passing unit tests does not prove that an operating system granted permissions, that coordinates map correctly, that an editor completed a command, or that an automatically selected clip is compelling.

## 1. Fast deterministic suite

Run on every change:

```bash
uv run --extra dev pytest
uv run --extra dev mypy
uv run --extra dev ruff check .
```

The simulator verifies protocol validation, session state, action budgets, stale observations, approval binding, audit records, RPC authentication/replay prevention, response budgets, and gateway boundaries without touching the real desktop.

Cancellation tests start a real child process, request cancellation, verify prompt termination, and confirm that no output directory is created when a job is cancelled before execution. Console tests verify atomic job persistence, terminal cancellation state, and conversion of jobs left running across restart into explicit interrupted/recoverable records.

GitHub Actions runs the suite on Python 3.12 with FFmpeg on Ubuntu, macOS, and Windows. A separate quality job runs Ruff, strict mypy, and the frozen-lock check. The package job builds both source and wheel distributions and retains them as CI artifacts. This matrix proves portable Python/media behavior; physical desktop permission and editor tests remain separate because hosted runners do not expose representative GUI sessions.

## 2. Local daemon smoke test

Start the safe simulator:

```bash
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime uv run nimbledesk-daemon
```

In another terminal:

```bash
uv run nimbledesk-smoke --connection-file /tmp/nimbledesk-runtime/connection.json
```

The command tests a real daemon process, authenticated RPC, session lifecycle, observation, bounded screenshot transfer, payload hash, and clean session shutdown.

To test this machine's real screen without input, start the daemon with `NIMBLEDESK_BACKEND=portable` and leave `NIMBLEDESK_ENABLE_INPUT` unset. The same smoke command captures a 640×400 image and cannot move or type.

The optional input smoke test moves the pointer by ten logical pixels and restores it:

```bash
NIMBLEDESK_BACKEND=portable \
NIMBLEDESK_ENABLE_INPUT=1 \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon

uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json \
  --test-input
```

Use this only in a dedicated test desktop with no sensitive dialog active. The test never clicks or types.

## 3. OS backend contract tests

A purpose-built fixture application must show buttons, text fields, scroll containers, menus, dialogs, drag targets, multiple windows, password fields, and a machine-readable event log. The same black-box suite runs against native macOS, Windows, Linux X11, and Linux Wayland backends.

The matrix covers mixed display scaling, multiple monitors, negative virtual coordinates, rotation, different keyboard layouts, focus changes, permission denial/revocation, sleep/wake, app crashes, and emergency cancellation. Dedicated machines are required because virtual CI does not accurately reproduce every capture and accessibility API.

For a Wayland fixture run, install the desktop's XDG portal backend and GStreamer PipeWire plugin, set `NIMBLEDESK_BACKEND=native`, and keep `XDG_SESSION_TYPE=wayland`. The first observation must show the compositor-owned monitor and remote-control consent dialog. Test capture on every selected stream, absolute motion on monitors with positive and negative origins, buttons, smooth drag, continuous scroll, Unicode text, hotkeys, consent denial, session revocation, and emergency release. The backend must report denied permissions without advertising capture or input when the user cancels sharing.

## 4. Real application workflows

Each application adapter owns versioned fixtures and projects. Video-editor tests import deterministic generated media, build a timeline, save, render, reopen, revise, and cancel a job. FFprobe validates technical output and the adapter exports a timeline report that is compared with the requested edit plan.

GUI fallback operations retain before/after screenshots and semantic evidence. A test fails when it merely clicks the expected coordinate but does not produce the expected application state.

## 5. Media intelligence and creative quality

Generated and licensed evaluation corpora contain labeled shots, speech, silence, music, kills, clutches, reactions, duplicate moments, photo bursts, technical defects, and known story structure. Automated metrics cover event precision/recall, boundary error, ranking quality, diversity, context retention, caption accuracy, loudness, color, pacing constraints, and render correctness.

Store labeled and detected events as `TimelineEvent` JSON lists, then create machine-readable
precision, recall, F1, per-event-type, miss, false-positive, and temporal-boundary evidence:

```bash
uv run nimbledesk-qualify events \
  --expected corpus/expected-events.json \
  --detected output/detected_events.json \
  --tolerance-seconds 3 \
  --output evidence/event-benchmark.json
```

Creative quality also needs blinded human review. Reviewers compare variants for hook, clarity, pacing, emotion, novelty, and overall preference without seeing which planner produced them. No heuristic is treated as proof that content will become popular. With user permission, normalized retention and engagement data may inform later experiments.

## Release gate

A release requires deterministic tests, all supported backend contract subsets, a real reference-editor workflow, security tests, interruption/recovery tests, and the declared creative benchmark. Failures are recorded by OS, backend capability, application version, and media domain rather than hidden behind generic retries.

The mixed-workload daemon endurance runner observes continuously, performs bounded screen captures,
validates each capture hash, records latency, and always closes its session. It runs for eight hours
unless `--hours` is provided. On a dedicated input-test machine, `--input-every 60` also moves and
restores the pointer every sixtieth iteration. A report passes only when the requested duration
completes without an RPC, capture, input, or cleanup failure.

```bash
uv run nimbledesk-qualify endurance \
  --connection-file ~/.nimbledesk/runtime/connection.json \
  --hours 8 \
  --interval-seconds 5 \
  --capture-every 12 \
  --output evidence/endurance.json
```
