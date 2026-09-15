# End-to-End Product and Engineering Plan

## 1. Product definition

### 1.1 Goal

Build a local, model-agnostic desktop automation platform that allows an AI agent to operate arbitrary desktop applications on macOS, Windows, and Linux. The platform must combine semantic UI automation, visual understanding, native application APIs, and mouse/keyboard fallback. It must be reliable enough for multi-step creative workflows such as editing and rendering a video, while remaining general enough to support future applications without changing the core runtime.

### 1.2 Reference outcome

The reference end-to-end scenario is:

1. The user supplies media files and editing instructions.
2. The agent identifies or launches the configured video editor.
3. The agent creates or opens a project.
4. It imports source media.
5. It analyzes the footage, dialogue, sound, motion, shot quality, color, faces, objects, and music.
6. It proposes an evidence-backed creative treatment: story structure, selected takes, cuts, pacing, speed changes, animation, transitions, titles, audio treatment, and color treatment.
7. It constructs and edits a non-destructive timeline from that treatment.
8. It adds supported titles, transitions, animation, speed ramps, stabilization, audio adjustments, effects, and color grades.
9. It saves the project and produces a low-resolution review render.
10. It incorporates requested revisions without rebuilding the edit from scratch.
11. It renders to a user-approved destination and preset.
12. It waits for completion without blocking indefinitely.
13. It validates both the technical output and the intended edit decisions.
14. It reports the output, editable project, creative decision list, and structured action history.

The same runtime must also support workflows in browsers, office applications, IDEs, graphics tools, 3D applications, and proprietary software.

### 1.3 Product principles

- **Model agnostic:** MCP is the first public interface. The core contains no dependency on a particular model provider.
- **Local first:** Screen images, UI trees, credentials, and files remain on the laptop unless the user deliberately configures a remote model or integration.
- **Semantic before visual:** Use accessibility elements or application APIs when available. Use vision and coordinates as fallbacks.
- **Verify every mutation:** State-changing actions are followed by an observation or an application-level verification.
- **Stale-state protection:** An action must identify the observation on which it was planned. The runtime rejects or revalidates actions when the active window or target changed.
- **Capability driven:** Backends report what is available. The runtime never assumes access to screen capture, accessibility, input, or app APIs.
- **Extensible without core edits:** Application support is delivered through versioned adapter packages.
- **Human authority:** The user controls desktop permissions, action policies, and confirmation rules.
- **Evidence-backed creativity:** Creative decisions identify the source signals, editorial goal, confidence, and reversible timeline operation. The system distinguishes objective defects from subjective style choices.
- **Non-destructive editing:** Source media is immutable. The system works through timelines, versions, generated proxies, and reversible application operations.

### 1.4 Initial non-goals

- A hosted remote-desktop product.
- Training a new vision or language model.
- Bypassing operating-system permissions, protected desktops, DRM, CAPTCHAs, or application security boundaries.
- Pixel-perfect support for every application in the first release.
- Running arbitrary shell commands as a generic model tool.
- Transmitting passwords or secrets through the model when an OS credential flow can be used.

## 2. Final system architecture

### 2.1 Process boundaries

The system uses four separately testable components:

1. **Desktop daemon (`desktopd`)**
   - Owns OS screen-capture and accessibility permissions.
   - Collects observations and executes primitive actions.
   - Enforces policy, stale-state checks, rate limits, and emergency stop.
   - Owns the audit log and approval queue.
   - Listens only on a local authenticated transport.

2. **MCP gateway (`desktop-mcp`)**
   - Exposes stable, model-friendly tools over MCP stdio.
   - Converts MCP requests to the daemon protocol.
   - Redacts sensitive fields before returning observations.
   - Has no direct input-control privileges.

3. **Approval console (`desktop-console`)**
   - Cross-platform tray and desktop UI controlled by the human.
   - Shows session state, active application, pending action, risk reason, and recent screenshot.
   - Supports approve once, deny, stop session, pause, and emergency stop.
   - Configures per-application and per-action policies.

4. **Adapter workers**
   - Optional subprocesses implementing high-level application commands.
   - Communicate through a versioned adapter protocol.
   - Declare required permissions and supported commands.
   - Can fail or restart without crashing the desktop daemon.

### 2.2 Local transport

- Bind to loopback only; never `0.0.0.0`.
- The daemon selects an available port and writes connection metadata to a per-user runtime directory protected by OS file permissions/ACLs.
- Each installation receives a random 256-bit secret.
- Clients authenticate every connection using a nonce-based challenge so the secret is not sent as reusable plaintext.
- Messages use JSON-RPC 2.0 with Pydantic-generated JSON Schema during the Python phase.
- Every request carries protocol version, session ID, caller ID, request ID, and deadline.
- Screen images use bounded binary payloads with explicit MIME type, dimensions, content hash, and observation ID.
- A future native daemon may replace Python without changing the protocol.

### 2.3 Core modules

```text
packages/
  protocol/          Versioned schemas and compatibility tests
  daemon/            Sessions, policy, audit, approvals, execution
  perception/        Capture, UI tree, OCR, visual anchors, redaction
  targeting/         Element and visual target resolution
  backends/          Stable OS backend interfaces and composition
  adapter-sdk/       Adapter contracts, manifest, worker host
  mcp-gateway/       MCP tools and response shaping
  console/           Human approval and settings application
backends/
  macos/             ScreenCaptureKit, AX API, CGEvent, windows
  windows/           Graphics Capture, UI Automation, SendInput
  linux/             PipeWire, AT-SPI, Wayland portal, X11 fallback
adapters/
  generic/           Universal desktop primitives
  video-reference/   First full video-editor integration
tests/
  contract/          Backend and adapter conformance
  simulator/         Deterministic fake desktop
  e2e/               OS and application workflows
```

## 3. Domain and protocol contracts

### 3.1 Observation

An observation is immutable and contains:

- Unique `observation_id` and monotonic sequence.
- Capture timestamp and expiry time.
- OS, desktop session, locale, keyboard layout, and permission status.
- Virtual desktop geometry using logical coordinates.
- Per-display physical bounds, logical bounds, scale, rotation, and primary status.
- Active application and focused window.
- Normalized window list with stable IDs where the OS supports them.
- Optional screenshot or requested crops.
- Optional normalized accessibility tree.
- Optional OCR blocks.
- Cursor location and current mouse-button state.
- Capability and warning list.
- Content hashes for screenshot and UI tree.

The screenshot and UI tree share one coordinate transform. Multi-monitor coordinates may be negative. Backend contract tests validate transforms at 100%, 125%, 150%, 200%, rotation, and mixed-DPI configurations.

### 3.2 Targets

Actions do not rely solely on raw coordinates. A target is one of:

- `ElementTarget`: snapshot-bound accessibility element ID.
- `SelectorTarget`: role, name, value, application, window, and ancestor constraints.
- `VisualTarget`: image template, text anchor, or model-supplied bounding box plus expected signature.
- `CoordinateTarget`: explicit logical coordinate and display ID.
- `WindowTarget`: stable window ID or application/title selector.

Target resolution order is application adapter, accessibility element, OCR/text anchor, visual match, then coordinate. A resolved target records confidence, method, bounding box, and evidence.

### 3.3 Actions

Primitive action types include:

- Pointer move, click, double click, right click, press/release, and drag.
- Vertical and horizontal scroll with units and duration.
- Text insertion, key press/release, and hotkey.
- Clipboard read/write when policy allows it.
- Focus, move, resize, minimize, maximize, and close window.
- Launch or activate an application through an allowlisted identifier.
- Wait for duration, element, window, visual change, process state, or adapter event.
- Application command routed to an adapter.

Every action request includes:

- Action and session IDs.
- Source observation ID.
- Expected active application/window.
- Target and arguments.
- Preconditions and postconditions.
- Deadline and maximum retry count.
- Idempotency classification.
- Risk context and optional approval token.

### 3.4 Results

Results distinguish `completed`, `failed`, `rejected`, `confirmation_required`, `stale_observation`, `timed_out`, `cancelled`, and `capability_unavailable`. They include structured error codes, backend evidence, duration, resolved target, postcondition result, and next observation ID when one was captured.

### 3.5 Compatibility

- Protocol versions use semantic versioning.
- Additive optional fields are backward compatible.
- Renames, semantic changes, and removals require a new major version.
- Recorded golden messages validate old gateway/daemon combinations.
- Adapter manifests specify protocol range and runtime requirements.

## 4. Perception and targeting

### 4.1 Capture pipeline

- Capture only requested displays/windows/regions when possible.
- Support PNG for lossless UI text and JPEG/WebP for lower-bandwidth full-screen context.
- Enforce maximum dimensions and bytes before returning data.
- Provide thumbnail plus optional high-resolution crops.
- Detect blank or permission-denied captures explicitly.
- Deduplicate unchanged frames using perceptual and content hashes.
- Support change maps so a model can inspect only changed regions.

### 4.2 Accessibility normalization

Normalize AX, UI Automation, and AT-SPI into a common tree:

- Role, subrole, name, description, value, state, enabled, focused, selected.
- Bounds and supported actions.
- Parent/child relationships.
- Application and window ownership.
- Sensitive flag for password or protected fields.
- Backend-native identity retained privately for execution.

Trees are pruned by relevance and size. Password values are never returned. Large virtualized lists expose visible elements and pagination metadata.

### 4.3 OCR and visual matching

- OCR is an optional local provider behind a port.
- Initial provider: platform OCR where practical, otherwise Tesseract.
- Visual anchors include normalized template matching and text-relative targeting.
- Model-provided boxes must include the source observation ID.
- Before clicking a visual target, recapture a small region and compare its signature.
- Low-confidence matches return alternatives instead of executing.

### 4.4 Recovery

- Detect unexpected dialogs, application switches, spinners, and no-op actions.
- On recoverable mismatch, return a new observation and structured reason to the agent.
- Do not blindly repeat non-idempotent actions.
- Limit retries by action and session budgets.
- Store recovery evidence in the audit record.

## 5. Platform backend plan

### 5.1 Shared backend contract

Each backend must implement permission probing, capability reporting, display enumeration, capture, window enumeration/control, accessibility snapshots/actions, pointer/keyboard input, clipboard access, application activation, and emergency cancellation. Unsupported methods return a capability error rather than silently falling back.

Backend composition allows native implementations for some capabilities and portable fallbacks for others. Selection and fallback are visible in diagnostics and audit logs.

### 5.2 macOS

- ScreenCaptureKit for displays and windows.
- Accessibility API through ApplicationServices for semantic UI.
- CGEvent for pointer, scroll, and keyboard injection.
- NSWorkspace/Core Graphics for applications and windows.
- NSPasteboard for clipboard.
- Dedicated permission diagnostics for Screen Recording and Accessibility.
- Tests cover Retina scaling, Spaces, multiple monitors, full-screen applications, and permission revocation.

### 5.3 Windows

- Windows Graphics Capture for screen/window capture; Desktop Duplication fallback.
- UI Automation for semantic elements and actions.
- SendInput for pointer and keyboard.
- Win32 APIs for window enumeration and focus.
- Windows clipboard APIs.
- Respect User Interface Privilege Isolation: report when an unelevated daemon cannot control an elevated target.
- Tests cover mixed DPI, virtual desktops, Remote Desktop, UAC boundaries, and multiple monitors.

### 5.4 Linux

- AT-SPI for semantic accessibility.
- PipeWire ScreenCast portal for Wayland capture.
- RemoteDesktop/InputCapture portal for Wayland input when supported.
- X11 XTest and capture fallback for X11 sessions.
- EWMH for X11 windows and desktop-portal facilities where available.
- Clipboard providers selected for Wayland or X11.
- Capability reporting includes compositor, portal implementation, and session type.
- Test targets initially include Ubuntu GNOME Wayland, Ubuntu X11, and KDE Plasma Wayland.

## 6. Application adapter system

### 6.1 Adapter manifest

Every adapter declares:

- Stable ID, version, vendor, supported operating systems, and application versions.
- How applications are detected.
- Commands and JSON schemas.
- Required files, network, clipboard, and application API permissions.
- Whether each command is read-only, idempotent, reversible, destructive, or externally visible.
- Progress and cancellation support.
- Health-check and diagnostics methods.

### 6.2 Adapter execution

- Run third-party adapters in subprocess workers.
- Validate arguments and results against the manifest schema.
- Apply timeouts, cancellation, stdout/stderr limits, and crash isolation.
- Adapters receive only explicitly granted paths and capabilities.
- Adapters may request primitive desktop actions through the daemon; they do not inject input directly.
- High-level adapter operations emit progress events and checkpoints.

### 6.3 Video reference adapter

Select one editor that runs on all three platforms and exposes a documented automation API. DaVinci Resolve is the leading reference candidate; confirm the user's actual editor before implementing the adapter.

Initial command surface:

- Detect application/version and connect to the project manager.
- Create/open/save/close project.
- Inspect media pool, bins, timelines, tracks, clips, markers, and render queue.
- Import media with canonical-path validation.
- Create timeline and append/insert clips.
- Trim, split, move, delete, and reorder clips.
- Set clip properties, transitions, titles, and basic audio levels where API support exists.
- Configure render preset and output path.
- Start, monitor, cancel, and validate render.
- Export a machine-readable project/timeline report for verification.

Unsupported editor operations use semantic UI and vision through the daemon, with explicit postconditions.

## 7. Media intelligence and creative planning

Desktop automation answers **how to operate the editor**. Media intelligence answers **what edits to make and why**. This is a separate, model-pluggable subsystem so editing judgment can improve without changing OS automation or editor adapters.

### 7.1 Creative brief

Before analysis, normalize the user's request into a versioned `CreativeBrief` containing audience, publishing destination, target duration/aspect ratios, content type, pace, mood, references, brand rules, mandatory moments, excluded content, preferred speakers, accessibility requirements, captions, titles, logos, fonts, transitions, music, color preferences, delivery specifications, and autonomy level.

Missing preferences receive visible defaults from a user-controlled style profile. Subjective decisions must never be presented as objective defects.

### 7.2 Media ingestion and indexing

Every source asset receives a stable content hash and immutable metadata record. The ingest pipeline performs:

- `ffprobe` inspection of container, codecs, duration, time base, frame rate, variable-frame-rate status, resolution, pixel aspect, rotation, color primaries, transfer function, bit depth, HDR metadata, audio layout, and embedded timecode.
- Proxy and thumbnail generation without modifying the source.
- Keyframe-aware frame sampling and waveform generation.
- Camera/source grouping using metadata, timecode, filenames, and capture timestamps.
- Duplicate and near-duplicate detection.
- Corrupt, offline, truncated, or unsupported media detection.
- Versioned caching by source hash, analyzer version, and configuration.

### 7.3 Time-aligned analysis tracks

All results use rational time rather than floating-point seconds. Each carries start/end time, confidence, analyzer/version, privacy classification, and evidence.

Visual tracks cover shot boundaries, fades, motion and optical flow, camera shake, action peaks, blur, focus, exposure, clipping, noise, faces, gaze, expressions, speaker visibility, objects, activities, OCR, composition, safe areas, color statistics, white balance, skin-tone consistency, contrast, dynamic range, and shot mismatch.

Audio tracks cover word-timestamped transcription, language, optional diarization, active speaker, silence, fillers, repeated takes, false starts, loudness, peaks, clipping, background noise, hum, reverb, music beats/downbeats, tempo, phrase boundaries, energy, and sound events.

Semantic tracks cover topics, claims, questions, answers, demonstrations, emotional beats, narrative importance, alternative takes, continuity entities, candidate hook/setup/development/payoff/call-to-action, and factual dependencies between segments.

Analyzers are independent providers behind typed ports. Local deterministic tools, local ML models, and configured multimodal APIs can contribute tracks.

### 7.4 Editorial decision engine

The engine combines the brief with analysis tracks to produce a declarative `EditPlan`, not direct mouse actions. Each operation contains source ranges, timeline placement, rationale, confidence, alternatives, dependencies, reversible parameters, and measurable postconditions.

It runs three stages:

1. **Story planner:** creates the outline, selects essential moments, detects narrative gaps, and assigns duration budgets.
2. **Rhythm and continuity planner:** chooses cuts and pacing while respecting sentence boundaries, eyelines, screen direction, jump cuts, music phrases, visual continuity, and target duration.
3. **Treatment planner:** proposes speed, animation, reframing, transitions, titles, audio processing, music edits, captions, and color treatment.

Mandatory content, factual coherence, source limitations, licensing, brand rules, target duration, and delivery specifications are hard constraints. Style preferences are optimization goals.

### 7.5 Speed and slow-motion decisions

- Speed up repetitive actions, setup, travel, waiting, screen progress, or low-information sections while preserving comprehension.
- Remove silence or use subtle time compression for speech before applying obvious playback-speed changes.
- Slow brief action peaks, emotional reactions, reveals, or product details when it supports the brief.
- Create speed ramps only when motion and neighboring shots support the transition.
- Preserve normal speed for important dialogue, safety/legal statements, and moments where altered timing could misrepresent events.

Each operation records original and target duration, rate curve, audio behavior, interpolation method, and evidence. Slow motion first checks source frame rate, shutter, and motion quality. When insufficient frames exist, the engine selects a smaller slowdown, requests explicitly enabled optical-flow interpolation, or declines the effect. Generated frames are disclosed in the review report.

### 7.6 Animation and motion-graphics decisions

- Lower thirds appear after a subject is established and remain for a calculated reading duration.
- Callouts track the relevant object or screen region and avoid faces, captions, and platform safe areas.
- Punch-ins and reframes emphasize reactions or details while respecting source resolution.
- Position, scale, opacity, masks, and text use versioned motion templates.
- Captions follow reading-speed, line-length, timing, contrast, and safe-area rules.
- Transitions indicate a real time, location, topic, or rhythmic change; straight cuts remain the default.
- B-roll covers jump cuts only when semantically aligned.

Every animation has a semantic anchor, start/end conditions, collision constraints, and template version. Low tracking confidence creates a review item instead of an automatic effect.

### 7.7 Color decisions

Color work separates technical correction from creative grading:

1. Interpret source color spaces in a configured color-managed pipeline.
2. Correct exposure and white balance conservatively using faces, neutrals, scopes, and neighboring shots.
3. Match cameras and adjacent shots while retaining intentional lighting differences.
4. Apply a creative look only from the brief, an approved reference, or a named style profile.
5. Protect skin tones, brand colors, highlights, shadows, and legal ranges.
6. Handle HDR/SDR transforms explicitly.

Objective corrections include measurable scope evidence. Creative grades provide before/after stills and editable effect parameters.

### 7.8 Audio decisions

- Select clean dialogue takes and preserve natural cadence.
- Reduce noise and hum within configured limits; flag artifacts instead of over-processing.
- Normalize dialogue, music, and effects toward a platform loudness target.
- Duck music using speech regions and smooth curves.
- Cut music at phrase or beat boundaries.
- Preserve room tone, add fades, and prevent edit clicks.
- Generate captions from the final edited dialogue, then validate spelling and timing.

#### Music supervision

The system can propose music for each scene or sequence. It derives a `MusicBrief` from narrative function, emotion, energy curve, pace, dialogue density, duration, audience, platform, brand, cultural context, and requested style. It searches only catalogs granted to the session, such as user-owned files, licensed production libraries, royalty-free catalogs, or an explicitly enabled generative-music provider.

Music assets are indexed by genre, mood, energy, tempo, key, meter, instrumentation, vocal presence, lyrical topic, explicit-content status, structure, edit points, stems, loopability, duration, license, territory, expiry, attribution, and platform restrictions. Audio analysis identifies intro, build, verse, chorus, drop, breakdown, bridge, outro, beats, bars, phrases, and clean cut points.

The recommender returns several candidates with explanations and previews. It selects a suitable range within a track rather than assuming the entire song should be used. A `MusicCue` records track/version, source range, timeline range, musical entry and exit, fades, loop or time-stretch limits, beat alignment, stem mix, dialogue ducking, transition, license evidence, and alternatives.

Selection rules include:

- Match emotional direction and narrative purpose, including deliberate contrast when requested.
- Match edit energy without forcing every visual cut onto a beat.
- Keep lyrics from competing with important dialogue or contradicting the scene.
- Prefer instrumental or stem-reduced sections under dense speech.
- Align major reveals, kills, transitions, and endings to useful phrase-level moments such as a build, drop, hit, or cadence.
- Preserve musical phrasing when shortening or rearranging a track.
- Avoid repeatedly choosing the same popular-sounding structure across a compilation.
- Never treat an unverified internet song as usable merely because it fits creatively.

For gameplay, the music planner can build toward encounters, place a drop near a clutch or multi-kill, reduce music under team communication, restore energy for the reaction, and use quieter tension beds before a reveal. It retains enough flexibility for game audio to carry important evidence.

#### Sound design

The sound-design planner proposes layers by purpose:

- Ambience and room tone for continuity and location.
- Foley for physical actions that need clarity.
- Impacts, sub hits, ticks, and accent sounds for important events.
- Risers, reverses, downlifters, and transitions for builds or scene changes.
- Whooshes only for motivated motion or graphics.
- UI sounds for readable on-screen interactions.
- Designed textures for mood, tension, comedy, or scale.

Every `SoundCue` has a semantic anchor, purpose, source/license, timing tolerance, gain, pan/spatial position, pitch/time treatment, fade, side-chain behavior, and priority. The planner enforces density limits so effects do not become repetitive or obscure source audio. It distinguishes sounds already present in the recording from added design and avoids replacing distinctive game or event audio that makes the moment understandable.

#### Generated music and sound

Optional providers may generate a score, sting, ambience, or effect from the MusicBrief. Generated assets are stored as new immutable sources with provider, prompt, seed when available, license terms, model/version, and disclosure metadata. Generation remains replaceable: the edit plan refers to the musical function and cue boundaries so a licensed track can be substituted later.

#### Audio validation

- Confirm every music and sound asset has a valid source and license record for the destination.
- Detect Content ID or platform restrictions when catalog metadata provides them.
- Validate dialogue intelligibility, integrated loudness, loudness range, true peak, phase, clipping, and channel layout.
- Detect abrupt music edits, broken loops, masked speech, excessive effect density, missing ambience, and unintended silence.
- Compare intended musical events with the executed timeline.
- Produce a cue sheet with track, composer/artist, source, usage range, license, attribution, and generated-content disclosure.

### 7.9 Plan review and revisions

The creative plan has a human-readable treatment, a machine-readable decision graph, and an editor-specific execution plan. The adapter creates a new timeline/version and proxy review render; it never mutates sources. Users can approve a plan or section, lock decisions, request natural-language changes, and choose alternatives. Revisions update only affected unlocked decisions and produce a plan diff.

### 7.10 Creative validation

- Compare the executed timeline with the plan.
- Confirm mandatory and excluded content.
- Detect black/frozen/flash frames, gaps, offline media, clipped titles, missing fonts, and broken effects.
- Re-align speech and validate captions against final audio.
- Validate pacing and section duration budgets.
- Verify speed effects, interpolation, and audio treatment.
- Measure shot color discontinuity, loudness, and true peak.
- Sample frames for overlays, safe areas, and tracking.
- Produce a contact sheet, waveform summary, and issue list.

Creative quality remains partly subjective. The system reports confidence and alternatives and learns only from explicit feedback stored in a user-controlled style profile.

### 7.11 Long-form content mining

The system accepts hours of video, audio, images, or mixed assets and turns them into a searchable `ContentIndex`. Processing is hierarchical so a two-to-three-hour recording does not require expensive frame-by-frame multimodal analysis at full resolution:

1. Inspect metadata and generate low-resolution proxies, waveforms, thumbnails, and scene boundaries.
2. Run inexpensive full-duration signals such as audio energy, transcript, motion, OCR changes, faces, and shot quality.
3. Generate candidate moments from signal peaks and domain detectors.
4. Re-analyze only candidate windows at higher temporal and visual resolution, including context before and after the peak.
5. Merge overlaps, remove near-duplicates, reject technically unusable moments, and retain alternative versions.
6. Rank candidates for the creative brief, then optimize the final set for quality, diversity, story coverage, and duration rather than selecting only the highest raw scores.

Each candidate is a `Moment` containing source ranges, lead-in and aftermath, detected event, participants, transcript, audiovisual signals, technical quality, novelty, confidence, and explanation. The index supports later requests such as “find every clutch,” “make a funny montage,” or “use more moments with my reaction” without reprocessing all media.

### 7.12 Domain and genre detection

The system first classifies the material and may select more than one domain: gameplay, interview/podcast, tutorial/screen recording, sports, vlog/travel, event, product/demo, cinematic footage, music performance, or photo collection. Classification chooses specialized detectors and editing grammar; it does not lock the user into a template.

Domain packs are plugins with event schemas, detectors, ranking features, editing rules, and evaluation fixtures. Examples:

- **Gaming:** kills, multi-kills, grenade kills, headshots, assists, deaths, wins, clutches, narrow escapes, rapid health changes, rare items, objectives, opponent proximity, score changes, kill-feed OCR, announcer cues, controller/input intensity, voice excitement, and teammate reactions.
- **Podcast/interview:** concise insights, surprising statements, complete answers, disagreement, humor, emotional moments, quotable hooks, and clean speaker turns.
- **Sports:** scores, saves, overtakes, celebrations, crowd peaks, replays, and scoreboard changes.
- **Tutorials:** finished result, key steps, visible state changes, mistakes and fixes, before/after, and chapter boundaries.
- **Vlog/event:** emotional peaks, location changes, human reactions, establishing shots, visual beauty, humor, and narrative milestones.
- **Product/demo:** problem, feature proof, benefit, comparison, testimonial, and call to action.

For games, evidence quality is prioritized in this order: official replay/telemetry/event logs when the user supplies them; capture-card or game integration metadata; OCR and UI-state recognition; audiovisual event detection; then general multimodal inference. A `GamePack` describes HUD regions, kill-feed vocabulary, event icons, match phases, scoring, and game-specific montage rules. Unknown games still use generic motion, OCR, audio, speech, and visual analysis with lower confidence.

### 7.13 Highlight ranking and clip construction

A single excitement score is insufficient. Candidate ranking combines:

- Event importance and rarity.
- Player skill or difficulty evidence.
- Stakes and match/story context.
- Reaction strength from voice, face, teammates, crowd, or commentary.
- Visual clarity and whether a viewer can understand what happened.
- Technical quality.
- Novelty compared with already selected clips.
- Fit with requested mood, platform, duration, and audience.
- Strength of hook, escalation, payoff, and ending.

The clip constructor determines the useful lead-in, event, reaction, and resolution. A kill clip may begin before enemy contact, retain enough HUD or context to establish low health/ammo, peak on the kill or survival, and end after the reaction. It avoids cuts that reveal the payoff too early or remove evidence that makes the moment impressive.

The set optimizer prevents ten nearly identical kills from displacing a more varied clutch, funny failure, reaction, or final win. It can produce:

- Individual highlight clips.
- A best-moments compilation.
- A narrative match recap.
- Vertical Shorts/Reels/TikTok variants with tracked reframing.
- Landscape YouTube versions.
- Teasers, trailers, intros, GIFs, and looping clips.
- Multiple hook, duration, caption, music, and thumbnail variants.

### 7.14 Photo and mixed-media creation

Photo ingestion detects duplicates, bursts, blur, exposure, faces, expressions, closed eyes, composition, subjects, locations, OCR, and aesthetic/technical quality. It groups events and selects a diverse set instead of several near-identical images.

Supported creations include:

- Best-photo selects and ranked contact sheets.
- Cropped, leveled, corrected, retouched, and consistently graded collections.
- Thumbnails, posters, banners, covers, and social cards.
- Carousels and photo essays with narrative ordering and captions.
- Slideshows, motion-photo stories, parallax treatments, and mixed photo/video montages.
- Before/after layouts, collages, memes, animated GIFs, and platform-specific variants.

Generative fill, background replacement, object removal, synthetic frames, or generated imagery must be explicitly enabled and labeled in the project report. Identity-sensitive edits and material documentary changes receive higher review requirements.

### 7.15 Watchability and variant evaluation

“Awesome” is converted into inspectable goals rather than a guarantee of virality. The evaluator scores hook clarity, time to first payoff, information density, pacing variation, dead time, narrative completeness, emotional movement, novelty, visual legibility, caption readability, audio intelligibility, platform fit, and loop quality.

The system generates a small number of meaningfully different variants and explains the tradeoffs: faster versus clearer, reaction-led versus action-led, cinematic versus energetic, or short hook versus full context. User selections and optional imported performance analytics update the private style profile. Analytics are normalized for audience size and placement before influencing later rankings; they never silently override the brief.

### 7.16 Provider boundaries

- `MediaAnalyzer` providers emit typed evidence tracks.
- `CreativePlanner` providers emit an `EditPlan`.
- `EditPlanValidator` applies deterministic constraints.
- `EditorAdapter` compiles validated plans into application commands.
- No model provider calls desktop input directly.
- Provider configuration declares whether media, frames, audio, or transcripts leave the machine.
- Remote analysis receives only permitted proxies or crops.
- All model output is schema-validated and treated as untrusted input.

## 8. Agent-facing MCP interface

### 8.1 Tools

- `session_start`, `session_status`, `session_pause`, and `session_stop`.
- `capabilities_get` and `permissions_get`.
- `desktop_observe` with display/window/region and detail options.
- `ui_find` and `target_resolve`.
- `action_execute` for one primitive action.
- `action_batch` only for validated, atomic, low-risk sequences; disabled initially.
- `condition_wait` for bounded event-driven waits.
- `adapters_list`, `adapter_describe`, and `adapter_execute`.
- `media_ingest`, `media_analysis_start`, `media_analysis_status`, and `media_analysis_get`.
- `content_index_query`, `moments_find`, `highlights_rank`, and `clip_set_generate`.
- `domain_packs_list`, `domain_pack_describe`, and `variants_compare`.
- `music_brief_create`, `music_search`, `music_cues_generate`, `sound_design_generate`, and `cue_sheet_export`.
- `creative_brief_create`, `edit_plan_generate`, `edit_plan_validate`, and `edit_plan_compare`.
- `edit_plan_execute`, `edit_review_render`, `edit_revision_apply`, and `render_validate`.
- `approval_status` for a previously returned pending action.
- `audit_query` with redacted summaries.

The gateway descriptions instruct models to observe before actions, avoid stale coordinates, execute one mutation at a time, verify results, and wait through condition tools instead of repeated polling.

### 8.2 Resources

- Session and capability resource.
- Adapter command schemas.
- Current policy summary.
- Recent redacted action history.
- Optional application-specific guidance supplied by adapters.

### 8.3 Response size

- Default observation returns metadata, a scaled overview, relevant UI subtree, and change summary.
- High-resolution crops are requested separately.
- Accessibility nodes use compact stable field names internally but return documented names over MCP.
- Hard limits prevent a full tree or raw video stream from exhausting model context.

### 8.4 Token-budget architecture

Token use is a correctness and cost constraint, not a later optimization. Every model-facing read tool accepts a `ResponseBudget` containing maximum text tokens, image dimensions/detail, result count, tree depth, and whether unchanged fields may be omitted. The gateway reports estimated usage and truncation with continuation or detail handles. It never silently drops fields required to execute a safe action.

The model receives information through progressive disclosure:

1. **Overview:** active application, focused window, cursor, display geometry, capability changes, a small screenshot, and a short change summary.
2. **Relevant detail:** bounded UI matches, OCR blocks, screenshot crops, transcript snippets, or content moments selected by a server-side query.
3. **Exact evidence:** high-resolution crop, complete element properties, media frames, waveform region, or analysis evidence for one selected stable ID.

Stable IDs keep state in the daemon rather than repeating it in model context. Observations, UI nodes, OCR blocks, media assets, moments, decisions, and adapter jobs are retrieved by ID. IDs are session-scoped, expire, and include version/content hashes to prevent stale use.

Desktop observation optimization:

- Hash screenshots, UI trees, windows, and regions; return `unchanged` when appropriate.
- Return changed regions and changed UI nodes relative to a caller-provided observation ID.
- Put the focused window and actionable elements first.
- Prune invisible, decorative, duplicate, empty, and off-screen accessibility nodes by default.
- Cap node count/depth and expose continuation handles.
- Keep native backend properties inside the daemon; return normalized properties only.
- Resolve selectors and rank targets server-side instead of sending a full UI tree to the model.
- Run OCR only on requested or changed regions and deduplicate OCR against accessibility text.
- Default screenshots to bounded dimensions and adaptive JPEG quality; use lossless PNG for text-heavy crops when requested.
- Reuse screenshot crops by content hash across repeated tool calls.

Interaction optimization:

- Use condition/event waits instead of repeated screenshot polling.
- Permit server-side verified high-level adapter commands to replace dozens of primitive UI turns.
- Allow bounded action sequences only with deterministic preconditions and stop behavior.
- Return concise structured failures with a recovery code and the smallest required observation.
- Maintain a session checkpoint summary so a model need not replay the full action history.

Media optimization:

- Analyze full duration first with inexpensive audio, transcript, scene, motion, and thumbnail signals.
- Apply expensive multimodal analysis only to candidate windows.
- Store transcripts and analysis tracks in the content index; queries return top-K moment summaries and evidence handles.
- Retrieve transcript context, frames, or waveform detail only for selected moments.
- Cluster near-duplicates before model ranking.
- Ask the planner to operate on summaries and constraints, then validate source ranges deterministically.
- Cache results by content hash, provider/model version, prompt/schema version, and configuration.

Budget enforcement occurs at daemon payload generation, gateway response shaping, and provider request construction. Tests use provider-specific token estimators where available and conservative byte/dimension estimates otherwise. Telemetry records estimates and actual provider usage separately without storing media or prompt content.

## 9. Safety, privacy, and control

### 9.1 Policy inputs

Policy evaluates caller, session, application, window, action, adapter risk declaration, file paths, text sensitivity, external effect, and prior approval. Rules may be global or application-specific.

Default policy:

- Allow observation after screen permission is granted.
- Require the user to enable input for each session.
- Confirm sending messages, publishing, purchasing, financial actions, credential entry, destructive changes, closing with unsaved work, and final render overwrite.
- Deny password-field extraction, protected desktop control, unapproved shell execution, and paths outside session grants.
- Permit routine reversible editing operations during an approved editing session.

### 9.2 Approval flow

1. Runtime evaluates an exact resolved action.
2. It freezes a pending-action record with expiry and evidence screenshot.
3. Console displays the application, target, effect, and arguments with sensitive data redacted.
4. User approves once, denies, or creates a scoped temporary rule.
5. Runtime issues an action-bound, short-lived approval token.
6. It revalidates target and preconditions immediately before execution.
7. Changed targets invalidate approval.

### 9.3 Emergency controls

- Global pause hotkey configurable during installation.
- Tray-menu stop.
- PyAutoGUI corner fail-safe only as a fallback mechanism.
- Session action/time budgets.
- Daemon watchdog releases pressed keys/buttons after cancellation or crash.
- Startup recovery clears stale sessions and input state.

### 9.4 Audit and retention

- Append-only JSONL or SQLite events with hash chaining.
- Record decisions, resolved targets, backend, timing, errors, and redacted arguments.
- Screenshots are opt-in and receive configurable retention.
- Sensitive accessibility values and typed secrets are never written.
- User can inspect, export, and delete local history.

## 10. Reliability and performance

### 10.1 Service objectives

- Primitive action request overhead under 100 ms excluding intentional motion and OS latency.
- Normal desktop observation under 500 ms at default quality on supported hardware.
- Cancellation recognized within 250 ms for primitive actions and within 1 second for adapter jobs.
- No stuck keys or buttons after handled failure.
- Deterministic rejection of actions using expired observations.
- Daemon survives gateway or adapter crashes.
- Default desktop overviews stay within configured text and image budgets.
- Unchanged desktop responses omit screenshot payloads unless explicitly requested.
- Long-form media queries return bounded top-K summaries rather than complete transcripts or frame sets.

### 10.2 Long-running work

- Rendering and imports return job IDs.
- Progress is event-driven where the adapter API supports it.
- Otherwise use bounded, backoff-based condition checks.
- Jobs persist checkpoints so the console can reconnect.
- Cancellation semantics distinguish requested, acknowledged, and completed.
- System sleep/wake and application restart produce explicit interrupted states.

## 11. Testing strategy

### 11.1 Unit tests

- Schema validation and version compatibility.
- Coordinate transforms and mixed DPI.
- Policy decisions and approval binding.
- Target ranking and stale-state detection.
- Action validation, deadlines, retry rules, and cancellation.
- Redaction and audit serialization.
- Adapter manifest and command validation.
- Rational timeline math, frame boundaries, variable-frame-rate conversion, and drop-frame timecode.
- Creative constraints, duration budgets, locked decisions, and plan revisions.
- Speed curves, source-frame requirements, caption reading speed, safe areas, loudness targets, and color transforms.
- Music structure, beat/phrase alignment, dialogue ducking, cue boundaries, sound-density limits, and license-policy decisions.

### 11.2 Deterministic desktop simulator

Build a fake desktop with displays, windows, UI trees, screenshots, dialogs, animation, and injected failures. It implements the same backend contract. Use it for fast agent-loop and recovery tests without controlling a real machine.

Scenarios include moved buttons, duplicate labels, stale observations, unexpected dialogs, slow applications, permission loss, adapter crash, partial drag, rendering progress, and cancellation.

### 11.3 Backend contract suite

Run the same tests against every backend:

- Capture dimensions and coordinate mapping.
- Point/click/drag/scroll accuracy.
- Keyboard layout and modifier release.
- Window focus and enumeration.
- Accessibility lookup and invocation.
- Clipboard round trips without leaking test content.
- Permission denial and revocation.
- Emergency cancellation.

### 11.4 OS integration tests

Use a purpose-built fixture application exposing known controls and reporting received events. Run it on:

- Latest and previous major macOS on Apple Silicon.
- Windows 11 and a supported Windows 10 image.
- Ubuntu GNOME Wayland and X11.
- KDE Plasma Wayland after the GNOME backend passes.

Test single/multiple displays and common scale combinations. Hardware-only suites run on dedicated workers; simulator and protocol suites run on every change.

### 11.5 Video end-to-end tests

Use deterministic generated media containing timecode, color, and audio cues. The reference workflow produces a short render. Validate with FFmpeg/FFprobe:

- Container and codec are expected.
- Resolution, frame rate, duration, stream count, and audio format match.
- Expected timecode/color samples appear at selected output frames.
- Audio tone segments occur at expected positions and levels.
- Project/timeline report matches the requested clip order and trims.
- Re-running an idempotent workflow does not duplicate imports or timeline items.
- Each removed range, speed change, animation, audio treatment, and color operation has evidence and rationale.
- Low-frame-rate footage is not slowed beyond its configured quality limit without approved interpolation.
- Mandatory story beats remain and excluded segments remain absent.
- Captions meet timing, reading-speed, safe-area, and transcription thresholds.
- Objective color and audio measurements remain inside the delivery profile.
- A request such as “make the opening faster but keep the product explanation” changes only relevant unlocked decisions.

Keep licensed application tests separate from open CI and run them on configured machines.

### 11.6 Long-form highlight and photo tests

- Generate multi-hour synthetic gameplay containing known kills, multi-kills, grenade events, deaths, low-health survivals, duplicate action, quiet travel, reactions, HUD changes, and match outcomes.
- Measure event precision/recall, temporal boundary error, selected-set diversity, context retention, and rejection of false excitement peaks.
- Verify coarse-to-fine analysis yields the same important candidates as the high-resolution reference within agreed tolerances while staying inside processing budgets.
- Test known and unknown game packs, missing HUD, streamer overlays, changed resolution, different languages, and absent telemetry.
- Confirm a clutch clip retains the setup, stakes, action, and reaction rather than only the event frame.
- Use photo corpora with labeled bursts, duplicates, closed eyes, blur, exposure, composition, people, and event groups.
- Verify photo selection balances technical quality, expressions, narrative coverage, and diversity.
- Validate vertical reframing, face/object tracking, titles, thumbnails, carousels, slideshows, and mixed-media outputs.
- Verify scene-aware music ranking, phrase-preserving song edits, clutch/drop alignment, dialogue intelligibility, sound-design density, and cue-sheet accuracy.
- Reject music whose license, territory, platform, or attribution constraints do not match the delivery brief.
- Compare generated variants against watchability constraints without treating a heuristic score as a promise of audience performance.

### 11.7 Security tests

- Reject non-loopback connections.
- Reject missing, replayed, expired, or incorrect authentication challenges.
- Reject forged approval tokens and modified approved actions.
- Validate path traversal and symlink boundaries in adapters.
- Fuzz protocol parsers and adapter results.
- Verify password fields and configured regions are redacted.
- Simulate malicious adapter output and crashes.

## 12. Packaging and operations

### 12.1 Installation artifacts

- macOS signed and notarized `.pkg` or `.dmg` with permission onboarding.
- Windows signed MSIX/MSI with per-user install by default.
- Linux AppImage or distribution packages plus documented portal dependencies.
- Python wheels remain available for developers, not as the primary consumer installation.

### 12.2 Startup and updates

- Per-user service, never root/system service by default.
- Console controls automatic startup.
- Signed updates with rollback to the previous version.
- Database/protocol migration is transactional and backed up.
- Diagnostic bundle excludes screenshots and typed text unless explicitly included.

### 12.3 Observability

- Structured local logs with request IDs and secret redaction.
- Health report for permissions, backends, adapters, transport, and versions.
- Metrics remain local unless the user opts into telemetry.
- Crash reports redact file paths, UI text, and application content.

## 13. Implementation sequence and gates

The sequence delivers vertical slices while preserving the final process and protocol boundaries from the beginning. No temporary public API becomes part of the product.

### Phase 0: Decisions and repository foundation

Deliverables:

- Confirm supported Python baseline and licensing constraints.
- Confirm the first video editor and versions.
- Record architecture decisions for process separation, IPC, schema versioning, plugin isolation, storage, and UI toolkit.
- Establish monorepo layout, formatting, typing, tests, security scanning, and three-OS CI.
- Add threat model and data-classification document.

Exit gate:

- Architecture review accepts component boundaries and protocol lifecycle.
- Empty daemon, gateway, console, and adapter worker connect through authenticated loopback IPC on all three OSes.
- CI packages and tests a hello-world build on all targets.

### Phase 1: Protocol, simulator, sessions, and policy

Deliverables:

- Versioned protocol schemas and generated documentation.
- Deterministic simulator backend.
- Session state machine and capability negotiation.
- Policy engine, exact-action approval records, audit store, deadlines, and cancellation.
- Console screens for session, policy, and pending approval.
- Response-budget schema, stable handle lifecycle, usage estimates, and truncation metadata.

Exit gate:

- Full action lifecycle works against the simulator through MCP.
- Stale, denied, confirmed, timed-out, and cancelled scenarios have deterministic tests.
- Gateway has no direct backend imports or OS privileges.
- Contract tests prove model-facing payloads respect configured budgets.

### Phase 2: Portable observation and input vertical slice

Deliverables:

- Cross-platform fallback capture and input backend.
- Display geometry and coordinate normalization.
- Basic observe, click, drag, scroll, type, hotkey, wait, and emergency stop.
- Permission diagnostics.

Exit gate:

- Fixture-app contract tests pass on macOS, Windows, and Linux X11.
- No pressed input remains after failure/cancellation tests.
- Every attempted input is policy checked and audited.

### Phase 3: Native macOS backend

Deliverables:

- ScreenCaptureKit, AX, CGEvent, window, clipboard, and permission providers.
- Accessibility normalization and semantic element actions.
- Retina/multi-display coordinate transforms.

Exit gate:

- Native macOS backend passes the shared contract suite.
- Semantic fixture workflows complete without coordinate clicks.
- Permission revocation and full-screen/multi-Space behavior are explicit and tested.

### Phase 4: Native Windows backend

Deliverables:

- Graphics Capture, UI Automation, SendInput, Win32 window, clipboard, and permission/elevation providers.
- Mixed-DPI and virtual-desktop handling.

Exit gate:

- Native Windows backend passes shared contract tests.
- UIA workflows complete semantically.
- Elevation boundary produces a clear non-retryable result.

### Phase 5: Native Linux backend

Deliverables:

- AT-SPI semantic provider.
- PipeWire and RemoteDesktop portal support for Wayland.
- X11 capture/input/window fallback.
- GNOME/KDE capability diagnostics.

Exit gate:

- Ubuntu GNOME Wayland and X11 pass supported contract subsets.
- KDE supported subset is documented and tested.
- Missing portal capability fails explicitly without unsafe workarounds.

### Phase 6: Targeting, OCR, and recovery

Deliverables:

- Unified target resolver and confidence scoring.
- Local OCR provider and visual-anchor service.
- Region revalidation, change detection, and stale-state enforcement.
- Recovery classification and bounded retry engine.

Exit gate:

- Simulator perturbation suite meets agreed target-resolution success rate.
- No low-confidence target is executed automatically.
- Unexpected-dialog and moved-control tests recover or stop safely.

### Phase 7: Media intelligence foundation

Deliverables:

- Immutable asset catalog, proxies/cache, rational time model, FFmpeg/FFprobe integration, and analysis-provider SDK.
- Shot, motion, technical-quality, transcription, silence, loudness, music-beat, OCR, color, and semantic tracks.
- Hierarchical long-form analysis, persistent content index, moment model, and candidate-window refinement.
- Domain-pack SDK with gaming and photo reference packs.
- Creative brief and style-profile schemas with local/remote data policy.
- Deterministic generated-media corpus with golden analysis ranges.

Exit gate:

- Analysis is resumable, version-cached, and never modifies source files.
- Golden-corpus results meet per-analyzer accuracy tolerances.
- Every result contains a rational time range, confidence, provenance, and evidence.
- Multi-hour processing resumes after interruption and reuses valid cached work.
- Highlight and photo golden corpora meet event, boundary, diversity, and quality tolerances.
- Remote providers cannot receive media outside session policy.

### Phase 8: Creative planning and validation

Deliverables:

- Story, rhythm/continuity, and treatment planners behind provider ports.
- Declarative decision graph, constraint solver, alternatives, decision locking, and revision engine.
- Speed, animation, color, audio, caption, and delivery rule modules.
- Music catalog index, scene-aware recommender, phrase-level cue planner, sound-design planner, mix rules, and cue-sheet generator.
- Highlight ranker, diverse-set optimizer, context-aware clip constructor, photo-story planner, and variant evaluator.
- Human-readable treatment and machine-readable plan diff.

Exit gate:

- Plans pass source-boundary, timeline, mandatory-content, factual, and delivery constraints before reaching an editor.
- Every creative operation has rationale, evidence, confidence, and reversible parameters.
- Revisions preserve locked decisions and produce a bounded diff.
- Unsupported or low-confidence treatments become review items rather than actions.
- The system can build individual clips, compilations, vertical variants, and photo stories from the content index.

### Phase 9: Adapter SDK and reference video adapter

Deliverables:

- Manifest/schema toolchain, worker host, SDK, examples, and conformance suite.
- Reference video-editor adapter.
- File grants, long-running jobs, progress, checkpoints, and cancellation.
- FFmpeg-based output verifier.
- Compiler from the editor-independent decision graph to adapter commands.
- Non-destructive timeline versioning, review renders, and revision application.

Exit gate:

- Reference video workflow completes from MCP instructions to validated render.
- Adapter crash does not crash or unlock the daemon.
- Re-run and cancellation behaviors pass.
- The generated edit demonstrates content-aware cuts, justified speed treatment, anchored animation, shot matching, dialogue/music mixing, and validated captions.

### Phase 10: Hardening and distribution

Deliverables:

- Signed installers, onboarding, permission repair, updates, and rollback.
- Performance and endurance testing.
- Security review, protocol fuzzing, dependency review, and diagnostic tooling.
- User and adapter-developer documentation.

Exit gate:

- Eight-hour mixed-workload endurance run has no stuck input, unbounded memory growth, or unrecoverable session state.
- Install/upgrade/uninstall tests pass on clean OS images.
- Security findings at release-blocking severity are resolved.

## 14. Release acceptance criteria

Version 1 is complete only when:

1. One MCP configuration works unchanged across supported OSes.
2. The runtime reports accurate capabilities and permissions.
3. An agent can observe and operate the fixture application on all supported targets.
4. Semantic targeting works for accessible applications; visual fallback is observation-bound and revalidated.
5. The reference video workflow produces and validates the expected render.
6. The media engine analyzes visual, audio, speech, motion, color, and semantic content with time-aligned provenance.
7. The planner produces reviewable decisions for selection, pacing, speed, animation, color, audio, and captions.
8. Every creative decision has evidence, confidence, rationale, and reversible parameters.
9. User revisions preserve locked or approved decisions.
10. Multi-hour input is processed hierarchically, can resume, and yields a searchable content index.
11. Domain packs can detect and rank events such as gaming kills, clutches, and narrow survivals using multiple evidence sources.
12. Highlight sets retain context and optimize diversity rather than repeating one event type.
13. Photo workflows select, correct, arrange, and create platform-specific visual outputs non-destructively.
14. “Watchability” results are explainable comparisons, never guarantees of virality.
15. Music and sound cues match scene purpose, preserve dialogue, remain editable, and include license provenance.
16. All state-changing actions pass through policy and audit.
17. Confirmation tokens cannot authorize changed or expired actions.
18. Emergency stop releases input and prevents further actions.
19. Adapter and analysis-provider failures are isolated.
20. Signed packages install, update, roll back, and uninstall cleanly.
21. Documentation covers setup, permissions, model connection, policy, creative briefs, style profiles, domain packs, adapter development, troubleshooting, and privacy.
22. Desktop and media tools enforce response budgets, report truncation, and retrieve detail by stable ID.
23. Token and image usage are measurable per operation without retaining private content.

## 15. Decisions required before implementation

The architecture can proceed with defaults, but these product decisions must be recorded in Phase 0:

- First reference video editor and supported versions.
- Whether the model runs locally, remotely, or both; this controls screenshot redaction and privacy defaults.
- Whether Linux Wayland is required for the first public release or may follow X11.
- Whether application launching and clipboard access are enabled in the first release.
- Distribution expectations: personal developer tool, internal company tool, or public signed product.
- Retention default for screenshots and action history.
- License and policy for third-party adapter packages.
- Whether media analysis is fully local by default and which remote providers may be enabled.
- Initial editing genres, publishing destinations, and brand/style inputs used to benchmark quality.

Recommended defaults are DaVinci Resolve as the cross-platform reference editor, local and remote model support with explicit disclosure, GNOME Wayland plus X11 in version 1, clipboard disabled until a session enables it, a developer preview before signed distribution, metadata-only audit retention, and isolated third-party adapters disabled until explicitly installed.
