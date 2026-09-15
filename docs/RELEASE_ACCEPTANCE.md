# Release acceptance evidence

This file tracks the 23 version-one acceptance criteria in `PLAN.md`. A deterministic unit or
integration test proves code behavior. It does not prove that a physical operating system granted
desktop permissions, that a particular DaVinci Resolve build completed a render, that a long media
corpus meets a quality threshold, or that an installer was signed by an external authority.

Status meanings:

- **Implemented**: the workflow exists and has direct automated evidence.
- **Qualification required**: implementation exists, but the required physical or representative
  release run has not been recorded.
- **Partial**: a named implementation requirement is still missing.
- **External release gate**: code exists, but credentials or external release infrastructure are
  required to produce the evidence.

| # | Status | Direct evidence | Remaining evidence or implementation |
|---|---|---|---|
| 1 | Qualification required | `.github/workflows/ci.yml` runs one source tree and MCP protocol on macOS, Windows, and Linux. `tests/test_smoke_test.py` verifies the unchanged client flow. | Record installed physical-machine smoke reports for all supported OSes. |
| 2 | Qualification required | `src/nimbledesk/diagnostics.py`, `src/nimbledesk/daemon/server.py`, `tests/test_diagnostics.py`, and backend tests verify capability and permission reporting. | Compare reports with permissions granted, denied, and revoked on physical targets. |
| 3 | Qualification required | `src/nimbledesk/testing/fixture_app.py` provides a state-file and event-log oracle. `nimbledesk fixture-contract` runs bounded capture, pointer restoration, keyboard input, and named semantic actions while checking real postconditions. Fixture and contract tests verify the harness. | Run and retain physical contract reports on macOS, Windows, GNOME Wayland/X11, and KDE. |
| 4 | Qualification required | `src/nimbledesk/backends/semantic.py`, `src/nimbledesk/perception/ocr.py`, and `src/nimbledesk/daemon/runtime.py`; `tests/test_native_backend.py`, `tests/test_ocr.py`, and `tests/test_runtime.py` cover bound semantic, OCR, visual-signature, and stale-target behavior. | Run semantic and visual recovery workflows in real applications on every supported desktop. |
| 5 | Qualification required | `src/nimbledesk/creative/workflow.py`, `render.py`, `davinci.py`, and `verify.py`; `tests/test_creation_workflow.py`, `tests/test_davinci_adapter.py`, and `tests/test_verify.py` cover plan-to-render and adapter contracts. | Import, render, reopen, revise, and cancel with the declared physical DaVinci versions. |
| 6 | Implemented | `src/nimbledesk/analysis/index.py`, transcription, OCR, vision, motion/audio/color/shot tracks, and `tests/test_content_index.py`, `tests/test_transcription.py`, `tests/test_vision.py`, and `tests/test_media_pipeline.py`. | Representative-corpus tolerances remain part of criteria 10 and 11. |
| 7 | Implemented | `src/nimbledesk/creative/planner.py` produces selection, speed, visual, color, audio, caption, and delivery decisions; `tests/test_creative_planner.py` and the end-to-end creation test inspect them. | None at the code-contract level. |
| 8 | Implemented | Creative models require evidence/confidence on selected moments and rationale plus reversible parameters on speed, visual, music, and sound decisions. Plan validation and planner tests enforce the contract. | Human review still determines whether a rationale is creatively persuasive. |
| 9 | Implemented | `src/nimbledesk/creative/revision.py` and `tests/test_plan_revision.py` verify locked-decision preservation and bounded plan diffs. | None at the code-contract level. |
| 10 | Qualification required | `src/nimbledesk/analysis/index.py` uses adaptive sampling, atomic per-track caches, content/configuration hashes, and stable asset IDs; `tests/test_content_index.py` verifies selective cache reuse. | Run interruption/resume and search checks on representative two-to-eight-hour sources. |
| 11 | Qualification required | `src/nimbledesk/creative/gaming.py` and `vision.py` fuse OCR, regex, temporal, audiovisual, and semantic evidence. `tests/test_gaming.py` and `tests/test_vision.py` verify kills, multi-kills, clutches, and confidence gates. `nimbledesk-qualify events` measures precision/recall. | Run labeled game/domain corpora and record agreed thresholds. |
| 12 | Implemented | `src/nimbledesk/media/ranking.py` retains lead-in/aftermath, separates peaks, and promotes unseen event types. `tests/test_media_ranking.py` verifies diversity. `nimbledesk-qualify ranking` measures ranking, coverage, and context retention. | Corpus scores are release-quality evidence rather than an implementation prerequisite. |
| 13 | Implemented | `src/nimbledesk/media/photos.py`, `photo_cli.py`, and `tests/test_photos.py` verify non-destructive selection, deduplication, correction, arrangement, and platform outputs. | Human review remains appropriate for taste. |
| 14 | Implemented | `src/nimbledesk/creative/variants.py` emits explainable comparisons and explicitly disclaims virality prediction; `tests/test_variants.py` verifies the variants and disclaimer. | None. |
| 15 | Implemented | Music indexing/selection, beat-aware cue planning, dialogue ducking, sound design, mastering, and cue-sheet license provenance are covered by `tests/test_music_index.py`, `tests/test_sound.py`, `tests/test_cue_sheet.py`, and render tests. | Licensed production catalogs are user-provided assets. |
| 16 | Implemented | `src/nimbledesk/daemon/policy.py`, `runtime.py`, and `audit.py`; `tests/test_policy.py`, `tests/test_runtime.py`, and adapter approval tests verify policy and audit routing. | Physical input runs are covered by criterion 3. |
| 17 | Implemented | `src/nimbledesk/daemon/approvals.py` binds tokens to canonical exact actions, expiry, and one-time use; runtime, adapter, and RPC tests cover changed, expired, and replayed requests. | None. |
| 18 | Implemented | Runtime emergency stop, portable input release, session stop, and cancellable media/editor processes are covered by `tests/test_cancellation.py`, `tests/test_runtime.py`, and `tests/test_portable_backend.py`. | The eight-hour physical input run remains criterion 10's release qualification. |
| 19 | Partial | DaVinci, semantic vision, and application adapters execute in bounded subprocesses. macOS third-party adapters use a tested deny-by-default sandbox; Linux requires Bubblewrap and disables networking by default. | Implement an equivalent Windows restricted-token/AppContainer boundary and physically exercise malicious-provider fixtures on every OS. |
| 20 | External release gate | `.github/workflows/release.yml`, `packaging/`, `src/nimbledesk/update.py`, `tests/test_update.py`, and installer CI cover signed metadata, verified download, transactional switch, rollback, and clean install/uninstall logic. | Supply Apple, Windows, and Ed25519 release credentials and record a tagged signed install/update/rollback/uninstall matrix. |
| 21 | Implemented | `README.md`, `docs/ADAPTERS.md`, `docs/DISTRIBUTION.md`, `docs/TESTING.md`, and `docs/THREAT_MODEL.md` cover setup, permissions, model connection, policy, briefs, profiles, domain packs, adapter development, troubleshooting, privacy, testing, and distribution. | Keep version support and screenshots current for each release. |
| 22 | Implemented | Desktop observations and images enforce response budgets and report truncation/usage. `media_index_open`, `media_index_search`, and `media_index_detail` use explicit session file grants, content-derived session handles, stable result IDs, bounded results, and reported token usage. Tests cover desktop and media budgets. | None at the code-contract level. |
| 23 | Implemented | Desktop capture reports exact bytes/pixels and estimated tiles/tokens. Semantic vision analysis records measured sheet bytes/tiles, conservative image-token estimates, and provider-reported input/output tokens without retaining credentials. Tests cover both paths. | Provider estimates remain labeled because billing formulas vary. |

## Required release evidence

The implementation cannot truthfully be labeled version-one production complete until these
artifacts exist:

1. Physical fixture reports for macOS, Windows, GNOME Wayland/X11, and the supported KDE subset.
2. Physical DaVinci import/render/reopen/revision/cancellation reports for supported versions.
3. An eight-hour mixed-workload endurance report.
4. Event and ranking reports from representative multi-hour labeled media corpora.
5. Signed public installers plus install/update/rollback/uninstall reports.
6. Windows third-party adapter isolation.

Use the exact qualification commands in `docs/TESTING.md`. Store generated reports outside the
repository when they contain private machine or media details; publish sanitized summaries with the
release.
