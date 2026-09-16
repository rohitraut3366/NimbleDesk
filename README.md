# NimbleDesk

NimbleDesk is a local, model-agnostic runtime for AI models that need to see and operate desktop applications. It also includes command-line workflows for finding highlights in long videos and selecting, correcting, and arranging photos.

The current release combines portable PyAutoGUI capture/input with native semantic accessibility through macOS AX, Windows UI Automation, or Linux AT-SPI. Portable Linux capture and input currently require X11. NimbleDesk separates the model-facing MCP server from the privileged desktop daemon, requires explicit input authority, rejects actions based on stale observations, and records attempted actions in a redacted audit log. The daemon also owns the persistent creative-job service, so Studio and MCP reconnect to one authoritative queue and never start competing workers for the same job store.

## What works today

- Desktop observation and bounded screenshots.
- Focused-window and semantic-control observation with observation-bound element invocation.
- Mouse movement, clicks, dragging, scrolling, text entry, key presses, and hotkeys.
- Native focus, move, resize, minimize, maximize, and approval-gated close-window actions.
- Approval-gated application launching and opt-in bounded clipboard access.
- Bounded sessions with pause, resume, stop, application allowlists, action limits, and expiry.
- An authenticated, replay-protected loopback connection between the MCP server and daemon.
- A deterministic simulator that exercises the complete control flow without touching the desktop.
- Generic video highlight detection from motion and audio peaks, plus optional supplied timeline events.
- Automatic game-event recognition with phrase, regex, and temporal inference rules in built-in or custom domain packs.
- Local Whisper transcription or supplied time-aligned transcripts.
- Explainable edit plans covering story order, pacing, speed, punch-ins, color, captions, and music.
- Executed animated punch-ins, dip-to-black entrances, and frame-blended or motion-compensated slow motion.
- Motion-guided per-segment reframing for vertical and square deliveries instead of blind center crops.
- Licensed local music-catalog ranking and timeline mixing.
- Finished FFmpeg review renders and DaVinci Resolve-importable FCPXML timelines.
- Direct project, timeline, and render execution through the DaVinci Resolve scripting API.
- A loopback-only production console with desktop-session start/pause/resume/stop, global emergency
  stop, exact-action approvals, creative-intelligence readiness, and background job monitoring.
- Durable console photo stories with duplicate removal, correction, contact sheets, and optional MP4 slideshows.
- Rendered, ranked MP4 clips with FFprobe validation and a JSON manifest.
- Recursive photo discovery, perceptual duplicate removal, technical ranking, light correction, a contact sheet, and an optional MP4 slideshow.
- Token-budgeted observations and size-bounded screenshots.

Automatic game recognition uses configurable OCR phrases and patterns, temporal event inference,
audiovisual evidence, and an optional bounded semantic-vision provider. Accuracy depends on the
game, HUD, language, crop, capture quality, and configured model. External release signing and
qualification on physical Windows, Linux, macOS, and DaVinci machines still require their actual
credentials and hardware as described in [PLAN.md](PLAN.md).

## Requirements

- macOS, Windows, or Linux. The portable Linux backend currently requires X11.
- Python 3.11 or newer. Development checks target Python 3.12.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [FFmpeg](https://ffmpeg.org/download.html), including `ffprobe`, for video highlights and photo slideshows.
- Optional [Tesseract](https://tesseract-ocr.github.io/) for automatic game HUD/event OCR.
- Optional `speech` install extra for faster-whisper, or an `openai-whisper` command on `PATH`, for transcription.
- Optional DaVinci Resolve with local external scripting enabled for direct editor execution.
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
uv sync --extra native --extra speech
```

All examples below run from the repository root. `uv run` automatically uses the project environment. To install the development tools as well:

```bash
uv sync --extra dev --extra native --extra speech
```

Three-platform CI also builds a standalone `nimbledesk` executable. Run `nimbledesk start` to launch the daemon and Studio together, or use its `daemon`, `mcp`, `studio`, `create`, `revise`, `highlights`, `photos`, `music-index`, `approve`, and `smoke` subcommands separately. See [distribution and bundle verification](docs/DISTRIBUTION.md).

Register the packaged executable as a per-user service and choose the global pause shortcut:

```bash
nimbledesk service install --pause-hotkey '<ctrl>+<alt>+<shift>+p'
```

The daemon starts a tray safety console. The shortcut pauses every active desktop session. The tray
menu can pause all sessions or perform a global emergency stop. Run the console separately during
development, or to change its persisted shortcut:

```bash
uv run nimbledesk console --hotkey '<ctrl>+<alt>+p'
```

Set `NIMBLEDESK_SAFETY_CONSOLE=0` only when another supervised safety console is already running.
On GNOME and KDE Wayland, the shortcut is compositor-owned through the XDG GlobalShortcuts portal;
the desktop may show a one-time registration dialog. Install the current desktop-specific
`xdg-desktop-portal` backend. X11, macOS, and Windows use the native global keyboard listener.

## Create a finished video

The creation workflow is the main end-to-end entry point. It analyzes the source, incorporates supplied or automatically detected semantic events, transcribes dialogue when requested, ranks moments, creates a reviewable edit plan, selects licensed music, renders a finished review MP4 with readable burned captions, retains an editable SRT sidecar, and exports a DaVinci Resolve timeline. Planned cross-dissolves compile as synchronized video and constant-power audio overlaps instead of being approximated as cuts.

```bash
uv run nimbledesk-create gameplay.mp4 output/my-video \
  --title "Best ranked moments" \
  --content-kind gameplay \
  --duration 60 \
  --aspect-ratio 9:16 \
  --pace fast \
  --mood exciting \
  --automatic
```

The command never changes the source. Its output directory contains:

- `final.mp4`: validated H.264/AAC review render with planned captions burned in for consistent review on every player. NimbleDesk uses libass when available and portable transparent caption cards otherwise.
- `final.srt`: editable captions when a transcript overlaps selected moments. Long passages are split into at most two roughly 42-character lines and timed proportionally to their source range.
- `edit_plan.json`: source ranges and explainable story, speed, visual, color, music, caption, evidence, confidence, and review decisions.
- `validation.json`: the mandatory source, timeline, duration, caption, music, and review gate applied before execution.
- `davinci_timeline.fcpxml`: editable timeline for DaVinci Resolve.
- `graphics/`: transparent title and speaker lower-third cards used by the review render and imported timeline.
- `transcript.json`: normalized transcript when speech is available.
- `detected_events.json`: merged automatic and supplied events.
- `cue_sheet.json` and `cue_sheet.csv`: music and sound usage ranges, credits, licenses, platform/territory restrictions, expiry, attribution, purpose, and generated-content disclosure.
- `variants/variant_comparison.json`: validated strongest-hook, energetic-short, and context-first plans scored with per-metric evidence and explicit tradeoffs. These scores compare edit properties and never claim to predict virality.
- `render_verification.json`: executed-output checks for duration, resolution, frame rate, streams, black/frozen ranges, silence, peak level, caption reading limits, and graphic safe areas. A blocking failure leaves this report in place and fails the creation job.
- `analysis/vision/analysis.json`: accepted multimodal semantic events with provider, model, confidence, configuration provenance, measured image bytes/tiles, conservative image-token estimates, and provider-reported input/output tokens when available.
- `analysis/transcription/analysis.json`: transcription provider, model, language, configuration provenance, segment count, processed audio duration, and provider-reported token usage when a configured speech provider is used.
- `analysis/index/content_index.json`: persistent rational-time analysis tracks, semantic moments, provenance, analyzer versions, and cache-hit metadata.
- `analysis/`: ranked intermediate clips and `highlights.json`.
- `automatic_intelligence.json`: every automatically enabled, unavailable, inapplicable, or
  policy-blocked intelligence and licensed-media capability, with the reason.

With `content_kind=auto`, detected gameplay events select gameplay treatment, an available transcript selects talking-head treatment, and other footage uses the general vlog treatment. Mandatory event types are hard constraints: creation stops if they are absent. Excluded event types are removed before ranking.

### Model-pluggable transcription

Local Whisper remains the default automatic speech engine. A different local or remote speech model
can implement the versioned JSON provider contract and be selected from the CLI or Studio. The
provider command receives a request file and must write a response file containing normalized,
time-aligned segments:

```json
{
  "provider_id": "my-speech-provider",
  "model": "my-speech-model",
  "command": ["/path/to/provider", "{request}", "{response}"],
  "timeout_seconds": 3600,
  "execution_location": "local",
  "network_access": false,
  "environment_variables": [],
  "code_paths": ["/path/to/provider", "/path/to/provider-runtime"]
}
```

```bash
uv run nimbledesk-create source.mp4 output/video \
  --transcription-provider provider.json
```

The request contains `request_version`, the absolute `source_path`, `source_name`, and optional
`language`. The response contains `segments` in the same format as `transcript.json`, optional
`detected_language`, and optional `usage` fields for `audio_seconds`, `provider_input_tokens`, and
`provider_output_tokens`. A remote provider is rejected unless the brief explicitly enables
`data_policy.allow_remote_audio`. Set `NIMBLEDESK_TRANSCRIPTION_PROVIDER` to the configuration path
to make `--automatic` and Studio discover it for future jobs. Provider environments contain only
basic runtime variables plus names explicitly listed in `environment_variables`; secret values stay
outside the configuration file. Remote execution enables network access, while local providers can
request it explicitly for a loopback model endpoint. `code_paths` must declare the provider program,
libraries, and interpreter runtime it needs; these paths are read-only inside the sandbox. Process
control variables such as `PYTHONPATH`, `PYTHONHOME`, `LD_*`, and `DYLD_*` cannot be delegated.

### Model-pluggable semantic vision

Pass `--automatic` to use every locally available intelligence component and discover configured
semantic vision and licensed catalogs. NimbleDesk enables game OCR for `gameplay` and `auto`
briefs when FFmpeg and Tesseract are installed, enables local transcription when faster-whisper or
the Whisper CLI is available, and reads catalog locations from `NIMBLEDESK_MUSIC_CATALOG` and
`NIMBLEDESK_SOUND_CATALOG`. It falls back to
`~/.nimbledesk/catalogs/music.json` and `~/.nimbledesk/catalogs/sound.json`. An explicitly supplied
flag or path takes precedence. A discovered remote vision endpoint remains disabled unless the
brief sets `data_policy.allow_remote_frames=true`; this decision appears in
`automatic_intelligence.json` rather than silently sending frames.

OCR remains useful for kill-feed text, but it cannot reliably understand a grenade throw, a near-death escape, an emotional reaction, or the narrative meaning of a visual sequence. Configure a multimodal provider command to add those events:

NimbleDesk includes an OpenAI-compatible worker for local multimodal servers and explicitly allowed
remote endpoints. Configure its endpoint and model without putting the API key in project files:

```bash
export NIMBLEDESK_VISION_ENDPOINT=https://provider.example/v1/chat/completions
export NIMBLEDESK_VISION_MODEL=multimodal-model
export NIMBLEDESK_VISION_API_KEY=your-provider-key
```

```json
{
  "provider_id": "nimbledesk-openai-compatible",
  "model": "configured-multimodal-model",
  "command": ["nimbledesk", "vision-http", "{request}", "{response}"],
  "sample_interval_seconds": 4,
  "maximum_frames": 48,
  "timeout_seconds": 300,
  "minimum_confidence": 0.65,
  "execution_location": "remote"
}
```

For a local OpenAI-compatible server, use a loopback `http://127.0.0.1:...` endpoint and set
`execution_location` to `local`. Remote execution is rejected unless the creative brief explicitly
sets `data_policy.allow_remote_frames=true`. The worker rejects non-HTTPS remote URLs, sends
low-detail bounded contact sheets, accepts only structured events, and never writes the API key.

NimbleDesk extracts at most the configured number of 640-pixel samples and packs twelve timestamped frames into each contact sheet. The worker receives the request JSON path and response JSON path as separate arguments without a shell. It must write `{"events":[{"time_seconds":12,"event_type":"grenade_kill","label":"Grenade double kill","confidence":0.91,"evidence":"throw, explosion, and two elimination markers"}]}`. Responses are schema-validated, limited to one megabyte, filtered by confidence, merged with OCR and supplied events, and retained with provenance. This bounded contact-sheet protocol keeps image-token use predictable and lets local models, hosted APIs, or future providers implement the same contract.

Vision and transcription commands run in a declared-path sandbox with network denied by default,
deadlines, cancellation, aggregate 512 MiB process-tree memory limits, and descendant cleanup. A
provider cannot read unrelated user files, and a timed-out, cancelled, or over-limit provider cannot
leave its helper processes running after the job stops. Standard output is capped at one megabyte
and standard error at 64 KiB so a faulty provider cannot grow temporary output without bound.

### Persistent long-form analysis

Every creation fingerprints the source and builds time-aligned motion, spatial motion centroids, audio energy/silence, color/exposure, shot-boundary, transcript-semantic, and combined semantic tracks. Each point has a rational source range, confidence, evidence, analyzer/version, and configuration hash. Transcript questions, reactions, instructions, and payoffs become semantic moments; domain-pack events and coincident audiovisual action remain separate evidence. When source and delivery aspect ratios differ, the planner smooths spatial motion into bounded crop keyframes, follows the action in FFmpeg, and exports editable position keyframes in FCPXML. Weak motion retains a documented center crop rather than inventing a subject location.

The index adapts its coarse sampling rate for recordings over 30 minutes and over two hours. Tracks are written atomically under `analysis/index`. Re-running the same output reuses unchanged tracks. Changing a transcript or event file rebuilds the semantic track while retaining valid motion, audio, color, and shot analysis. Changing the source fingerprint invalidates dependent tracks. Source media is always read-only.

The final dialogue, licensed music, and sound-effect mix is mastered to the delivery plan's integrated loudness target (−14 LUFS by default), constrained to −1.5 dB true peak, and limited before AAC encoding. Music is still ducked from the measured dialogue signal before mastering.

### Validate and revise a plan

Every newly generated plan is validated before NimbleDesk exports a timeline or starts a render. Execution stops when a source range exceeds the indexed asset, timeline segments overlap or leave gaps, the target duration is exceeded, captions or music exceed their bounds, a music file/license is unavailable, or a blocking review item remains. Warnings and measured duration are retained in `validation.json`.

Revise an existing plan while preserving selected decisions:

```bash
uv run nimbledesk-revise output/my-video/edit_plan.json output/revision-2 \
  --duration 45 \
  --pace fast \
  --color-look vivid \
  --lock segment-002 \
  --render
```

Use `--lock` or `--unlock` more than once. Locked segments retain their source selection, speed, visual treatment, and evidence while unlocked content is retimed or removed to meet the new duration. Caption timing and beat alignment are recalculated after timeline changes. The revision directory contains a new `edit_plan.json`, `plan_diff.json`, `validation.json`, FCPXML timeline, and optional render. Use `--davinci` or `--davinci-render` to send the validated revision to Resolve.

### Creation options

| Option | Default | Meaning |
| --- | --- | --- |
| `SOURCE` | Required | Source video. |
| `OUTPUT_DIRECTORY` | Required | New or existing output directory. |
| `--brief` | None | Complete `CreativeBrief` JSON; when present it replaces the individual brief flags. |
| `--title` | `Untitled creation` | Project, plan, and DaVinci timeline name. |
| `--content-kind` | `auto` | `auto`, `gameplay`, `talking_head`, `tutorial`, or `vlog`. |
| `--platform` | `youtube` | Intended delivery platform recorded in the plan. |
| `--duration` | `60` | Target timeline duration from 5 to 14,400 seconds. |
| `--aspect-ratio` | `16:9` | `16:9`, `9:16`, or `1:1`. |
| `--pace` | `balanced` | `calm`, `balanced`, or `fast`. |
| `--mood` | `engaging` | Mood terms used for music supervision. |
| `--clip-count` | `10` | Maximum selected moments, from 1 to 100. |
| `--color-look` | `natural_contrast` | Named editable color treatment; also accepts `vivid`, `high_contrast`, `cinematic`, `moody`, `flat`, or `neutral`. |
| `--no-captions` | Off | Do not map transcript segments into caption cues. |
| `--no-music` | Off | Do not select or mix music. |
| `--events` | None | Supplied event JSON in the highlight-event format. |
| `--automatic` | Off | Discover and use available OCR, local transcription, configured semantic vision, and licensed catalogs. Studio enables this by default. |
| `--game-ocr` | Off | Sample frames and run the selected Tesseract game pack. |
| `--game-pack` | Built-in shooter | Custom game-pack JSON. |
| `--transcript` | None | Existing normalized transcript JSON. |
| `--transcribe` | Off | Run local faster-whisper when the `speech` extra is installed, otherwise use the local `whisper` CLI. |
| `--whisper-model` | `small` | Whisper model name. Models download through Whisper on first use. |
| `--language` | Auto | Optional Whisper language code. |
| `--music-catalog` | None | JSON catalog of local music with explicit license metadata. |
| `--sound-catalog` | None | JSON catalog of local sound effects with tags and explicit license metadata. |
| `--plan-only` | Off | Analyze, plan, and export FCPXML without rendering `final.mp4`. |
| `--davinci` | Off | Import the generated timeline into a running DaVinci Resolve instance. |
| `--davinci-render` | Off | Import the timeline, render it in Resolve, and verify `davinci-final.mp4`. |

You can supply one complete brief instead of flags:

```json
{
  "title": "Road to the final",
  "content_kind": "gameplay",
  "audience": "competitive FPS players",
  "platform": "youtube_shorts",
  "target_duration_seconds": 55,
  "aspect_ratio": "9:16",
  "pace": "fast",
  "mood": "tense exciting",
  "clip_count": 8,
  "captions": true,
  "music": true,
  "color_look": "vivid",
  "mandatory_event_types": ["clutch"],
  "excluded_event_types": ["death"],
  "mandatory_moments": [
    {"label": "Sponsor message", "start_seconds": 12.0, "end_seconds": 18.5}
  ],
  "excluded_moments": [
    {"label": "Private chat", "start_seconds": 105.0, "end_seconds": 112.0}
  ]
}
```

Use it with `--brief brief.json`. Required event types are bound to the strongest detected
source occurrence, forced into candidate selection, and checked again after the plan is trimmed.
Explicit required source moments receive the same final-timeline check. Excluded source moments
are removed before selection and make validation fail if any edit operation reintroduces them.

Creative briefs also accept reusable production controls: `references`, `preferred_speakers`,
`excluded_content`, `title_style`, `transition_style`, `music_style`, `brand`, `accessibility`,
`autonomy`, and `data_policy`. Brand settings can name a logo, its corner and size, a font file,
and protected colors. NimbleDesk validates required assets, renders the chosen font and accent
color into titles/lower thirds, and places the logo in both FFmpeg reviews and editable FCPXML
timelines. Accessibility-required captions are hard constraints. Requesting audio description
without an available description track blocks validation instead of silently omitting it.

`autonomy` is one of `plan_only`, `review_before_render`, `render_review`, or `execute_editor`.
Editor execution requires the last value. A provider configuration declaring
`"execution_location": "remote"` cannot receive contact-sheet frames unless the brief explicitly
sets `data_policy.allow_remote_frames` to `true`.

### Style profiles

A style profile stores visible defaults without overriding values supplied in the current brief:

```json
{
  "profile_id": "gaming-shorts",
  "name": "Gaming shorts",
  "defaults": {
    "audience": "competitive FPS players",
    "platform": "youtube_shorts",
    "aspect_ratio": "9:16",
    "pace": "fast",
    "color_look": "vivid",
    "brand": {"logo_path": "/path/logo.png", "primary_color": "#ff5500"},
    "autonomy": "render_review"
  }
}
```

Pass a profile file with `nimbledesk create ... --style-profile profile.json`. Studio profiles are
stored under `~/.nimbledesk/style-profiles`, selected from the creation form, and managed through
`GET /api/style-profiles` and `PUT /api/style-profiles/{profile_id}`. Choosing a watchability
variant records explicit feedback in the selected profile; no implicit viewing behavior is used.

### Transcription

Install the local faster-whisper integration with `uv sync --extra speech`, then pass `--transcribe`.
It uses voice-activity filtering, word timestamps, and the selected model. If faster-whisper is not
installed, NimbleDesk uses an `openai-whisper` command found on `PATH`. Both providers process media
locally and record normalized, time-aligned segments; model weights may download on first use.

To use another transcription engine, provide normalized JSON:

```json
[
  {
    "source_range": {"start_seconds": 17.2, "end_seconds": 19.8},
    "text": "That was close!",
    "confidence": 0.96,
    "speaker": "Rohit"
  }
]
```

### Automatic game events

`--game-ocr` samples frames with FFmpeg, runs Tesseract, and applies the built-in shooter domain pack. It recognizes exact HUD phrases and configurable regular expressions. It then analyzes the event sequence: nearby kills become a multi-kill, and a kill or victory soon after critical health becomes a clutch. Per-event cooldowns collapse OCR copies while retaining distinct rapid kills. Combine `--game-ocr` with telemetry or manual `--events` when available; the workflow merges both evidence sources.

A custom pack controls sampling, crop, phrases, and importance:

```json
{
  "name": "my-game",
  "sample_interval_seconds": 1,
  "crop": "iw*0.45:ih*0.35:iw*0.55:0",
  "phrases": {
    "kill": ["eliminated"],
    "clutch": ["clutch", "last player standing"]
  },
  "patterns": {
    "kill": ["\\b(?:killed|eliminated)\\s+[a-z0-9_]"],
    "narrow_survival": ["\\b(?:[1-9]|1[0-5])\\s*(?:hp|health)\\b"]
  },
  "importance": {"kill": 0.75, "clutch": 1.0},
  "cooldown_seconds": {"kill": 1.5},
  "multi_kill_window_seconds": 8,
  "clutch_window_seconds": 20
}
```

The optional crop uses FFmpeg's `crop=width:height:x:y` expression and should cover the game's kill feed or event banner. Pattern values are Python regular expressions evaluated case-insensitively against normalized OCR text. Keep them specific to visible HUD grammar to reduce false matches.

### Licensed music selection

For edits with enough setup and payoff duration, NimbleDesk supervises scene-level music rather
than stretching one song across the entire timeline. It chooses a lower-energy tension/setup cue
and a distinct higher-energy payoff cue, aligns the transition to the first payoff, adds short
fades, ducks the combined music bus under dialogue, and exports every cue as a separate editable
FCPXML clip and cue-sheet row. Short edits or one-track catalogs retain a single continuous bed.

NimbleDesk only selects tracks declared in a user-supplied catalog. Use absolute paths so DaVinci can resolve the media:

Create a catalog from one track or a directory of AAC, FLAC, M4A, MP3, OGG, or WAV files:

```bash
uv run nimbledesk-music-index music/ music.json \
  --license "user-owned; worldwide social usage" \
  --mood exciting \
  --mood tense
```

Use `--attribution` when the license requires a credit and `--contains-vocals` for songs with vocals. The indexer decodes audio locally, measures duration and energy, estimates tempo when the rhythmic signal is strong enough, infers broad energy/mood tags, and preserves every declared license field. It scans subdirectories and never modifies music sources. Catalog-relative paths and absolute paths are both supported.

```json
[
  {
    "path": "/absolute/path/music/action-bed.wav",
    "duration_seconds": 180,
    "title": "Action Bed",
    "mood": ["exciting", "tense"],
    "bpm": 128,
    "energy": 0.85,
    "instrumental": true,
    "license": "user-owned; worldwide social usage",
    "attribution": null
  }
]
```

The planner ranks mood, energy, duration, pace, and vocal competition. When measured tempo is available, it offsets the music source so a beat lands on the first payoff. The renderer applies the planned gain and side-chain compression so source dialogue/game audio ducks the music dynamically. The selected cue, license, source/timeline ranges, beat interval/alignment, ducking target, and rationale remain editable in `edit_plan.json`.

### Licensed sound design

Supply optional scene accents with `--sound-catalog sounds.json` or the matching Studio field:

```json
[
  {
    "path": "/absolute/path/sounds/impact.wav",
    "duration_seconds": 1.2,
    "title": "Licensed impact",
    "tags": ["impact", "hit", "payoff"],
    "license": "user-owned; worldwide social usage",
    "attribution": null
  }
]
```

NimbleDesk matches tags to segment role and recorded evidence such as a grenade, kill, clutch, hook, payoff, or outro. It uses each asset once, places at most one cue per segment, and enforces at least two seconds between cues. Cue source/timeline ranges, gain, purpose, segment anchor, license, and rationale remain editable in `edit_plan.json`. FFmpeg mixes cues at their planned times and FCPXML exports them as separate effects-lane clips with editable gain. Plan validation rejects missing, unlicensed, out-of-range, or orphaned cues; revisions move retained cues with their segments.

### DaVinci Resolve execution

Open DaVinci Resolve and enable local external scripting in Resolve preferences. NimbleDesk searches the standard scripting-module directory on macOS, Windows, and Linux. If Resolve is installed elsewhere, set `RESOLVE_SCRIPT_API` to its `Developer/Scripting/Modules` directory.

Import and render directly:

```bash
uv run nimbledesk-create gameplay.mp4 output/my-video \
  --brief brief.json \
  --events events.json \
  --music-catalog music.json \
  --davinci-render
```

`--davinci` stops after creating/selecting the project and importing the editable timeline. Each plan receives a content-derived timeline version name; rerunning the identical plan reuses that timeline instead of duplicating it. The adapter verifies that Resolve retained every primary clip and caption, applies each planned exposure and saturation treatment as an editable CDL grade, and requires Resolve to save the project. `--davinci-render` additionally enables burned subtitle export, creates an MP4/H.264 job, starts it, checks Resolve's final job status, saves again, and verifies `davinci-final.mp4`. Resolve scripting runs in a dedicated subprocess with a minimal environment and a bounded typed result, so an API crash does not terminate Studio. Cancellation is cooperative: the worker observes a cancellation marker and calls Resolve's `StopRendering`; an unresponsive worker is terminated after a bounded grace period. The standalone `davinci_timeline.fcpxml` includes editable caption, cross-dissolve, color, and tracked-reframe elements and can also be imported manually with **File → Import → Timeline**.

## Run the creation console

Start the loopback-only web console:

```bash
uv run nimbledesk-ui
```

Open `http://127.0.0.1:8765`, enter absolute source/output paths, choose the creative brief, and start a background job. Studio reports creative intelligence readiness before submission, including local OCR and transcription availability, configured semantic vision, licensed music, and sound catalogs. The page reports the analysis, transcription, planning, render, and DaVinci stages, plays finished review videos inline, previews photo contact sheets, and provides generated plans, timelines, cue sheets, verification reports, and media as bounded downloads. It can cancel queued or running work. Clear **Render review MP4** when you want to inspect and approve the plan before spending time on a render. When the desktop daemon is running, exact application actions awaiting a human decision appear at the top with their adapter, command, complete arguments, expiry, and Approve/Reject controls. Each pending action freezes a bounded JPEG from its exact source observation and shows the observation ID, dimensions, and SHA-256 with the image.

For repeated editor operations, **Approve scope for 10 minutes** creates a revocable rule bound to the current session, action kind, application, window, adapter, and command with a 20-use limit. Clipboard writes always require exact approval for each value and cannot create temporary rules.

Completed video jobs show the explainable watchability variants with their score, tradeoff, recommendation, and selected state. **Select and render** persists the choice and creates a normal validated revision job from that variant; the selected plan receives its own FCPXML timeline, optional review MP4, verification report, and DaVinci execution path.

Render verification fully decodes video and audio and treats failed FFmpeg scans as blocking. It
measures duration, resolution, frame rate, black and frozen ranges, silence, maximum volume,
integrated LUFS, true peak, caption execution/readability, and graphic safe areas. Each verified
render also produces a 12-frame contact sheet and waveform image, both viewable from Studio.

When a plan completes, expand **Review and revise decisions**. Each segment shows its role, source range, speed, color treatment, punch-in scale, crop anchor, strongest confidence, and the evidence used to select it. Check the segments you approve; checked segments are locked so later duration, pace, and color changes cannot alter their source selection or treatment. Choose the new target, pace, and color look, then select optional MP4 rendering or Resolve import and click **Build revision**. NimbleDesk writes each version under `<original-output>/revisions/` with its own `edit_plan.json`, `plan_diff.json`, `validation.json`, FCPXML timeline, and optional render. Revisions are normal durable jobs, so they are cancellable and remain visible after restarting the console.

Use **Create from photos** with a photo or folder to run perceptual duplicate removal, technical-quality ranking, conservative correction, selection, and contact-sheet generation as a durable background job. Enable the optional MP4 slideshow when needed. Photo analysis and correction check cancellation between assets, and cancellation terminates the FFmpeg slideshow process.

Job state is atomically persisted under `~/.nimbledesk/jobs`. Restarting the console retains completed, failed, and cancelled history. Work that was active during a process or machine restart is marked `interrupted`; submit the same source and output again to resume from the valid per-track semantic cache. Cancellation propagates into FFmpeg analysis/rendering, Whisper, Tesseract OCR, highlight rendering, and DaVinci rendering; child processes are terminated and Resolve receives `StopRendering`.

Use `--port` to choose another port. The server rejects non-loopback bind addresses so local file and editor controls are not exposed to the network. Set `NIMBLEDESK_JOB_DIR` to use another job-history directory.

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
2. Call `session_start` with a clear reason. Set `input_enabled` to `true` only when input is intended. Enable `clipboard_enabled` only for a workflow that needs clipboard text, and allowlist every application that may be launched. Add only the project or analysis directories needed by media tools to `granted_paths`.
3. Call `desktop_observe` and retain its `observation_id`, active application, focused window, semantic elements, display bounds, and semantic hashes. On the next fresh observation, pass that ID as `previous_observation_id` so unchanged trees can be omitted. Follow a returned continuation with `continuation_observation_id` and its offsets to page through the exact same snapshot.
4. Call `take_screenshot` with that observation. Prefer a crop when the relevant region is known.
5. Prefer `click_element` when the observation contains the intended accessible control. Use `click_text` for visible labels in inaccessible applications. For a model-detected visual box, call `capture_region_signature` immediately before `click_visual`. Use raw coordinates only when none of these targets apply. Include expected application and window IDs when available.
6. Observe again after each action that can change the interface. Do not reuse an old observation.
7. Call `session_pause` if human intervention is needed, and `session_stop` when finished.

### MCP tool reference

| Tool | Required arguments | Optional arguments and behavior |
| --- | --- | --- |
| `health` | None | Checks daemon availability. |
| `session_start` | `reason` | `input_enabled=false`; `clipboard_enabled=false`; `allowed_applications=[]`. Sessions default to 1,000 actions and one hour. |
| `session_status` | `session_id` | Returns current state, original limits, expiry, and consumed action count. |
| `capabilities_get` | None | Returns the selected backend and currently advertised capabilities. |
| `permissions_get` | `session_id` | Performs a fresh permission probe and returns its new observation ID, capabilities, and warnings. |
| `desktop_observe` | `session_id` | `max_estimated_text_tokens=2000` (128–100,000); `max_windows=10` (0–200); `max_elements=100` (0–2,000). `previous_observation_id` returns semantic hashes and a change summary, while `omit_unchanged=true` suppresses repeated trees. Use the returned `continuation.observation_id` as `continuation_observation_id` with its offsets to read another bounded page from the same snapshot. |
| `ui_find` | `session_id` plus `role` or `name` | Searches accessibility elements server-side and returns bounded actionable matches tied to one new observation. |
| `target_resolve` | `session_id`, `observation_id`, `target` | Resolves coordinate, semantic, selector, visual-signature, or OCR targets without producing input. |
| `action_execute` | Complete `ActionRequest` | Executes one primitive through the same stale-observation, policy, approval, recovery, deadline, pre/postcondition, idempotency, and audit path as the convenience tools. Explicitly idempotent failures can retry up to `maximum_retries`; completed duplicate IDs replay without repeating input. |
| `condition_wait` | `session_id`, `condition_type`, `value` | Internally waits up to 60 seconds for an active application, focused window, or element presence/absence without consuming model turns for polling. |
| `media_index_open` | `session_id`, `index_path` | Opens a content index only within the session's explicit `granted_paths`; returns a session-scoped, content-derived handle without exposing the source path. |
| `media_index_search` | `session_id`, `index_id`, `query` | Searches labels, transcript text, event types, and evidence. `maximum_results=20`; `maximum_tokens=2000`; returns stable result IDs and explicit usage/truncation. |
| `media_index_detail` | `session_id`, `index_id`, `result_id` | Retrieves one time-aligned result by stable ID. `maximum_tokens=2000`; expires when the session stops. |
| `content_index_query` / `moments_find` | `session_id`, `index_id`, description | Searches transcript, event, motion, audio, shot, and semantic evidence with stable detail handles and explicit result/token limits. |
| `media_ingest` / `media_analysis_start` | `session_id`, source, output, brief | Starts persistent fingerprinting, integrity checks, hierarchical signal extraction, semantic analysis, ranking, clip rendering, and plan generation. |
| `highlights_rank` / `clip_set_generate` | `session_id`, completed job ID | Returns bounded evidence-backed candidate summaries or the rendered clip set from the persistent analysis job. |
| `domain_packs_list` / `domain_pack_describe` | Optional pack ID | Discovers the built-in general editorial and generic shooter contracts, including kill, grenade, multi-kill, clutch, narrow-survival, and victory evidence. |
| `variants_compare` | `session_id`, `job_id` | Returns bounded hook, payoff-time, information-density, pacing, narrative, caption, and evidence metrics with explicit tradeoffs. |
| `music_brief_create` / `music_search` | Brief and licensed local catalog | Produces inspectable mood, energy, platform, duration, and instrumental criteria and returns bounded ranked metadata without exposing asset paths. |
| `music_cues_generate` / `sound_design_generate` | Granted plan, catalog, and output plan | Writes an atomic revised plan with scene-aware beat alignment or sparse evidence-based sound accents after validating every embedded asset path. |
| `cue_sheet_export` | Granted plan and output directory | Exports JSON and CSV license, attribution, territory, expiry, and generated-asset provenance. |
| `edit_plan_validate` / `edit_plan_compare` | Granted plan paths | Validates timing, sources, assets, licenses, accessibility, captions, and delivery; comparison returns a bounded deterministic field diff. |
| `render_validate` | Granted plan, render, and report paths | Fully decodes audio/video, checks delivery and timing, and writes verification diagnostics. |
| `creative_brief_create` | Creative brief fields | Validates the brief and fills production defaults for platform, pace, captions, music, color, autonomy, and data policy. |
| `edit_plan_generate` | `session_id`, source, output directory, brief | Starts a persistent granted-path job for automatic transcription, game/semantic event detection, indexing, highlights, music/sound selection, variants, plan validation, and timeline export without a final render. |
| `edit_plan_execute` | Same inputs plus DaVinci options | Runs the complete persistent creative workflow, renders and verifies the output, and can import or render through a running DaVinci Resolve instance. |
| `media_analysis_status` / `media_analysis_get` | `session_id`, `job_id` | Returns compact progress or terminal results with bounded capability, variant, and artifact summaries. Jobs survive process restart as explicit interrupted/recoverable records. |
| `media_analysis_cancel` | `session_id`, `job_id` | Cancels the session-owned job and its active media/adapter child process. |
| `edit_revision_apply` | `session_id`, `job_id`, revision changes | Preserves locked decisions, validates the revised plan, and optionally renders or executes it in DaVinci. |
| `edit_review_render` | `session_id`, completed planning job ID | Starts a persistent revision render and full verification without changing approved plan decisions. |
| `photo_creation_start` | `session_id`, photo source, output directory | Selects and corrects photos and can create a contact sheet, slideshow, thumbnail, poster, collage, carousel, and animated GIF. |
| `take_screenshot` | `session_id`, `observation_id` | Crop with all of `left`, `top`, `width`, `height`; `image_format=jpeg`; `max_width=1280`; `max_height=800`; `jpeg_quality=75`. |
| `capture_region_signature` | `session_id`, `observation_id`, `left`, `top`, `width`, `height` | Losslessly recaptures a target crop and returns its SHA-256 plus measured image usage without returning duplicate image bytes. |
| `move_mouse` | `session_id`, `observation_id`, `x`, `y` | `duration=0.2`; expected application/window IDs. |
| `click` | `session_id`, `observation_id`, `x`, `y` | `button=left`; `clicks=1`; `interval=0.1`; expected application/window IDs. |
| `click_element` | `session_id`, `observation_id`, `element_id` | Invokes an observation-bound accessibility element; expected application/window IDs. Returns `capability_unavailable` when the selected backend has no semantic provider. |
| `click_text` | `session_id`, `observation_id`, `text` | Runs local Tesseract OCR inside an optional search region, rejects missing or ambiguous matches, and clicks the resolved center. `exact=false`; `minimum_confidence=0.75`. |
| `click_visual` | `session_id`, `observation_id`, bounds, `signature`, `confidence` | Requires confidence of at least 0.65, losslessly recaptures the crop, and clicks its center only if the SHA-256 is unchanged. |
| `drag_to` | `session_id`, `observation_id`, `x`, `y` | Drags from the current pointer; `duration=0.5`; `button=left`; expected application/window IDs. |
| `scroll` | `session_id`, `observation_id`, `amount` | Positive scrolls up and negative scrolls down; expected application/window IDs. |
| `type_text` | `session_id`, `observation_id`, `text` | `interval=0.02`; expected application/window IDs. Text is redacted from the audit record. |
| `press_key` | `session_id`, `observation_id`, `key` | `presses=1`; `interval=0.1`; accepts PyAutoGUI names such as `enter`, `tab`, `escape`, `backspace`, and `f5`. |
| `hotkey` | `session_id`, `observation_id`, `keys` | Example: `["command", "s"]` on macOS or `["ctrl", "s"]` elsewhere. |
| `focus_window` | `session_id`, `observation_id`, `window_id` | Activates a native window returned by the latest observation; accepts an expected application ID. |
| `move_window` | `session_id`, `observation_id`, `window_id`, `left`, `top` | Moves the current observation-bound native window in logical desktop coordinates. |
| `resize_window` | `session_id`, `observation_id`, `window_id`, `width`, `height` | Resizes the observation-bound native window. |
| `minimize_window` | `session_id`, `observation_id`, `window_id` | Minimizes the observation-bound native window. |
| `maximize_window` | `session_id`, `observation_id`, `window_id` | Maximizes or enters the platform full-screen state for the observation-bound window. |
| `close_window` | `session_id`, `observation_id`, `window_id` | Requests a native close and always requires exact-action approval. Unsaved-work prompts remain visible to the user. |
| `read_clipboard` | `session_id`, `observation_id` | Requires both clipboard gates. Returns at most `maximum_characters=10000` with explicit truncation. Clipboard text is redacted from audit records. |
| `write_clipboard` | `session_id`, `observation_id`, `text` | Requires both clipboard gates and exact-action approval; pass the approval token on the repeated call. |
| `launch_application` | `session_id`, `observation_id`, `application_id` | Requires an exact match in the session application allowlist and exact-action approval. Uses a macOS bundle ID or Windows executable/AppUserModel ID. |
| `wait` | `session_id`, `seconds` | Waits for an interface or animation to settle; maximum 10 seconds per call. |
| `application_command` | `session_id`, `observation_id`, `adapter_id`, `command`, `arguments` | Executes an installed subprocess adapter after exact-action approval; pass the returned token as `approval_token` on the repeated call. |
| `adapter_execute` | Same adapter arguments | Stable planned-name alias using the identical policy, approval, isolation, timeout, path-grant, and audit route. |
| `adapters_list` | None | Lists installed adapters and bounded command contracts without exposing package paths. |
| `adapter_describe` | `adapter_id` | Returns one adapter's version, isolation, network declaration, risks, arguments, and timeouts. |
| `approval_status` | `approval_id` | Polls a human decision. An approved response contains the short-lived token; consumed, rejected, invalidated, and expired approvals cannot authorize work. |
| `audit_query` | `session_id` | Returns up to 100 redacted action summaries and hash-chain integrity evidence. |
| `session_pause` | `session_id` | Pauses input and releases common modifier keys and mouse buttons. |
| `session_resume` | `session_id` | Restores the original session limits; a stopped session cannot resume. |
| `session_stop` | `session_id` | Permanently stops the session and releases input. |

The MCP server also exposes `nimbledesk://capabilities`, `nimbledesk://policy`,
`nimbledesk://adapters`, `nimbledesk://sessions/{session_id}`, and
`nimbledesk://sessions/{session_id}/audit` resources. Session and audit resources remain
authenticated, session-scoped, bounded, and redacted.

Coordinates are logical desktop coordinates from `desktop_observe`. Element IDs are valid only for the observation that returned them. The daemon verifies the observation is fresh and, when supplied, that the active application and focused window still match. PyAutoGUI's corner fail-safe remains enabled: move the pointer to a screen corner to interrupt portable automation.

Semantic, OCR, and visual clicks opt into one bounded stale-state recovery by default. If the observation changed before execution, the daemon observes again and re-resolves the target while requiring the expected application and window. Semantic recovery requires one enabled element with the same role and accessible name. OCR recovery repeats local recognition and stops on ambiguity. Visual recovery losslessly recaptures the same box and requires the exact signature. Application commands and raw coordinates are never retried. Recovery happens only before input, remains policy checked, consumes one action budget entry, and is recorded in the result and audit trail.

The generic action contract accepts active-application, focused-window, element, window, and semantic-hash conditions. Preconditions stop before input. Requested postconditions trigger one fresh observation and report each measured value. Results include a stable error code, measured duration, resolved target, backend evidence, postcondition evaluation, and the next observation ID. Relative deadlines are checked before validation, policy, execution, retries, and after the backend returns. Only actions declared `read_only` or `idempotent` may retry backend failures; a completed `non_idempotent` action ID cannot execute twice.

Application adapters are disabled until a manifest and its Python package are installed. Pending actions appear in NimbleDesk Studio when it uses the same `NIMBLEDESK_CONNECTION_FILE` as the daemon. See [docs/ADAPTERS.md](docs/ADAPTERS.md) for the manifest, worker contract, path grants, approval flow, and isolation limits.

Before opening its authenticated RPC listener, the daemon clears stale sessions, observations,
approvals, content-index handles, and common pressed keys/buttons. Handled backend failures,
timeouts, and cancellations force the same input release. Normal shutdown performs a global
emergency stop before deleting the connection file.
The daemon also starts a minimal companion watchdog through an inherited pipe. If the daemon exits
or crashes, pipe closure makes the independent process release Shift, Control, Alt, Command/Windows,
and all common mouse buttons before the service manager restarts the daemon.
It also starts the tray safety console described in **Install**. Closing the daemon's inherited pipe
closes that console, so a service restart cannot leave duplicate hotkey listeners or tray icons.

## Use the real desktop

### Read-only screen access

This mode exposes observations and screenshots while the host gate rejects mouse and keyboard actions:

```bash
NIMBLEDESK_BACKEND=native \
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
NIMBLEDESK_BACKEND=native \
NIMBLEDESK_ENABLE_INPUT=1 \
NIMBLEDESK_RUNTIME_DIR=/tmp/nimbledesk-runtime \
uv run nimbledesk-daemon
```

The model then calls `session_start` with `input_enabled=true`. If either gate is false, input actions are rejected.

Clipboard access has another host gate and is disabled by default. Start the daemon with
`NIMBLEDESK_ENABLE_CLIPBOARD=1`, then set both `input_enabled=true` and
`clipboard_enabled=true` for that session. Reads are bounded; writes and application launches
always appear in Studio for exact-action approval.

The optional smoke test moves the pointer by 10 logical pixels and restores it. It never clicks or types:

```bash
uv run nimbledesk-smoke \
  --connection-file /tmp/nimbledesk-runtime/connection.json \
  --test-input
```

Run real input tests only on a dedicated test desktop with no sensitive or destructive dialog focused.

### Operating-system permissions

**macOS:** Install the `native` extra, then grant the terminal or packaged process **Screen & System Audio Recording** for screenshots and **Accessibility** for semantic controls, mouse, and keyboard in **System Settings → Privacy & Security**. Restart the daemon after changing permissions. The native backend uses macOS AX and reports `denied` until the exact daemon process is trusted.

**Windows:** Install the `native` extra to enable UI Automation through `pywinauto`. A normal process can control applications running at the same integrity level. It cannot control an application launched as administrator; run both at the same level. UIA failures are returned without retrying through mouse coordinates.

**Linux:** Install the distribution AT-SPI bindings (`sudo apt install python3-pyatspi` on Ubuntu/Debian) and make them visible to the NimbleDesk Python environment. The native backend uses AT-SPI for semantic controls. On X11, capture and input use the portable backend and require access to `DISPLAY`. On Wayland, NimbleDesk automatically uses the XDG ScreenCast and RemoteDesktop portals; install `xdg-desktop-portal`, the portal backend for the desktop, `gstreamer1.0-tools`, and `gstreamer1.0-pipewire`. The first observation opens the desktop's standard consent dialog. Only the monitors selected there are exposed, and denying the dialog leaves screen, pointer, and keyboard capabilities disabled.

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

Without event analysis, ranking uses generic audiovisual activity. Enable game OCR and, when
available, a semantic vision provider to identify kills, grenade kills, multi-kills, clutches,
narrow survivals, and victories. Corroborating detectors are fused and every event records its
detector provenance and evidence in `detected_events.json`.

## Create from photos

The photo command recursively scans one image or a directory, applies EXIF orientation, detects perceptual duplicates, scores sharpness, exposure, contrast, and resolution, keeps visually diverse high-scoring images, and creates lightly corrected JPEG copies. Supported source formats are JPEG, PNG, WebP, TIFF, and TIF. Source images remain unchanged.

Pass `--social-assets --title "My story" --platform instagram` to produce a 1280×720
thumbnail, a platform poster (1080×1920 for TikTok or 1080×1350 otherwise), a square collage,
and up to ten numbered 1080×1080 carousel cards. Pass `--animated-gif` for a looping square GIF
from up to twelve selected images. Studio exposes the same options and previews or downloads every
generated artifact from the completed photo job.

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

1. Start with `desktop_observe(max_estimated_text_tokens=512, max_windows=5)` and keep its observation ID.
2. Use window bounds to crop `take_screenshot` to the relevant application or control.
3. Keep JPEG at the default quality for visual navigation and reduce `max_width`/`max_height` when text remains readable.
4. Use PNG only for small, text-heavy regions where JPEG artifacts prevent reading.
5. Pass the prior ID as `previous_observation_id` when observing again. Unchanged window and accessibility trees are represented by stable SHA-256 values and omitted by default.
6. When an observation is paginated, use its continuation ID and offsets to fetch more controls from the same frozen snapshot instead of taking another observation.
7. Preserve application and window IDs in the model's working state; avoid resending entire earlier observations.

Observation responses include a `usage` object with `estimated_text_tokens`, `maximum_text_tokens`, and `truncated_fields`. They also include normalized `windows_sha256` and `ui_tree_sha256` values. A fresh observation made with `previous_observation_id` reports the fields and item counts that changed. Required safety fields are preserved even when window titles and lists are shortened. Continuations remain valid only while their short-lived source observation is retained. Screenshot limits are 64–4,096 pixels per dimension; JPEG quality accepts 20–95.

Every capture also reports its source dimensions, delivered pixel count, exact encoded byte count, estimated 512-pixel vision tiles, and a conservative image-token estimate (`85 + 170 × tiles`). Provider billing formulas differ, so this estimate is for comparing capture choices; the byte and pixel measurements are exact. Lower `max_width`, `max_height`, or JPEG quality and recapture when the resulting text and controls remain readable.

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
The exact operating-system, desktop, DaVinci Resolve, and corpus qualification matrix is declared
in [docs/SUPPORT.md](docs/SUPPORT.md).

For physical native-backend qualification, launch the included cross-platform fixture application.
It exposes known accessible controls, scrolling, a modal dialog, a password field, and a drag target,
then records machine-readable postconditions without storing the password value:

```bash
uv run nimbledesk-fixture \
  --state evidence/fixture-state.json \
  --events evidence/fixture-events.jsonl
```

With the fixture focused and the input-enabled native daemon running, execute the contract and
retain its machine-readable report:

```bash
uv run nimbledesk-fixture-contract \
  --connection-file ~/.nimbledesk/runtime/connection.json \
  --state evidence/fixture-state.json \
  --output evidence/fixture-contract.json
```

To qualify a real DaVinci Resolve installation against an existing plan:

```bash
uv run nimbledesk-davinci-contract \
  --plan output/my-video/edit_plan.json \
  --output evidence/davinci-contract
```

On Windows, retain the standalone bundle's AppContainer isolation report:

```powershell
.\nimbledesk.exe windows-adapter-contract `
  --output evidence\windows-adapter-contract.json
```

Run the cross-platform malicious-adapter contract from each shipped executable and retain its
report for the unified release gate:

```bash
nimbledesk adapter-isolation-contract \
  --target-id macos-arm64 \
  --output evidence/isolation-macos-arm64.json
```

The contract checks declared and undeclared filesystem access, opt-in network access, child-process
denial, the aggregate 512 MiB memory ceiling, timeout cleanup, and worker-host survival. Replace the
target ID with the exact platform target declared in [docs/SUPPORT.md](docs/SUPPORT.md).

Measure event recognition against a labeled corpus with the release qualification command:

```bash
uv run nimbledesk-qualify events \
  --expected corpus/expected-events.json \
  --detected output/detected_events.json \
  --tolerance-seconds 3 \
  --output evidence/event-benchmark.json
```

Run the endurance gate against a separately running daemon. The release run is eight hours by
default. `--input-every` is optional and only moves the pointer briefly before restoring it; use it
on a dedicated test desktop after enabling host and session input.

```bash
uv run nimbledesk-qualify endurance \
  --connection-file ~/.nimbledesk/runtime/connection.json \
  --output evidence/endurance.json
```

After collecting every target report, generate and evaluate the one fail-closed release manifest:

```bash
nimbledesk qualify release-example --output release-evidence/manifest.json
nimbledesk qualify release \
  --manifest release-evidence/manifest.json \
  --output release-evidence/report.json
```

The manifest covers physical desktop and endurance runs, Resolve 20.x, paired media corpora,
malicious-adapter isolation, native package signatures, signed updates, rollback, uninstall, data
retention, and explicit purge. Every installer report must refer to the same version and Ed25519 key.

## Configuration reference

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `NIMBLEDESK_BACKEND` | `simulator` | Select `simulator`, `portable`, or `native`. macOS native mode uses ScreenCaptureKit/Core Graphics, CGEvent, AX, and Retina geometry. Windows native mode uses Windows Graphics Capture, Desktop Duplication, then GDI fallback with per-monitor DPI geometry, SendInput, UIA, and explicit UIPI errors. Linux Wayland uses the XDG ScreenCast/RemoteDesktop portals; X11 adds explicit xdotool window control, xclip/xsel clipboard access, and gtk-launch activation to portable capture/input. Unknown values fail at startup. |
| `NIMBLEDESK_ENABLE_INPUT` | Disabled | Host input gate. Truthy values are `1`, `true`, `yes`, or `on`, ignoring case. |
| `NIMBLEDESK_ENABLE_CLIPBOARD` | Disabled | Additional host gate for native clipboard reads and writes. The session must also set `clipboard_enabled=true`. |
| `NIMBLEDESK_RUNTIME_DIR` | `~/.nimbledesk/runtime` | Directory for `connection.json` and `audit.jsonl`. |
| `NIMBLEDESK_CONNECTION_FILE` | `~/.nimbledesk/runtime/connection.json` | Connection file read by `nimbledesk-mcp`. |
| `NIMBLEDESK_JOB_DIR` | `~/.nimbledesk/jobs` | Durable local creation-console job records. |

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

The unprivileged MCP gateway has no backend imports and no direct desktop authority. Every call crosses an HMAC-authenticated, replay-protected loopback RPC boundary. The signed envelope includes protocol version, caller ID, session context, request ID, creation time, nonce, and an absolute deadline capped at 120 seconds; expired calls and mismatched session envelopes are rejected before dispatch. Studio and MCP use distinct caller IDs. The daemon validates strict protocol models, session state, host and session input gates, action budgets, observation freshness, application/window preconditions, and coordinate bounds. Pausing or stopping releases common modifiers and all mouse buttons.

The audit file redacts typed text and links entries with hashes so tampering is detectable. The model never receives a shell through the desktop protocol. Review the [threat model](docs/THREAT_MODEL.md) and the process-boundary and protocol decisions in [docs/adr](docs/adr).

The full target architecture, including native OS providers, semantic accessibility, game/domain packs, transcription, creative planning, music selection, editor adapters, recovery, evaluation, packaging, and staged release gates, is in [PLAN.md](PLAN.md).

The requirement-by-requirement implementation and qualification status is tracked in
[docs/RELEASE_ACCEPTANCE.md](docs/RELEASE_ACCEPTANCE.md). NimbleDesk is not labeled production
complete while any row there remains partial or requires release qualification.
