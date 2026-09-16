from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from nimbledesk.client import DaemonClient
from nimbledesk.creative.cue_sheet import write_cue_sheet
from nimbledesk.creative.gaming import DEFAULT_GAME_PACK
from nimbledesk.creative.models import CreativeBrief, EditPlan, PlanRevisionRequest
from nimbledesk.creative.music import load_music_catalog, recommend_music
from nimbledesk.creative.planner import plan_scene_music
from nimbledesk.creative.revision import compare_edit_plans
from nimbledesk.creative.sound import load_sound_catalog, plan_sound_cues
from nimbledesk.creative.style import resolve_brief
from nimbledesk.creative.validation import validate_edit_plan
from nimbledesk.creative.verify import verify_render
from nimbledesk.creative.workflow import CreationResult
from nimbledesk.gateway.budget import compact_observation, estimate_text_tokens
from nimbledesk.jobs.service import (
    JOB_SERVICE,
    CreateJobRequest,
    PhotoJobRequest,
    ReviseJobRequest,
    RevisionResult,
)
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    CaptureOptions,
    CoordinateTarget,
    ElementTarget,
    Point,
    RecoveryOptions,
    Rectangle,
    ResponseBudget,
    Target,
    TextTarget,
    VisualTarget,
)

mcp = FastMCP("NimbleDesk")


@lru_cache(maxsize=1)
def client() -> DaemonClient:
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    default_path = Path.home() / ".nimbledesk" / "runtime" / "connection.json"
    path = Path(configured) if configured else default_path
    return DaemonClient.from_file(path)


@mcp.tool()
async def health() -> dict[str, Any]:
    """Check whether the local NimbleDesk daemon is available."""
    return await client().call("health")


@mcp.resource("nimbledesk://capabilities")
async def capability_resource() -> str:
    """Current desktop backend and capability advertisement."""
    return json.dumps(await client().call("health"), sort_keys=True)


@mcp.resource("nimbledesk://sessions/{session_id}")
async def session_resource(session_id: str) -> str:
    """Current state and limits for one session."""
    result = await client().call("session_status", {"session_id": session_id})
    return json.dumps(result, sort_keys=True)


@mcp.resource("nimbledesk://policy")
async def policy_resource() -> str:
    """Current host policy and approval requirements."""
    return json.dumps(await client().call("policy_get"), sort_keys=True)


@mcp.resource("nimbledesk://adapters")
async def adapter_resource() -> str:
    """Installed adapter command schemas and isolation declarations."""
    return json.dumps(await client().call("adapters_list"), sort_keys=True)


@mcp.resource("nimbledesk://sessions/{session_id}/audit")
async def audit_resource(session_id: str) -> str:
    """Recent redacted action history and audit-chain integrity."""
    result = await client().call("audit_query", {"session_id": session_id, "limit": 20})
    return json.dumps(result, sort_keys=True)


@mcp.tool()
async def session_start(
    reason: str,
    input_enabled: bool = False,
    clipboard_enabled: bool = False,
    allowed_applications: list[str] | None = None,
    granted_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Start a bounded desktop-control session for a clear user-provided reason."""
    return await client().call(
        "session_start",
        {
            "reason": reason,
            "config": {
                "input_enabled": input_enabled,
                "clipboard_enabled": clipboard_enabled,
                "allowed_applications": allowed_applications or [],
                "granted_paths": granted_paths or [],
            },
        },
    )


@mcp.tool()
async def session_status(session_id: str) -> dict[str, Any]:
    """Return the current state, limits, and action usage for one session."""
    return await client().call("session_status", {"session_id": session_id})


@mcp.tool()
async def capabilities_get() -> dict[str, Any]:
    """Return the selected backend and currently advertised runtime capabilities."""
    return await client().call("health")


@mcp.tool()
async def permissions_get(session_id: str) -> dict[str, Any]:
    """Probe current OS permission states through a fresh bounded observation."""
    observation = await client().call("desktop_observe", {"session_id": session_id})
    return {
        "observation_id": observation["observation_id"],
        "permissions": observation.get("permissions", {}),
        "capabilities": observation.get("capabilities", []),
        "warnings": observation.get("warnings", [])[:10],
    }


@mcp.tool()
async def desktop_observe(
    session_id: str,
    max_estimated_text_tokens: int = 2_000,
    max_windows: int = 10,
    max_elements: int = 100,
    previous_observation_id: str | None = None,
    continuation_observation_id: str | None = None,
    window_offset: int = 0,
    element_offset: int = 0,
    omit_unchanged: bool = True,
) -> dict[str, Any]:
    """Observe or continue one bounded desktop snapshot with an optional change summary."""
    if previous_observation_id and continuation_observation_id:
        raise ValueError("previous and continuation observation IDs cannot be combined")
    if continuation_observation_id:
        observation = await client().call(
            "desktop_observation_get",
            {
                "session_id": session_id,
                "observation_id": continuation_observation_id,
            },
        )
    else:
        observation = await client().call(
            "desktop_observe",
            {
                "session_id": session_id,
                "previous_observation_id": previous_observation_id,
            },
        )
    budget = ResponseBudget(
        max_estimated_text_tokens=max_estimated_text_tokens,
        max_windows=max_windows,
        max_elements=max_elements,
    )
    return compact_observation(
        observation,
        budget,
        window_offset=window_offset,
        element_offset=element_offset,
        omit_unchanged=omit_unchanged,
    )


@mcp.tool()
async def ui_find(
    session_id: str,
    role: str | None = None,
    name: str | None = None,
    exact_name: bool = False,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Find enabled accessibility elements server-side without returning the full UI tree."""
    if not role and not name:
        raise ValueError("ui_find requires a role or name")
    if not 1 <= maximum_results <= 100 or not 128 <= maximum_tokens <= 100_000:
        raise ValueError("UI result or token budget is outside allowed bounds")
    observation = await client().call("desktop_observe", {"session_id": session_id})
    normalized_role = role.casefold() if role else None
    normalized_name = name.casefold() if name else None
    matches = []
    for element in observation.get("elements", []):
        element_name = str(element.get("name", ""))
        role_matches = (
            normalized_role is None
            or str(element.get("role", "")).casefold() == normalized_role
        )
        name_matches = normalized_name is None or (
            element_name.casefold() == normalized_name
            if exact_name
            else normalized_name in element_name.casefold()
        )
        if role_matches and name_matches and element.get("enabled", True):
            matches.append(
                {
                    key: element.get(key)
                    for key in (
                        "element_id",
                        "window_id",
                        "role",
                        "name",
                        "bounds",
                        "focused",
                        "actions",
                    )
                }
            )
    returned = matches[:maximum_results]
    while returned and estimate_text_tokens(returned) > maximum_tokens:
        returned.pop()
    return {
        "observation_id": observation["observation_id"],
        "matches": returned,
        "usage": {
            "estimated_text_tokens": estimate_text_tokens(returned),
            "maximum_text_tokens": maximum_tokens,
            "total_matches": len(matches),
            "truncated": len(returned) < len(matches),
        },
    }


@mcp.tool()
async def target_resolve(
    session_id: str, observation_id: str, target: Target
) -> dict[str, Any]:
    """Resolve one coordinate, semantic, selector, visual, or OCR target without input."""
    return await client().call(
        "target_resolve",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "target": target.model_dump(mode="json"),
        },
    )


@mcp.tool()
async def action_execute(action: ActionRequest) -> dict[str, Any]:
    """Execute one complete governed action protocol request through the desktop daemon."""
    return await client().call(
        "action_execute", {"action": action.model_dump(mode="json")}
    )


@mcp.tool()
async def condition_wait(
    session_id: str,
    condition_type: Literal[
        "application_active", "window_focused", "element_present", "element_absent"
    ],
    value: str,
    role: str | None = None,
    timeout_seconds: float = 10,
    poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Wait internally for an application, window, or accessible element state change."""
    if not value:
        raise ValueError("condition value cannot be empty")
    if not 0.1 <= timeout_seconds <= 60 or not 0.05 <= poll_interval_seconds <= 2:
        raise ValueError("condition wait timing is outside allowed bounds")
    deadline = monotonic() + timeout_seconds
    observations = 0
    while True:
        observation = await client().call("desktop_observe", {"session_id": session_id})
        observations += 1
        if _condition_matches(observation, condition_type, value, role):
            return {
                "matched": True,
                "condition_type": condition_type,
                "value": value,
                "observation_id": observation["observation_id"],
                "observations": observations,
            }
        remaining = deadline - monotonic()
        if remaining <= 0:
            return {
                "matched": False,
                "condition_type": condition_type,
                "value": value,
                "observation_id": observation["observation_id"],
                "observations": observations,
                "reason": "condition wait timed out",
            }
        await asyncio.sleep(min(poll_interval_seconds, remaining))


@mcp.tool()
async def media_index_open(session_id: str, index_path: str) -> dict[str, Any]:
    """Open a content index within the session's explicit file grants and return a stable handle."""
    return await client().call(
        "media_index_open", {"session_id": session_id, "index_path": index_path}
    )


@mcp.tool()
async def media_index_search(
    session_id: str,
    index_id: str,
    query: str,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Search time-aligned labels, transcript text, and evidence with a bounded response."""
    return await client().call(
        "media_index_search",
        {
            "session_id": session_id,
            "index_id": index_id,
            "query": query,
            "maximum_results": maximum_results,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def media_index_detail(
    session_id: str,
    index_id: str,
    result_id: str,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Retrieve one media search result by stable ID within a text-token budget."""
    return await client().call(
        "media_index_detail",
        {
            "session_id": session_id,
            "index_id": index_id,
            "result_id": result_id,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def content_index_query(
    session_id: str,
    index_id: str,
    query: str,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Query time-aligned media evidence through stable bounded result handles."""
    return await client().call(
        "media_index_search",
        {
            "session_id": session_id,
            "index_id": index_id,
            "query": query,
            "maximum_results": maximum_results,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def moments_find(
    session_id: str,
    index_id: str,
    description: str,
    maximum_results: int = 20,
    maximum_tokens: int = 2_000,
) -> dict[str, Any]:
    """Find transcript, event, motion, audio, or semantic moments matching a description."""
    return await client().call(
        "media_index_search",
        {
            "session_id": session_id,
            "index_id": index_id,
            "query": description,
            "maximum_results": maximum_results,
            "maximum_tokens": maximum_tokens,
        },
    )


@mcp.tool()
async def creative_brief_create(brief: dict[str, Any]) -> dict[str, Any]:
    """Validate and fill defaults for a complete creative brief."""
    return resolve_brief(brief, None).model_dump(mode="json")


@mcp.tool()
async def edit_plan_generate(
    session_id: str,
    source: str,
    output_directory: str,
    brief: CreativeBrief,
    automatic_intelligence: bool = True,
    game_ocr: bool = False,
    transcribe: bool = False,
    vision_provider: str | None = None,
    music_catalog: str | None = None,
    sound_catalog: str | None = None,
) -> dict[str, Any]:
    """Start persistent analysis, highlight ranking, and creative plan generation."""
    return await _start_creation_job(
        session_id,
        source,
        output_directory,
        brief,
        automatic_intelligence=automatic_intelligence,
        game_ocr=game_ocr,
        transcribe=transcribe,
        vision_provider=vision_provider,
        music_catalog=music_catalog,
        sound_catalog=sound_catalog,
        render=False,
        execute_davinci=False,
        render_in_davinci=False,
    )


@mcp.tool()
async def edit_plan_execute(
    session_id: str,
    source: str,
    output_directory: str,
    brief: CreativeBrief,
    automatic_intelligence: bool = True,
    game_ocr: bool = False,
    transcribe: bool = False,
    vision_provider: str | None = None,
    music_catalog: str | None = None,
    sound_catalog: str | None = None,
    execute_davinci: bool = False,
    render_in_davinci: bool = False,
) -> dict[str, Any]:
    """Start the complete persistent plan, render, verify, and optional DaVinci workflow."""
    return await _start_creation_job(
        session_id,
        source,
        output_directory,
        brief,
        automatic_intelligence=automatic_intelligence,
        game_ocr=game_ocr,
        transcribe=transcribe,
        vision_provider=vision_provider,
        music_catalog=music_catalog,
        sound_catalog=sound_catalog,
        render=True,
        execute_davinci=execute_davinci,
        render_in_davinci=render_in_davinci,
    )


@mcp.tool()
async def media_analysis_status(session_id: str, job_id: str) -> dict[str, Any]:
    """Return compact progress and artifact metadata for one persistent creative job."""
    return _creative_job(session_id, job_id)


@mcp.tool()
async def media_ingest(
    session_id: str,
    source: str,
    output_directory: str,
    brief: CreativeBrief,
    automatic_intelligence: bool = True,
    game_ocr: bool = False,
    transcribe: bool = False,
) -> dict[str, Any]:
    """Start persistent media fingerprinting, integrity checks, analysis, and indexing."""
    return await _start_creation_job(
        session_id,
        source,
        output_directory,
        brief,
        automatic_intelligence=automatic_intelligence,
        game_ocr=game_ocr,
        transcribe=transcribe,
        vision_provider=None,
        music_catalog=None,
        sound_catalog=None,
        render=False,
        execute_davinci=False,
        render_in_davinci=False,
    )


@mcp.tool()
async def media_analysis_start(
    session_id: str,
    source: str,
    output_directory: str,
    brief: CreativeBrief,
    automatic_intelligence: bool = True,
    game_ocr: bool = False,
    transcribe: bool = False,
    vision_provider: str | None = None,
) -> dict[str, Any]:
    """Start persistent multimodal analysis, moment detection, ranking, and plan generation."""
    return await _start_creation_job(
        session_id,
        source,
        output_directory,
        brief,
        automatic_intelligence=automatic_intelligence,
        game_ocr=game_ocr,
        transcribe=transcribe,
        vision_provider=vision_provider,
        music_catalog=None,
        sound_catalog=None,
        render=False,
        execute_davinci=False,
        render_in_davinci=False,
    )


@mcp.tool()
async def variants_compare(session_id: str, job_id: str) -> dict[str, Any]:
    """Return the bounded watchability metrics and tradeoffs for a creative job's variants."""
    result = _creative_job(session_id, job_id)
    return {
        "job_id": job_id,
        "status": result["status"],
        "variants": result.get("variant_options", []),
    }


@mcp.tool()
async def highlights_rank(
    session_id: str, job_id: str, maximum_results: int = 20
) -> dict[str, Any]:
    """Return bounded evidence-backed highlight candidates from a completed analysis job."""
    if not 1 <= maximum_results <= 100:
        raise ValueError("highlight result limit must be between 1 and 100")
    manifest = _highlight_manifest(session_id, job_id)
    candidates = manifest.get("candidates", [])
    return {
        "job_id": job_id,
        "highlights": candidates[:maximum_results],
        "available_highlights": len(candidates),
        "truncated": len(candidates) > maximum_results,
    }


@mcp.tool()
async def clip_set_generate(
    session_id: str, job_id: str, maximum_results: int = 20
) -> dict[str, Any]:
    """Return the bounded rendered clip set produced by a completed analysis job."""
    if not 1 <= maximum_results <= 100:
        raise ValueError("clip result limit must be between 1 and 100")
    manifest = _highlight_manifest(session_id, job_id)
    clips = manifest.get("clips", [])
    return {
        "job_id": job_id,
        "clips": clips[:maximum_results],
        "available_clips": len(clips),
        "truncated": len(clips) > maximum_results,
    }


@mcp.tool()
async def domain_packs_list() -> dict[str, Any]:
    """List built-in media-domain analysis packs."""
    return {
        "domain_packs": [
            {
                "pack_id": "generic-shooter",
                "content_kind": "gameplay",
                "event_types": sorted(
                    set(DEFAULT_GAME_PACK.phrases) | set(DEFAULT_GAME_PACK.patterns)
                ),
            },
            {
                "pack_id": "general-editorial",
                "content_kind": "auto",
                "event_types": ["speech", "motion_peak", "audio_peak", "shot_change"],
            },
        ]
    }


@mcp.tool()
async def domain_pack_describe(pack_id: str) -> dict[str, Any]:
    """Describe one built-in domain pack and its deterministic event contract."""
    if pack_id == "generic-shooter":
        return {
            "pack_id": pack_id,
            "content_kind": "gameplay",
            "sample_interval_seconds": DEFAULT_GAME_PACK.sample_interval_seconds,
            "phrases": DEFAULT_GAME_PACK.phrases,
            "patterns": DEFAULT_GAME_PACK.patterns,
            "importance": DEFAULT_GAME_PACK.importance,
            "cooldown_seconds": DEFAULT_GAME_PACK.cooldown_seconds,
            "derived_events": ["multi_kill", "clutch", "narrow_survival"],
        }
    if pack_id == "general-editorial":
        return {
            "pack_id": pack_id,
            "content_kind": "auto",
            "tracks": ["motion", "audio", "color", "shots", "transcript", "semantic"],
            "ranking": "fuses normalized motion, audio, events, diversity, and context",
        }
    raise ValueError("unknown domain pack")


@mcp.tool()
async def music_brief_create(brief: CreativeBrief) -> dict[str, Any]:
    """Create inspectable music-search criteria from a creative brief."""
    target_energy = {"calm": 0.3, "balanced": 0.6, "fast": 0.85}[brief.pace.value]
    return {
        "enabled": brief.music,
        "mood_terms": sorted(
            set(brief.music_style)
            | {term for term in brief.mood.casefold().replace(",", " ").split() if term}
        ),
        "target_energy": target_energy,
        "platform": brief.platform,
        "instrumental_preferred": brief.captions,
        "license_required": True,
    }


@mcp.tool()
async def music_search(
    session_id: str,
    catalog_path: str,
    brief: CreativeBrief,
    required_duration_seconds: float,
    maximum_results: int = 10,
) -> dict[str, Any]:
    """Rank licensed local music for mood, pace, duration, and platform."""
    if not 1 <= maximum_results <= 50:
        raise ValueError("music result limit must be between 1 and 50")
    await _authorize_paths(session_id, [catalog_path])
    assets = load_music_catalog(Path(catalog_path))
    await _authorize_paths(session_id, [str(asset.path) for asset in assets])
    remaining = assets
    selected: list[dict[str, Any]] = []
    excluded: frozenset[Path] = frozenset()
    while remaining and len(selected) < maximum_results:
        asset = recommend_music(
            brief, assets, required_duration_seconds, excluded_paths=excluded
        )
        if asset is None:
            break
        selected.append(
            {
                "asset_id": hashlib.sha256(str(asset.path).encode()).hexdigest()[:24],
                "title": asset.title,
                "artist": asset.artist,
                "duration_seconds": asset.duration_seconds,
                "mood": asset.mood,
                "bpm": asset.bpm,
                "energy": asset.energy,
                "instrumental": asset.instrumental,
                "license": asset.license,
                "attribution": asset.attribution,
            }
        )
        excluded = excluded | {asset.path}
        remaining = tuple(item for item in remaining if item.path not in excluded)
    return {"results": selected, "available_assets": len(assets)}


@mcp.tool()
async def music_cues_generate(
    session_id: str,
    plan_path: str,
    catalog_path: str,
    output_plan_path: str,
) -> dict[str, Any]:
    """Generate scene-aware, beat-aligned licensed music cues and write a revised plan."""
    plan = await _load_granted_plan(session_id, plan_path)
    await _authorize_paths(session_id, [catalog_path, output_plan_path])
    assets = load_music_catalog(Path(catalog_path))
    await _authorize_paths(session_id, [str(asset.path) for asset in assets])
    cues = plan_scene_music(plan.brief, assets, plan.segments, plan.duration_seconds)
    updated = plan.model_copy(
        update={"music_cue": cues[0] if cues else None, "music_cues": cues}
    )
    _write_model(updated, Path(output_plan_path))
    return {
        "output_plan_path": output_plan_path,
        "cue_count": len(cues),
        "cues": [cue.model_dump(mode="json") for cue in cues[:10]],
    }


@mcp.tool()
async def sound_design_generate(
    session_id: str,
    plan_path: str,
    catalog_path: str,
    output_plan_path: str,
) -> dict[str, Any]:
    """Generate sparse licensed sound accents from segment role and evidence."""
    plan = await _load_granted_plan(session_id, plan_path)
    await _authorize_paths(session_id, [catalog_path, output_plan_path])
    assets = load_sound_catalog(Path(catalog_path))
    await _authorize_paths(session_id, [str(asset.path) for asset in assets])
    cues = plan_sound_cues(plan.segments, assets, plan.brief.platform)
    updated = plan.model_copy(update={"sound_cues": cues})
    _write_model(updated, Path(output_plan_path))
    return {"output_plan_path": output_plan_path, "cue_count": len(cues)}


@mcp.tool()
async def cue_sheet_export(
    session_id: str, plan_path: str, output_directory: str
) -> dict[str, Any]:
    """Export JSON and CSV music/sound license provenance for one edit plan."""
    plan = await _load_granted_plan(session_id, plan_path)
    await _authorize_paths(session_id, [output_directory])
    json_path, csv_path = write_cue_sheet(plan, Path(output_directory))
    return {"json_path": str(json_path), "csv_path": str(csv_path)}


@mcp.tool()
async def edit_plan_validate(session_id: str, plan_path: str) -> dict[str, Any]:
    """Validate source ranges, timing, assets, licenses, captions, and delivery constraints."""
    plan = await _load_granted_plan(session_id, plan_path)
    report = validate_edit_plan(plan, probe_media(plan.source_path))
    return report.model_dump(mode="json")


@mcp.tool()
async def edit_plan_compare(
    session_id: str, before_plan_path: str, after_plan_path: str, maximum_changes: int = 100
) -> dict[str, Any]:
    """Return a bounded deterministic field-level comparison of two edit plans."""
    if not 1 <= maximum_changes <= 1_000:
        raise ValueError("plan change limit must be between 1 and 1000")
    before = await _load_granted_plan(session_id, before_plan_path)
    after = await _load_granted_plan(session_id, after_plan_path)
    changes = compare_edit_plans(before, after)
    return {
        "changes": [change.model_dump(mode="json") for change in changes[:maximum_changes]],
        "total_changes": len(changes),
        "truncated": len(changes) > maximum_changes,
    }


@mcp.tool()
async def render_validate(
    session_id: str, plan_path: str, render_path: str, report_path: str
) -> dict[str, Any]:
    """Decode and validate a render against its plan, writing diagnostic evidence."""
    plan = await _load_granted_plan(session_id, plan_path)
    await _authorize_paths(session_id, [render_path, report_path])
    report = verify_render(plan, Path(render_path), Path(report_path))
    return report.model_dump(mode="json")


@mcp.tool()
async def media_analysis_get(session_id: str, job_id: str) -> dict[str, Any]:
    """Return a completed creative job's compact results, variants, and artifacts."""
    result = _creative_job(session_id, job_id)
    if result["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
        raise ValueError("creative job is still running")
    return result


@mcp.tool()
async def media_analysis_cancel(session_id: str, job_id: str) -> dict[str, Any]:
    """Cancel one session-owned creative job and its active child process."""
    _creative_job(session_id, job_id)
    job = JOB_SERVICE.cancel(job_id)
    if job is None:
        raise ValueError("unknown creative job")
    return _compact_job(job.response())


@mcp.tool()
async def edit_revision_apply(
    session_id: str,
    job_id: str,
    changes: PlanRevisionRequest,
    render: bool = True,
    execute_davinci: bool = False,
    render_in_davinci: bool = False,
) -> dict[str, Any]:
    """Create a persistent, validated revision from a completed creative job."""
    parent = JOB_SERVICE.get(job_id)
    if parent is None or parent.state.session_id != session_id:
        raise ValueError("unknown creative job")
    with parent.lock:
        result = parent.state.result
        if parent.state.status != "completed" or not isinstance(
            result, (CreationResult, RevisionResult)
        ):
            raise ValueError("only a completed video creation can be revised")
        plan_path = result.plan_path
        output_root = result.output_directory
    revision = JOB_SERVICE.submit_revision(
        job_id,
        ReviseJobRequest(
            plan=plan_path,
            output_directory=output_root / "revisions" / f"mcp-{os.urandom(8).hex()}",
            changes=changes,
            ffmpeg_render=render,
            davinci=execute_davinci or render_in_davinci,
            davinci_render=render_in_davinci,
        ),
        session_id=session_id,
    )
    return _compact_job(revision.response())


@mcp.tool()
async def edit_review_render(session_id: str, job_id: str) -> dict[str, Any]:
    """Render and verify a review MP4 from a completed planning job."""
    result: dict[str, Any] = await edit_revision_apply(
        session_id, job_id, PlanRevisionRequest(), render=True
    )
    return result


@mcp.tool()
async def photo_creation_start(
    session_id: str,
    source: str,
    output_directory: str,
    title: str = "Photo story",
    platform: Literal["youtube", "instagram", "tiktok"] = "instagram",
    count: int = 20,
    create_slideshow: bool = True,
    create_social_assets: bool = True,
    create_animated_gif: bool = False,
) -> dict[str, Any]:
    """Start a persistent photo selection, correction, and social-asset job."""
    await _authorize_paths(session_id, [source, output_directory])
    job = JOB_SERVICE.submit_photo(
        PhotoJobRequest(
            source=Path(source),
            output_directory=Path(output_directory),
            title=title,
            platform=platform,
            count=count,
            create_slideshow=create_slideshow,
            create_social_assets=create_social_assets,
            create_animated_gif=create_animated_gif,
        ),
        session_id=session_id,
    )
    return _compact_job(job.response())


@mcp.tool()
async def take_screenshot(
    session_id: str,
    observation_id: str,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    image_format: Literal["png", "jpeg"] = "jpeg",
    max_width: int = 1280,
    max_height: int = 800,
    jpeg_quality: int = 75,
) -> Image:
    """Capture the screen for the latest observation, optionally cropped to a region."""
    region_values = (left, top, width, height)
    if any(value is not None for value in region_values) and not all(
        value is not None for value in region_values
    ):
        raise ValueError("left, top, width, and height must be provided together")
    region = None
    if all(value is not None for value in region_values):
        region = {"left": left, "top": top, "width": width, "height": height}
    options = CaptureOptions(
        image_format=image_format,
        max_width=max_width,
        max_height=max_height,
        jpeg_quality=jpeg_quality,
    )
    capture = await client().call(
        "screen_capture",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "region": region,
            "options": options.model_dump(mode="json"),
        },
    )
    return Image(
        data=base64.b64decode(capture["data_base64"]),
        format="jpeg" if capture["mime_type"] == "image/jpeg" else "png",
    )


@mcp.tool()
async def capture_region_signature(
    session_id: str,
    observation_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Capture a lossless target crop and return its execution signature without image bytes."""
    bounds = {"left": left, "top": top, "width": width, "height": height}
    capture = await client().call(
        "screen_capture",
        {
            "session_id": session_id,
            "observation_id": observation_id,
            "region": bounds,
            "options": CaptureOptions(
                image_format="png",
                max_width=max(64, min(4096, width)),
                max_height=max(64, min(4096, height)),
            ).model_dump(mode="json"),
        },
    )
    return {
        "observation_id": observation_id,
        "bounds": bounds,
        "signature": capture["sha256"],
        "width": capture["width"],
        "height": capture["height"],
        "usage": capture.get("usage"),
    }


@mcp.tool()
async def click(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    button: Literal["left", "middle", "right"] = "left",
    clicks: int = 1,
    interval: float = 0.1,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Click a coordinate from the latest observation after stale-state validation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": button, "clicks": clicks, "interval": interval},
    )


@mcp.tool()
async def click_element(
    session_id: str,
    observation_id: str,
    element_id: str,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Invoke a semantic UI element from the latest accessibility observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=ElementTarget(observation_id=observation_id, element_id=element_id),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def click_visual(
    session_id: str,
    observation_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
    signature: str,
    confidence: float,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Click a visual crop only if a lossless recapture still has the supplied signature."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=VisualTarget(
            observation_id=observation_id,
            bounds=Rectangle(left=left, top=top, width=width, height=height),
            signature=signature,
            confidence=confidence,
        ),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def click_text(
    session_id: str,
    observation_id: str,
    text: str,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    exact: bool = False,
    minimum_confidence: float = 0.75,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recover_if_stale: bool = True,
) -> dict[str, Any]:
    """Locate visible text with local OCR and click only an unambiguous fresh match."""
    values = (left, top, width, height)
    if any(value is not None for value in values) and not all(
        value is not None for value in values
    ):
        raise ValueError("left, top, width, and height must be provided together")
    bounds = None
    if all(value is not None for value in values):
        assert left is not None and top is not None
        assert width is not None and height is not None
        bounds = Rectangle(left=left, top=top, width=width, height=height)
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.CLICK,
        target=TextTarget(
            observation_id=observation_id,
            text=text,
            search_bounds=bounds,
            exact=exact,
            minimum_confidence=minimum_confidence,
        ),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"button": "left", "clicks": 1},
        recovery=RecoveryOptions(max_reobservations=1 if recover_if_stale else 0),
    )


@mcp.tool()
async def move_mouse(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    duration: float = 0.2,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Move the pointer to a coordinate from the latest observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.MOVE_POINTER,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"duration": duration},
    )


@mcp.tool()
async def drag_to(
    session_id: str,
    observation_id: str,
    x: int,
    y: int,
    duration: float = 0.5,
    button: Literal["left", "middle", "right"] = "left",
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Drag from the current pointer position to a coordinate."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.DRAG,
        target=CoordinateTarget(point=Point(x=x, y=y)),
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"duration": duration, "button": button},
    )


@mcp.tool()
async def scroll(
    session_id: str,
    observation_id: str,
    amount: int,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Scroll vertically; positive values move up and negative values move down."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.SCROLL,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"amount": amount},
    )


@mcp.tool()
async def type_text(
    session_id: str,
    observation_id: str,
    text: str,
    interval: float = 0.02,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Type text into the currently focused control."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.TYPE_TEXT,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"text": text, "interval": interval},
    )


@mcp.tool()
async def press_key(
    session_id: str,
    observation_id: str,
    key: str,
    presses: int = 1,
    interval: float = 0.1,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Press a named key such as enter, tab, escape, backspace, or f5."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.PRESS_KEY,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"key": key, "presses": presses, "interval": interval},
    )


@mcp.tool()
async def hotkey(
    session_id: str,
    observation_id: str,
    keys: list[str],
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
) -> dict[str, Any]:
    """Press a key combination such as ['command', 's'] or ['ctrl', 's']."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.HOTKEY,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        arguments={"keys": keys},
    )


@mcp.tool()
async def focus_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    expected_application_id: str | None = None,
) -> dict[str, Any]:
    """Focus a native window from the latest observation."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.FOCUS_WINDOW,
        expected_application_id=expected_application_id,
        arguments={"window_id": window_id},
    )


@mcp.tool()
async def move_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    left: int,
    top: int,
) -> dict[str, Any]:
    """Move a native window to logical desktop coordinates."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MOVE_WINDOW, {"left": left, "top": top}
    )


@mcp.tool()
async def resize_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Resize a native window in logical desktop units."""
    return await _window_action(
        session_id,
        observation_id,
        window_id,
        ActionKind.RESIZE_WINDOW,
        {"width": width, "height": height},
    )


@mcp.tool()
async def minimize_window(
    session_id: str, observation_id: str, window_id: str
) -> dict[str, Any]:
    """Minimize a native window."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MINIMIZE_WINDOW
    )


@mcp.tool()
async def maximize_window(
    session_id: str, observation_id: str, window_id: str
) -> dict[str, Any]:
    """Maximize a native window."""
    return await _window_action(
        session_id, observation_id, window_id, ActionKind.MAXIMIZE_WINDOW
    )


@mcp.tool()
async def close_window(
    session_id: str,
    observation_id: str,
    window_id: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Request a native window close after exact-action approval."""
    return await _window_action(
        session_id,
        observation_id,
        window_id,
        ActionKind.CLOSE_WINDOW,
        approval_token=approval_token,
    )


@mcp.tool()
async def read_clipboard(
    session_id: str,
    observation_id: str,
    maximum_characters: int = 10_000,
) -> dict[str, Any]:
    """Read bounded text from the clipboard when host and session access are enabled."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.READ_CLIPBOARD,
        arguments={"maximum_characters": maximum_characters},
    )


@mcp.tool()
async def write_clipboard(
    session_id: str,
    observation_id: str,
    text: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Write clipboard text after an exact-action approval."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.WRITE_CLIPBOARD,
        arguments={"text": text},
        approval_token=approval_token,
    )


@mcp.tool()
async def launch_application(
    session_id: str,
    observation_id: str,
    application_id: str,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Launch an allowlisted bundle, executable, or AppUserModel ID after approval."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.LAUNCH_APPLICATION,
        arguments={"application_id": application_id},
        approval_token=approval_token,
    )


@mcp.tool()
async def wait(session_id: str, seconds: float) -> dict[str, Any]:
    """Wait up to ten seconds for an application or animation to settle."""
    return await _execute_action(
        session_id=session_id,
        observation_id=None,
        kind=ActionKind.WAIT,
        arguments={"seconds": seconds},
    )


@mcp.tool()
async def application_command(
    session_id: str,
    observation_id: str,
    adapter_id: str,
    command: str,
    arguments: dict[str, Any],
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Run an installed adapter command; first call returns an exact-action approval request."""
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={"adapter_id": adapter_id, "command": command, "arguments": arguments},
        approval_token=approval_token,
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


@mcp.tool()
async def adapters_list() -> dict[str, Any]:
    """List installed application adapters and their bounded command contracts."""
    return await client().call("adapters_list")


@mcp.tool()
async def adapter_describe(adapter_id: str) -> dict[str, Any]:
    """Describe one installed adapter without exposing its local package path."""
    response = await client().call("adapters_list")
    adapter = next(
        (item for item in response.get("adapters", []) if item.get("adapter_id") == adapter_id),
        None,
    )
    if not isinstance(adapter, dict):
        raise ValueError("adapter is not installed")
    return dict(adapter)


@mcp.tool()
async def approval_status(approval_id: str) -> dict[str, Any]:
    """Check whether a human approved or rejected a pending exact action."""
    return await client().call("approval_status", {"approval_id": approval_id})


@mcp.tool()
async def audit_query(session_id: str, limit: int = 20) -> dict[str, Any]:
    """Return bounded redacted action summaries for one session."""
    return await client().call("audit_query", {"session_id": session_id, "limit": limit})


@mcp.tool()
async def session_pause(session_id: str) -> dict[str, Any]:
    """Pause a session and release any held desktop input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "paused"},
    )


@mcp.tool()
async def session_resume(session_id: str) -> dict[str, Any]:
    """Resume a paused session without changing its original permissions or budgets."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "active"},
    )


@mcp.tool()
async def session_stop(session_id: str) -> dict[str, Any]:
    """Stop a desktop-control session permanently and release input."""
    return await client().call(
        "session_set_state",
        {"session_id": session_id, "state": "stopped"},
    )


async def _execute_action(
    session_id: str,
    observation_id: str | None,
    kind: ActionKind,
    arguments: dict[str, Any],
    target: Target | None = None,
    expected_application_id: str | None = None,
    expected_window_id: str | None = None,
    recovery: RecoveryOptions | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    action = ActionRequest(
        session_id=session_id,
        source_observation_id=observation_id,
        expected_application_id=expected_application_id,
        expected_window_id=expected_window_id,
        kind=kind,
        target=target,
        arguments=arguments,
        recovery=recovery or RecoveryOptions(),
        approval_token=approval_token,
    )
    return await client().call("action_execute", {"action": action.model_dump(mode="json")})


async def _start_creation_job(
    session_id: str,
    source: str,
    output_directory: str,
    brief: CreativeBrief,
    *,
    automatic_intelligence: bool,
    game_ocr: bool,
    transcribe: bool,
    vision_provider: str | None,
    music_catalog: str | None,
    sound_catalog: str | None,
    render: bool,
    execute_davinci: bool,
    render_in_davinci: bool,
) -> dict[str, Any]:
    paths = [source, output_directory]
    paths.extend(
        path
        for path in (vision_provider, music_catalog, sound_catalog)
        if path is not None
    )
    await _authorize_paths(session_id, paths)
    job = JOB_SERVICE.submit(
        CreateJobRequest(
            source=Path(source),
            output_directory=Path(output_directory),
            brief=brief,
            automatic_intelligence=automatic_intelligence,
            game_ocr=game_ocr,
            vision_provider=Path(vision_provider) if vision_provider else None,
            transcribe=transcribe,
            music_catalog=Path(music_catalog) if music_catalog else None,
            sound_catalog=Path(sound_catalog) if sound_catalog else None,
            ffmpeg_render=render,
            davinci=execute_davinci or render_in_davinci,
            davinci_render=render_in_davinci,
        ),
        session_id=session_id,
    )
    return _compact_job(job.response())


async def _authorize_paths(session_id: str, paths: list[str]) -> None:
    await client().call("paths_authorize", {"session_id": session_id, "paths": paths})


async def _load_granted_plan(session_id: str, plan_path: str) -> EditPlan:
    await _authorize_paths(session_id, [plan_path])
    plan = EditPlan.model_validate_json(Path(plan_path).read_text(encoding="utf-8"))
    embedded_paths = {str(plan.source_path)}
    embedded_paths.update(str(segment.source_path) for segment in plan.segments)
    embedded_paths.update(str(cue.asset.path) for cue in plan.all_music_cues)
    embedded_paths.update(str(cue.asset.path) for cue in plan.sound_cues)
    if plan.brief.brand.logo_path is not None:
        embedded_paths.add(str(plan.brief.brand.logo_path))
    if plan.brief.brand.font_path is not None:
        embedded_paths.add(str(plan.brief.brand.font_path))
    await _authorize_paths(session_id, sorted(embedded_paths))
    return plan


def _write_model(model: EditPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _creative_job(session_id: str, job_id: str) -> dict[str, Any]:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise ValueError("unknown creative job")
    with job.lock:
        if job.state.session_id != session_id:
            raise ValueError("unknown creative job")
    return _compact_job(job.response())


def _highlight_manifest(session_id: str, job_id: str) -> dict[str, Any]:
    job = JOB_SERVICE.get(job_id)
    if job is None:
        raise ValueError("unknown creative job")
    with job.lock:
        result = job.state.result
        if (
            job.state.session_id != session_id
            or job.state.status != "completed"
            or not isinstance(result, CreationResult)
        ):
            raise ValueError("completed session-owned analysis job is required")
        path = result.output_directory / "analysis" / "highlights.json"
    if not path.is_file():
        raise ValueError("highlight manifest is unavailable")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("highlight manifest is invalid")
    return payload


def _compact_job(response: dict[str, Any]) -> dict[str, Any]:
    result = response.pop("result", None)
    response.pop("request", None)
    response["variant_options"] = response.get("variant_options", [])[:3]
    response["automatic_capabilities"] = response.get("automatic_capabilities", [])[:10]
    if isinstance(result, dict):
        output_keys = (
            "output_directory",
            "content_index_path",
            "plan_path",
            "validation_path",
            "timeline_path",
            "render_path",
            "transcript_path",
            "events_path",
            "vision_analysis_path",
            "cue_sheet_path",
            "cue_sheet_csv_path",
            "variant_comparison_path",
            "verification_path",
            "davinci_verification_path",
            "contact_sheet",
            "slideshow",
            "thumbnail",
            "poster",
            "collage",
            "animated_gif",
        )
        response["outputs"] = {
            key: result[key] for key in output_keys if result.get(key) is not None
        }
        plan = result.get("plan")
        if isinstance(plan, dict):
            response["plan_summary"] = {
                "duration_seconds": plan.get("duration_seconds"),
                "segment_count": len(plan.get("segments", [])),
                "review_items": plan.get("review_items", [])[:20],
            }
    return response


async def _window_action(
    session_id: str,
    observation_id: str,
    window_id: str,
    kind: ActionKind,
    arguments: dict[str, Any] | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=kind,
        arguments={"window_id": window_id, **(arguments or {})},
        expected_window_id=window_id,
        approval_token=approval_token,
    )


@mcp.tool()
async def adapter_execute(
    session_id: str,
    observation_id: str,
    adapter_id: str,
    command: str,
    arguments: dict[str, Any],
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Execute one declared adapter command through policy, approval, isolation, and audit."""
    return await _execute_action(
        session_id=session_id,
        observation_id=observation_id,
        kind=ActionKind.APP_COMMAND,
        arguments={
            "adapter_id": adapter_id,
            "command": command,
            "arguments": arguments,
        },
        approval_token=approval_token,
    )


def _condition_matches(
    observation: dict[str, Any],
    condition_type: str,
    value: str,
    role: str | None,
) -> bool:
    if condition_type == "application_active":
        return observation.get("active_application_id") == value
    if condition_type == "window_focused":
        return observation.get("focused_window_id") == value or any(
            window.get("focused") and value.casefold() in str(window.get("title", "")).casefold()
            for window in observation.get("windows", [])
        )
    matching_element = any(
        value.casefold() in str(element.get("name", "")).casefold()
        and (role is None or str(element.get("role", "")).casefold() == role.casefold())
        for element in observation.get("elements", [])
    )
    if condition_type == "element_present":
        return matching_element
    if condition_type == "element_absent":
        return not matching_element
    raise ValueError(f"unknown condition type: {condition_type}")


def main() -> None:
    mcp.run(transport="stdio")
