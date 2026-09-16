# Testing NimbleDesk

NimbleDesk needs evidence at five levels. Passing unit tests does not prove that an operating system granted permissions, that coordinates map correctly, that an editor completed a command, or that an automatically selected clip is compelling.

## 1. Fast deterministic suite

Run on every change:

```bash
uv run --extra dev pytest
uv run --extra dev mypy
uv run --extra dev ruff check .
```

The simulator verifies protocol validation, session state, action budgets, stale observations, approval binding, audit records, RPC authentication/replay prevention, response budgets, and gateway boundaries without touching the real desktop. RPC tests also prove that the HMAC binds the caller identity, protocol fields, request payload, and bounded absolute deadline, and that expired envelopes never dispatch.

Action-lifecycle tests enforce relative deadlines before input, reject failed preconditions, capture
and evaluate postconditions, expose resolved targets and structured error codes, replay completed
idempotent action IDs without new input, reject duplicate non-idempotent IDs, and retry backend
failures only when the request explicitly classifies the operation as safe to repeat.

Clipboard tests require independent host/session grants, cap returned text, redact both request and
result text in the audit chain, and prove a changed write cannot reuse an approval. Application
launch tests require an exact session allowlist match and an exact one-time approval token.
Window-contract tests cover focus, move, resize, minimize, maximize, and approval-gated close on
the shared controller plus native macOS, Win32, X11, and explicit Wayland capability behavior.
Studio tests cover session creation and state controls; runtime tests prove global emergency stop
releases input and invalidates pending and already granted approval authority.
Approval tests verify that the pending record freezes a bounded screenshot from the action's exact
observation and that Studio displays its image, observation ID, dimensions, and content hash.
Recovery tests verify that daemon startup invalidates stale runtime authority and releases input,
and that failed, timed-out, or cancelled backend actions force key/button release.
Watchdog tests close the daemon-liveness pipe and verify that an independent process attempts every
modifier and mouse-button release even when one platform call fails.
Safety-console tests validate persisted shortcut syntax, authenticated pause and emergency-stop
calls, atomic status output, daemon child-process startup, the parent-liveness pipe, and explicit
disablement. Physical qualification must activate the configured shortcut from outside NimbleDesk,
confirm every active session becomes paused, invoke the tray emergency stop, and verify input stays
blocked until a new session is explicitly enabled. Repeat on macOS, Windows, X11, and each supported
Wayland desktop. A Wayland run must show that the XDG GlobalShortcuts portal registered the exact
configured trigger and that activation still works after closing and restarting the daemon service.
Gateway tests verify bounded server-side UI search, internal condition waits, adapter discovery,
session discovery, and redacted audit summaries. Audit tests reopen the log across a simulated
daemon restart and detect content or chain tampering.
Observation-budget tests verify stable normalized window and accessibility hashes, focused and
actionable-first ordering, fresh-observation change summaries, omission of unchanged trees, and
same-snapshot continuation offsets. Authenticated RPC tests retrieve a retained observation and
prove that a continuation never triggers a fresh desktop capture.
Creative gateway tests verify that long-running video and photo work is submitted to the persistent
job service only after every input and output path is authorized by the active session. Status
responses omit the full request and edit plan, returning bounded summaries for model token control.
Authenticated job RPC tests submit through one client and reconnect through another, proving that
Studio and MCP share the daemon-owned queue. Shutdown tests verify that queued or active work ends
in a persisted terminal or explicitly interrupted state instead of leaving an orphaned worker.
Music, sound, cue-sheet, plan-comparison, and MCP-resource tests cover licensed catalog filtering,
embedded path authorization, atomic plan output, deterministic diffs, policy discovery, and bounded
redacted audit access.

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

NimbleDesk ships that fixture. Start it in the dedicated desktop session before starting the native
daemon:

```bash
uv run nimbledesk-fixture \
  --state evidence/fixture-state.json \
  --events evidence/fixture-events.jsonl
```

The window exposes a named counter button, public text field, password field, checkbox, 100-item
scroll list, modal dialog, and drag canvas. `fixture-state.json` is replaced atomically after every
recognized operation, while `fixture-events.jsonl` records timestamped state transitions. Password
content is never written; only its length is recorded. Use these files as the postcondition oracle
instead of treating a successful input API return as proof that the UI changed.

With the fixture focused and a native daemon running with host input enabled, run the automated
contract in a second terminal:

```bash
uv run nimbledesk-fixture-contract \
  --connection-file ~/.nimbledesk/runtime/connection.json \
  --state evidence/fixture-state.json \
  --output evidence/fixture-contract.json
```

The contract requires capture, pointer, keyboard, and accessibility capabilities. It validates a
bounded capture hash, moves and restores the pointer, types into the initially focused public field,
invokes named semantic button and checkbox controls, and checks fixture state after every action.
It always stops its session and returns a nonzero status if an API reports success without the
expected state change. Run it in a dedicated desktop because it performs real input.

The matrix covers mixed display scaling, multiple monitors, negative virtual coordinates, rotation, different keyboard layouts, focus changes, permission denial/revocation, sleep/wake, app crashes, and emergency cancellation. Dedicated machines are required because virtual CI does not accurately reproduce every capture and accessibility API.

macOS native runs must report a `macos-screencapturekit-coregraphics-cgevent` I/O provider. The
contract records Screen Recording and Accessibility independently, enumerates each NSScreen/Core
Graphics display with logical and backing-pixel bounds, and exercises capture and CGEvent input.
ScreenCaptureKit is preferred; Core Graphics remains the explicit fallback when ScreenCaptureKit
does not expose a display to a command-line or restricted process.

Windows native runs must report a `windows-wgc-dxgi-gdi-sendinput` I/O provider. The shared contract
checks negative virtual coordinates and 100%, 125%, 150%, and 200% monitor scaling, captures each
monitor through a native device context, and verifies absolute SendInput normalization across the
whole virtual desktop. Run one fixture normally and one elevated; an unelevated-to-elevated input
or focus attempt must return the explicit UIPI/secure-desktop failure rather than report success.

For a Wayland fixture run, install the desktop's XDG portal backend and GStreamer PipeWire plugin, set `NIMBLEDESK_BACKEND=native`, and keep `XDG_SESSION_TYPE=wayland`. The first observation must show the compositor-owned monitor and remote-control consent dialog. Test capture on every selected stream, absolute motion on monitors with positive and negative origins, buttons, smooth drag, continuous scroll, Unicode text, hotkeys, consent denial, session revocation, and emergency release. The backend must report denied permissions without advertising capture or input when the user cancels sharing.

## 4. Real application workflows

Each application adapter owns versioned fixtures and projects. Video-editor tests import deterministic generated media, build a timeline, save, render, reopen, revise, and cancel a job. FFprobe validates technical output and the adapter exports a timeline report that is compared with the requested edit plan.

After producing and validating an edit plan, open DaVinci Resolve with external scripting enabled
and run the physical contract:

```bash
uv run nimbledesk-davinci-contract \
  --plan output/my-video/edit_plan.json \
  --output evidence/davinci-contract
```

The runner creates a uniquely named qualification project, imports and saves a new timeline,
reruns the identical plan to prove idempotent reuse, renders and verifies the output, changes an
editable color decision and proves a new versioned timeline is created, then starts and cancels a
render. It writes `davinci-contract.json` and exits nonzero unless all four cases pass.

GUI fallback operations retain before/after screenshots and semantic evidence. A test fails when it merely clicks the expected coordinate but does not produce the expected application state.

Physical fixture reports record the operating-system release, desktop environment, session type,
backend, capabilities, and permission state. DaVinci reports record the operating-system release
and the Resolve version returned by its scripting API. Keep these fields in sanitized release
evidence so a passing result cannot be mistaken for qualification of a different target version.
On Windows, run the contract from the standalone executable so it tests the production
AppContainer boundary rather than the source-development restricted-token fallback:

```powershell
.\nimbledesk.exe windows-adapter-contract `
  --output evidence\windows-adapter-contract.json
```

Run the common malicious-adapter contract from the shipped executable on every target. It verifies
undeclared read/write denial, declared read/write access, default-denied and explicitly declared
network access, child-process denial, the 512 MiB process-tree memory ceiling, timeout cleanup, and
worker-host survival:

```bash
nimbledesk adapter-isolation-contract \
  --target-id macos-arm64 \
  --output evidence/isolation-macos-arm64.json
```

Use the matching target ID from `SUPPORT.md`. On Windows, retain both this common report and the
standalone AppContainer report. The AppContainer contract also runs during every Windows bundle
build. Every platform bundle build runs the common isolation contract against the frozen executable
before producing its archive, so a source-only pass cannot hide a packaged-worker failure.
It also generates deterministic video, transcript, event, licensed-music, and licensed-sound
fixtures and requires the frozen executable to produce a verified portrait render, captions,
creative plan, cue sheets, semantic index, and editable DaVinci timeline.
The deterministic semantic-vision fixture receives real bounded contact sheets and returns a
high-confidence event with provider token usage; the build requires that event and provenance in
the final merged analysis.
Finally, it launches the frozen daemon and Studio with a simulator backend, verifies authenticated
health and browser security headers, creates and pauses a desktop session, exercises the global
emergency stop, checks creative-intelligence readiness, and confirms clean connection-file removal.
The bundle contract also loads a deterministic Resolve scripting API fixture through the same
external module boundary used by Resolve. It requires the frozen DaVinci worker to import and save
a timeline, render output, reuse an identical plan, version a changed plan, apply editable color,
and stop a cancelled render. This proves packaged adapter behavior but does not replace the physical
Resolve 20.x release report.
Use the target IDs and minimum corpus mix declared in [SUPPORT.md](SUPPORT.md). Pass
`--corpus-id` and `--source-duration-seconds` to both `nimbledesk qualify events` and
`nimbledesk qualify ranking`; the identifiers must match for reports derived from the same source.

Generate the complete evidence manifest, replace its paths with reports collected from each target,
and run the release gate:

```bash
nimbledesk qualify release-example --output release-evidence/manifest.json
nimbledesk qualify release \
  --manifest release-evidence/manifest.json \
  --output release-evidence/report.json
```

The command exits nonzero for missing targets, mismatched OS/desktop/session identity, a Resolve
version outside 20.x, an endurance run shorter than eight hours or without capture and input,
unpaired corpus reports, less than eight hours of source material, a missing required corpus kind,
metrics below the thresholds stored in the manifest, missing malicious-adapter cases, unsigned
native installers, incomplete install/update/rollback/uninstall cases, or installer reports that
do not describe the same release version and signing key. Isolation reports must include the ten
case names emitted by the physical malicious-adapter procedure; installer reports must include the
eight lifecycle case names in the generated manifest contract. macOS and Windows reports require
both the Ed25519 release signature and the platform-native package signature; Linux requires the
signed release manifest.

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

Store human-labeled highlight moments with `moment_id`, `peak_seconds`, `event_type`, relevance
from 1–5, and optional required context bounds. Compare them with a generated
`analysis/highlights.json` manifest to measure precision@k, recall@k, mean average precision,
normalized discounted cumulative gain, event-type coverage, and context retention:

```bash
uv run nimbledesk-qualify ranking \
  --expected corpus/expected-highlights.json \
  --detected output/analysis/highlights.json \
  --cutoff 10 \
  --tolerance-seconds 5 \
  --output evidence/ranking-benchmark.json
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
