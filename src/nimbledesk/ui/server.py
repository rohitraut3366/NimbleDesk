# ruff: noqa: E501
from __future__ import annotations

import argparse
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from nimbledesk.client import DaemonClient
from nimbledesk.creative.automatic import resolve_automatic_intelligence
from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.style import StyleProfile, StyleProfileStore, resolve_brief
from nimbledesk.jobs.service import (
    CreateJobRequest,
    PhotoJobRequest,
    ReviseJobRequest,
    RevisionSubmission,
    VariantSelectionRequest,
)

STYLE_PROFILE_STORE = StyleProfileStore()

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class LoopbackGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = urlsplit("//" + request.headers.get("host", "")).hostname
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"error": "invalid Studio host"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            origin_host = urlsplit(origin).hostname if origin else None
            if origin_host is not None and origin_host not in LOOPBACK_HOSTS:
                return JSONResponse({"error": "cross-origin Studio request rejected"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "cross-site Studio request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["content-security-policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response


def daemon_client() -> DaemonClient:
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    configured_runtime = os.getenv("NIMBLEDESK_RUNTIME_DIR")
    if configured:
        connection_file = Path(configured)
    elif configured_runtime:
        connection_file = Path(configured_runtime) / "connection.json"
    else:
        connection_file = Path.home() / ".nimbledesk" / "runtime" / "connection.json"
    return DaemonClient.from_file(connection_file, caller_id="studio-console")


async def home(request: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)


async def create_job(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
        profile_id = raw.get("style_profile_id") if isinstance(raw, dict) else None
        if profile_id:
            raw["brief"] = resolve_brief(
                raw.get("brief", {}), STYLE_PROFILE_STORE.load(profile_id)
            ).model_dump(mode="json")
        payload = CreateJobRequest.model_validate(raw)
        job = await daemon_client().call(
            "job_submit",
            {"kind": "create", "request": payload.model_dump(mode="json")},
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(job, status_code=202)


async def create_photo_job(request: Request) -> JSONResponse:
    try:
        payload = PhotoJobRequest.model_validate(await request.json())
        job = await daemon_client().call(
            "job_submit",
            {"kind": "photo", "request": payload.model_dump(mode="json")},
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(job, status_code=202)


async def list_jobs(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await daemon_client().call("job_list"))
    except Exception as error:
        return JSONResponse({"jobs": [], "daemon_error": str(error)}, status_code=503)


async def get_job(request: Request) -> JSONResponse:
    try:
        job = await daemon_client().call(
            "job_get", {"job_id": request.path_params["job_id"]}
        )
    except Exception:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(job)


async def get_artifact(request: Request) -> Response:
    artifact_name = request.path_params["artifact_name"]
    try:
        artifact = await daemon_client().call(
            "job_artifact",
            {
                "job_id": request.path_params["job_id"],
                "artifact_name": artifact_name,
            },
        )
        path = Path(str(artifact["path"]))
    except Exception:
        return JSONResponse({"error": "unknown artifact"}, status_code=404)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if request.query_params.get("download") == "1" else None,
    )


async def cancel_job(request: Request) -> JSONResponse:
    try:
        job = await daemon_client().call(
            "job_cancel", {"job_id": request.path_params["job_id"]}
        )
    except Exception:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(job)


async def revise_job(request: Request) -> JSONResponse:
    try:
        parent = await daemon_client().call(
            "job_get", {"job_id": request.path_params["job_id"]}
        )
    except Exception:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    result = parent.get("result")
    if parent.get("status") != "completed" or not isinstance(result, dict):
        return JSONResponse(
            {"error": "only a completed job can be revised"}, status_code=409
        )
    plan_value = result.get("plan_path")
    output_value = result.get("output_directory")
    if not isinstance(plan_value, str) or not isinstance(output_value, str):
        return JSONResponse({"error": "photo jobs do not contain edit plans"}, status_code=409)
    plan_path = Path(plan_value)
    output_root = Path(output_value)
    try:
        payload = RevisionSubmission.model_validate(await request.json())
        revision_id = f"revision-{int(time.time())}-{uuid4().hex[:8]}"
        revision_request = ReviseJobRequest(
            plan=plan_path,
            output_directory=output_root / "revisions" / revision_id,
            changes=payload.changes,
            ffmpeg_render=payload.ffmpeg_render,
            davinci=payload.davinci,
            davinci_render=payload.davinci_render,
        )
        revision = await daemon_client().call(
            "job_submit",
            {
                "kind": "revision",
                "parent_job_id": request.path_params["job_id"],
                "request": revision_request.model_dump(mode="json"),
            },
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(revision, status_code=202)


async def select_variant(request: Request) -> JSONResponse:
    try:
        payload = VariantSelectionRequest.model_validate(await request.json())
        job = await daemon_client().call(
            "job_get", {"job_id": request.path_params["job_id"]}
        )
        selected = await daemon_client().call(
            "job_variant_select",
            {
                "job_id": request.path_params["job_id"],
                "variant_id": request.path_params["variant_id"],
                "request": payload.model_dump(mode="json"),
            },
        )
        parent_request = job.get("request")
        profile_id = (
            parent_request.get("style_profile_id")
            if isinstance(parent_request, dict)
            else None
        )
        if profile_id:
            STYLE_PROFILE_STORE.record_feedback(
                profile_id, request.path_params["variant_id"]
            )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(selected, status_code=202)


async def list_style_profiles(request: Request) -> JSONResponse:
    return JSONResponse(
        {"profiles": [profile.model_dump(mode="json") for profile in STYLE_PROFILE_STORE.list()]}
    )


async def save_style_profile(request: Request) -> JSONResponse:
    try:
        profile = StyleProfile.model_validate(await request.json())
        if profile.profile_id != request.path_params["profile_id"]:
            raise ValueError("profile ID in the URL and document must match")
        STYLE_PROFILE_STORE.save(profile)
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(profile.model_dump(mode="json"))


async def list_approvals(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await daemon_client().call("approval_list"))
    except Exception as error:
        return JSONResponse({"approvals": [], "daemon_error": str(error)})


async def runtime_health(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await daemon_client().call("health"))
    except Exception as error:
        return JSONResponse({"status": "unavailable", "error": str(error)}, status_code=503)


async def list_sessions(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await daemon_client().call("session_list"))
    except Exception as error:
        return JSONResponse({"sessions": [], "daemon_error": str(error)}, status_code=503)


async def start_session(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        result = await daemon_client().call(
            "session_start",
            {"reason": payload["reason"], "config": payload.get("config", {})},
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(result, status_code=201)


async def set_session_state(request: Request) -> JSONResponse:
    state = request.path_params["state"]
    if state not in {"active", "paused", "stopped"}:
        return JSONResponse({"error": "unknown session state"}, status_code=404)
    try:
        result = await daemon_client().call(
            "session_set_state",
            {"session_id": request.path_params["session_id"], "state": state},
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(result)


async def emergency_stop(request: Request) -> JSONResponse:
    try:
        return JSONResponse(await daemon_client().call("emergency_stop"))
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)


async def intelligence_readiness(request: Request) -> JSONResponse:
    with tempfile.TemporaryDirectory(prefix="nimbledesk-intelligence-readiness-") as temporary:
        selection = resolve_automatic_intelligence(
            CreativeBrief(),
            Path(temporary),
            enabled=True,
            game_ocr=False,
            transcribe=False,
            vision_provider=None,
            music_catalog=None,
            sound_catalog=None,
        )
    return JSONResponse(selection.report.model_dump(mode="json"))


async def decide_approval(request: Request) -> JSONResponse:
    decision = request.path_params["decision"]
    if decision not in {"approve", "approve-temporary", "reject"}:
        return JSONResponse({"error": "unknown approval decision"}, status_code=404)
    try:
        method = (
            "approval_approve_temporary"
            if decision == "approve-temporary"
            else f"approval_{decision}"
        )
        result = await daemon_client().call(
            method, {"approval_id": request.path_params["approval_id"]}
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    if decision == "approve-temporary":
        return JSONResponse({"status": decision, **result})
    return JSONResponse({"status": decision})


async def revoke_approval_rule(request: Request) -> JSONResponse:
    try:
        result = await daemon_client().call(
            "approval_rule_revoke", {"rule_id": request.path_params["rule_id"]}
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(result)


app = Starlette(
    debug=False,
    middleware=[Middleware(LoopbackGuardMiddleware)],
    routes=[
        Route("/", home),
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/style-profiles", list_style_profiles, methods=["GET"]),
        Route("/api/style-profiles/{profile_id}", save_style_profile, methods=["PUT"]),
        Route("/api/photo-jobs", create_photo_job, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}", get_job, methods=["GET"]),
        Route(
            "/api/jobs/{job_id}/artifacts/{artifact_name}",
            get_artifact,
            methods=["GET"],
        ),
        Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
        Route("/api/jobs/{job_id}/revisions", revise_job, methods=["POST"]),
        Route(
            "/api/jobs/{job_id}/variants/{variant_id}/select",
            select_variant,
            methods=["POST"],
        ),
        Route("/api/approvals", list_approvals, methods=["GET"]),
        Route("/api/health", runtime_health, methods=["GET"]),
        Route("/api/sessions", list_sessions, methods=["GET"]),
        Route("/api/sessions", start_session, methods=["POST"]),
        Route(
            "/api/sessions/{session_id}/{state}", set_session_state, methods=["POST"]
        ),
        Route("/api/emergency-stop", emergency_stop, methods=["POST"]),
        Route("/api/intelligence", intelligence_readiness, methods=["GET"]),
        Route(
            "/api/approvals/{approval_id}/{decision}", decide_approval, methods=["POST"]
        ),
        Route(
            "/api/approval-rules/{rule_id}/revoke",
            revoke_approval_rule,
            methods=["POST"],
        ),
    ],
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local NimbleDesk creation console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    if arguments.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the NimbleDesk console may only bind to a loopback address")
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="info")


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NimbleDesk Studio</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; background: #0b1020; color: #e8ecf7; }
    main { max-width: 980px; margin: 0 auto; padding: 48px 24px; }
    h1 { font-size: 40px; margin: 0 0 8px; } p { color: #aeb8d0; }
    form, article { background: #151c30; border: 1px solid #29334f; border-radius: 16px;
      padding: 24px; margin: 24px 0; box-shadow: 0 12px 40px #0005; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    label { display: grid; gap: 7px; color: #c7cfe1; font-size: 14px; }
    input, select, textarea { box-sizing: border-box; width: 100%; border: 1px solid #3a4668;
      background: #0e1528; color: white; border-radius: 9px; padding: 11px 12px; }
    textarea { min-height: 76px; resize: vertical; }
    .checks { display: flex; flex-wrap: wrap; gap: 16px; margin: 18px 0; }
    .checks label { display: flex; align-items: center; } .checks input { width: auto; }
    button { border: 0; border-radius: 10px; padding: 12px 18px; font-weight: 700;
      color: #07101f; background: #71e5b4; cursor: pointer; }
    progress { width: 100%; accent-color: #71e5b4; }
    code { color: #8ed8ff; } .error { color: #ff9b9b; }
    .segments { display: grid; gap: 10px; margin: 18px 0; }
    .segment { display: grid; grid-template-columns: auto 1fr auto; gap: 12px;
      align-items: start; padding: 12px; border: 1px solid #303b5b; border-radius: 10px; }
    .segment input { margin-top: 3px; width: auto; }
    .segment small { display: block; color: #95a2c0; margin-top: 4px; }
    .revision-form { margin: 18px 0 0; padding: 16px; background: #0e1528; }
    .actions { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; }
    .actions label { min-width: 130px; }
    details summary { cursor: pointer; color: #8ed8ff; }
    video, .artifact-image { width: 100%; max-height: 540px; border-radius: 12px;
      background: #050812; object-fit: contain; }
    .artifact-links, .variants { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    .artifact-links a { color: #8ed8ff; padding: 7px 10px; border: 1px solid #344263;
      border-radius: 8px; text-decoration: none; }
    .variant { flex: 1 1 220px; border: 1px solid #303b5b; border-radius: 10px; padding: 12px; }
    @media (max-width: 680px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body><main>
  <h1>NimbleDesk Studio</h1>
  <p>Turn long footage into a planned, rendered, and editable DaVinci Resolve timeline.</p>
  <section id="health"></section>
  <section id="intelligence"></section>
  <form id="session-control">
    <h2>Desktop control sessions</h2>
    <div class="grid">
      <label>Reason<input name="reason" required value="Operate an approved desktop workflow"></label>
      <label>Allowed application IDs<input name="applications" placeholder="com.blackmagic-design.DaVinciResolve"></label>
    </div>
    <div class="checks">
      <label><input name="inputEnabled" type="checkbox"> Allow desktop input</label>
      <label><input name="clipboardEnabled" type="checkbox"> Allow clipboard access</label>
    </div>
    <div class="actions"><button>Start session</button>
      <button class="cancel" type="button" onclick="emergencyStop()">Emergency stop all</button></div>
    <div id="sessions"></div>
  </form>
  <section id="approvals"></section>
  <form id="create">
    <div class="grid">
      <label>Source video
        <input name="source" required placeholder="/absolute/path/gameplay.mp4">
      </label>
      <label>Output directory
        <input name="output" required placeholder="/absolute/path/output">
      </label>
      <label>Title<input name="title" value="My creation"></label>
      <label>Style profile<select name="styleProfile"><option value="">No saved profile</option></select></label>
      <label>Audience<input name="audience" value="general"></label>
      <label>Content type<select name="kind"><option>auto</option><option>gameplay</option>
        <option>talking_head</option><option>tutorial</option><option>vlog</option></select></label>
      <label>Publishing platform<input name="platform" value="youtube"></label>
      <label>Target seconds<input name="duration" type="number" min="5" value="60"></label>
      <label>Clip count<input name="clipCount" type="number" min="1" max="100" value="10"></label>
      <label>Aspect ratio<select name="ratio"><option>16:9</option><option>9:16</option>
        <option>1:1</option></select></label>
      <label>Pace<select name="pace"><option>balanced</option><option>fast</option>
        <option>calm</option></select></label>
      <label>Mood<input name="mood" value="engaging"></label>
      <label>Color look<input name="colorLook" value="natural_contrast"></label>
      <label>Title style<input name="titleStyle" value="clean"></label>
      <label>Transition style<select name="transitionStyle"><option>restrained</option>
        <option>energetic</option><option>cinematic</option></select></label>
      <label>Music style or genres<input name="musicStyle" placeholder="cinematic, electronic"></label>
      <label>Autonomy<select name="autonomy"><option value="render_review">Render review</option>
        <option value="review_before_render">Review before render</option><option value="plan_only">Plan only</option>
        <option value="execute_editor">Allow editor execution</option></select></label>
      <label>Required event types<input name="mandatoryEvents" placeholder="clutch, victory"></label>
      <label>Excluded event types<input name="excludedEvents" placeholder="death, loading"></label>
      <label>Reference links or notes<input name="references" placeholder="One per comma"></label>
      <label>Preferred speakers<input name="speakers" placeholder="Rohit, Guest"></label>
      <label>Excluded content<input name="excludedContent" placeholder="profanity, private screens"></label>
      <label>Required moments
        <textarea name="mandatoryMoments" placeholder="One per line: label | start seconds | end seconds | event type"></textarea>
      </label>
      <label>Excluded moments
        <textarea name="excludedMoments" placeholder="One per line: label | start seconds | end seconds | event type"></textarea>
      </label>
      <label>Transcript JSON<input name="transcript" placeholder="Optional"></label>
      <label>Whisper model<input name="whisperModel" value="small"></label>
      <label>Transcript language<input name="language" placeholder="Auto detect"></label>
      <label>Licensed music catalog JSON<input name="music" placeholder="Optional"></label>
      <label>Licensed sound catalog JSON<input name="sounds" placeholder="Optional"></label>
      <label>Timeline events JSON<input name="events" placeholder="Optional"></label>
      <label>Game domain pack JSON<input name="gamePack" placeholder="Optional"></label>
      <label>Semantic vision provider JSON<input name="visionProvider" placeholder="Optional"></label>
      <label>Brand logo path<input name="brandLogo" placeholder="Optional PNG"></label>
      <label>Brand font path<input name="brandFont" placeholder="Optional TTF/OTF"></label>
      <label>Primary brand color<input name="primaryColor" placeholder="#71e5b4"></label>
      <label>Secondary brand color<input name="secondaryColor" placeholder="#8ed8ff"></label>
      <label>Protected colors<input name="protectedColors" placeholder="#ff0000, #00ff00"></label>
      <label>Logo position<select name="logoPosition"><option>top_right</option><option>top_left</option>
        <option>bottom_right</option><option>bottom_left</option></select></label>
      <label>Logo width fraction<input name="logoWidth" type="number" min="0.03" max="0.35" step="0.01" value="0.12"></label>
      <label>Caption language<input name="captionLanguage" placeholder="en"></label>
      <label>Caption characters per line<input name="captionLineLength" type="number" min="20" max="60" value="42"></label>
      <label>Caption characters per second<input name="captionSpeed" type="number" min="8" max="30" step="0.5" value="22"></label>
    </div>
    <div class="checks">
      <label><input name="automaticIntelligence" type="checkbox" checked> Automatically discover intelligence tools and licensed media</label>
      <label><input name="gameOcr" type="checkbox"> Force game event OCR</label>
      <label><input name="transcribe" type="checkbox"> Run Whisper</label>
      <label><input name="captions" type="checkbox" checked> Burn captions</label>
      <label><input name="musicEnabled" type="checkbox" checked> Select music</label>
      <label><input name="ffmpegRender" type="checkbox" checked> Render review MP4</label>
      <label><input name="davinci" type="checkbox"> Import into DaVinci</label>
      <label><input name="davinciRender" type="checkbox"> Render in DaVinci</label>
      <label><input name="brandRequired" type="checkbox"> Require brand assets</label>
      <label><input name="captionsRequired" type="checkbox"> Require captions for accessibility</label>
      <label><input name="speakerLabels" type="checkbox" checked> Include speaker labels</label>
      <label><input name="audioDescription" type="checkbox"> Require audio description</label>
      <label><input name="remoteTranscript" type="checkbox"> Allow remote transcript processing</label>
      <label><input name="remoteAudio" type="checkbox"> Allow remote audio processing</label>
      <label><input name="remoteFrames" type="checkbox"> Allow remote frame processing</label>
      <label><input name="retainCache" type="checkbox" checked> Retain local analysis cache</label>
    </div>
    <button>Create video</button>
  </form>
  <form id="photos">
    <h2>Create from photos</h2>
    <div class="grid">
      <label>Photo or folder
        <input name="source" required placeholder="/absolute/path/photos">
      </label>
      <label>Output directory
        <input name="output" required placeholder="/absolute/path/photo-output">
      </label>
      <label>Photos to select<input name="count" type="number" min="1" max="1000" value="20"></label>
      <label>Slideshow width<input name="width" type="number" min="320" max="7680" value="1920"></label>
      <label>Story title<input name="title" value="Photo story"></label>
      <label>Platform<select name="platform"><option>instagram</option><option>youtube</option>
        <option>tiktok</option></select></label>
    </div>
    <div class="checks"><label><input name="slideshow" type="checkbox"> Create MP4 slideshow</label>
      <label><input name="socialAssets" type="checkbox" checked> Create social assets</label>
      <label><input name="animatedGif" type="checkbox"> Create animated GIF</label></div>
    <button>Create photo story</button>
  </form>
  <section id="jobs"></section>
</main><script>
const form = document.querySelector('#create'); const jobs = document.querySelector('#jobs');
const photoForm = document.querySelector('#photos');
const approvals = document.querySelector('#approvals');
const health = document.querySelector('#health');
const intelligence = document.querySelector('#intelligence');
const sessionForm = document.querySelector('#session-control');
const sessions = document.querySelector('#sessions');
sessionForm.addEventListener('submit',async event=>{event.preventDefault();const data=new FormData(sessionForm);
  const applications=String(data.get('applications')||'').split(',').map(value=>value.trim()).filter(Boolean);
  const response=await fetch('/api/sessions',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({reason:data.get('reason'),config:{input_enabled:data.has('inputEnabled'),
      clipboard_enabled:data.has('clipboardEnabled'),allowed_applications:applications}})});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refreshSessions();});
async function refreshSessions(){const response=await fetch('/api/sessions');const data=await response.json();
  sessions.innerHTML=(data.sessions||[]).map(item=>`<article><strong>${h(item.reason)}</strong> · ${h(item.state)}
    <small>${h(item.session_id)} · ${item.action_count} actions</small><div class="actions">
    ${item.state==='active'?`<button type="button" onclick="setSessionState('${h(item.session_id)}','paused')">Pause</button>`:''}
    ${item.state==='paused'?`<button type="button" onclick="setSessionState('${h(item.session_id)}','active')">Resume</button>`:''}
    ${item.state!=='stopped'?`<button type="button" class="cancel" onclick="setSessionState('${h(item.session_id)}','stopped')">Stop</button>`:''}
    </div></article>`).join('')||'<p>No desktop-control sessions.</p>';}
async function setSessionState(sessionId,state){const response=await fetch(
  `/api/sessions/${encodeURIComponent(sessionId)}/${state}`,{method:'POST'});const result=await response.json();
  if(!response.ok){alert(result.error);return;}refreshSessions();}
async function emergencyStop(){const response=await fetch('/api/emergency-stop',{method:'POST'});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refreshSessions();refreshApprovals();}
form.addEventListener('submit', async event => {
  event.preventDefault(); try { const data = new FormData(form);
  const optional = name => data.get(name) || null;
  const list = name => String(data.get(name)||'').split(',').map(value=>value.trim()).filter(Boolean);
  const moments = name => String(data.get(name)||'').split('\\n').map(value=>value.trim()).filter(Boolean)
    .map(value=>{const parts=value.split('|').map(item=>item.trim());if(parts.length<3)
      throw new Error(`${name} entries need label | start | end | optional event type`);
      return {label:parts[0],start_seconds:Number(parts[1]),end_seconds:Number(parts[2]),
        event_type:parts[3]||null};});
  const payload = {source:data.get('source'), output_directory:data.get('output'),
    brief:{title:data.get('title'),content_kind:data.get('kind'),audience:data.get('audience'),
      platform:data.get('platform'),
      target_duration_seconds:Number(data.get('duration')),aspect_ratio:data.get('ratio'),
      pace:data.get('pace'),mood:data.get('mood'),clip_count:Number(data.get('clipCount')),
      captions:data.has('captions'),music:data.has('musicEnabled'),color_look:data.get('colorLook'),
      autonomy:data.get('autonomy'),
      title_style:data.get('titleStyle'),transition_style:data.get('transitionStyle'),
      music_style:list('musicStyle'),references:list('references'),preferred_speakers:list('speakers'),
      excluded_content:list('excludedContent'),mandatory_moments:moments('mandatoryMoments'),
      excluded_moments:moments('excludedMoments'),
      mandatory_event_types:list('mandatoryEvents'),excluded_event_types:list('excludedEvents'),
      brand:{logo_path:optional('brandLogo'),font_path:optional('brandFont'),
        primary_color:optional('primaryColor'),secondary_color:optional('secondaryColor'),
        protected_colors:list('protectedColors'),logo_position:data.get('logoPosition'),
        logo_width_fraction:Number(data.get('logoWidth')),required:data.has('brandRequired')},
      accessibility:{captions_required:data.has('captionsRequired'),
        caption_language:optional('captionLanguage'),speaker_labels:data.has('speakerLabels'),
        maximum_caption_characters_per_line:Number(data.get('captionLineLength')),
        maximum_caption_characters_per_second:Number(data.get('captionSpeed')),
        audio_description_required:data.has('audioDescription')},
      data_policy:{allow_remote_transcript:data.has('remoteTranscript'),
        allow_remote_audio:data.has('remoteAudio'),allow_remote_frames:data.has('remoteFrames'),
        retain_analysis_cache:data.has('retainCache')}},
    transcript:optional('transcript'),music_catalog:optional('music'),sound_catalog:optional('sounds'),
    events:optional('events'),
    game_pack:optional('gamePack'),
    vision_provider:optional('visionProvider'),
    automatic_intelligence:data.has('automaticIntelligence'),
    game_ocr:data.has('gameOcr'),transcribe:data.has('transcribe'),
    whisper_model:data.get('whisperModel'),language:optional('language'),
    style_profile_id:optional('styleProfile'),ffmpeg_render:data.has('ffmpegRender'),davinci:data.has('davinci'),
    davinci_render:data.has('davinciRender')};
  const response = await fetch('/api/jobs',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)
  }); const result=await response.json();
  if(!response.ok){alert(result.error);return;} refresh();
  }catch(error){alert(error instanceof Error?error.message:String(error));}
});
photoForm.addEventListener('submit',async event=>{event.preventDefault();const data=new FormData(photoForm);
  const payload={source:data.get('source'),output_directory:data.get('output'),count:Number(data.get('count')),
    create_slideshow:data.has('slideshow'),slideshow_width:Number(data.get('width')),
    create_social_assets:data.has('socialAssets'),create_animated_gif:data.has('animatedGif'),
    title:data.get('title'),platform:data.get('platform')};
  const response=await fetch('/api/photo-jobs',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refresh();});
const h=value=>String(value??'').replace(/[&<>"']/g,char=>({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[char]));
function revisionPanel(job){const plan=job.result?.plan;if(!plan||job.status!=='completed')return '';
  const segments=plan.segments.map(segment=>{const confidence=Math.max(0,...segment.evidence.map(e=>e.confidence));
    const evidence=segment.evidence.map(e=>e.description).join(' · ');
    return `<label class="segment"><input type="checkbox" name="locked" value="${h(segment.segment_id)}"
      ${segment.locked?'checked':''}><span><strong>${h(segment.role)}</strong> · ${h(segment.segment_id)}
      <small>Source ${segment.source_range.start_seconds.toFixed(1)}–${segment.source_range.end_seconds.toFixed(1)}s · ${segment.speed.rate}× ${h(segment.speed.interpolation)} · punch ${segment.visual.punch_in_scale}× · ${h(segment.visual.transition_in)} · ${h(segment.visual.color_look)} · crop ${segment.visual.reframe_center_x.toFixed(2)}, ${segment.visual.reframe_center_y.toFixed(2)} (${Math.round(segment.visual.reframe_confidence*100)}%)</small>
      <small>${h(evidence||'No evidence recorded')}</small></span><span>${Math.round(confidence*100)}%</span></label>`;}).join('');
  return `<details><summary>Review and revise ${plan.segments.length} decisions</summary>
    <form class="revision-form" data-job-id="${h(job.job_id)}"><p>Checked decisions are approved and locked in the next version.</p>
    <div class="segments">${segments}</div><div class="actions">
      <label>Target seconds<input name="duration" type="number" min="5" value="${h(plan.brief.target_duration_seconds)}"></label>
      <label>Pace<select name="pace"><option ${plan.brief.pace==='calm'?'selected':''}>calm</option><option ${plan.brief.pace==='balanced'?'selected':''}>balanced</option><option ${plan.brief.pace==='fast'?'selected':''}>fast</option></select></label>
      <label>Color look<input name="color" value="${h(plan.brief.color_look)}"></label>
      <label><input name="render" type="checkbox"> Render MP4</label>
      <label><input name="davinci" type="checkbox"> Import to Resolve</label>
      <button>Build revision</button></div></form></details>`;}
function jobOutputs(job){if(!job.result)return '';
  const artifacts=job.artifacts||[];const byName=name=>artifacts.find(item=>item.name===name);
  const render=byName('render')||byName('slideshow');const image=byName('contact-sheet');
  const preview=render?`<video controls preload="metadata" src="${h(render.url)}"></video>`:
    image?`<img class="artifact-image" src="${h(image.url)}" alt="Generated contact sheet">`:'';
  const links=artifacts.map(item=>`<a href="${h(item.download_url)}">${h(item.name)} · ${
    Math.max(1,Math.round(item.size_bytes/1024))} KB</a>`).join('');
  const variants=(job.variant_options||[]).map(item=>`<div class="variant"><strong>${h(item.variant_id)}
    ${item.recommended?' · recommended':''}${item.selected?' · selected':''}</strong>
    <p>${h(item.tradeoff)} · ${Math.round(item.overall_score*100)}%</p>
    <button onclick="selectVariant('${h(job.job_id)}','${h(item.variant_id)}')" ${item.selected?'disabled':''}>
      ${item.selected?'Selected':'Select and render'}</button></div>`).join('');
  const variantPanel=variants?`<details><summary>Compare and select edit variants</summary>
    <div class="variants">${variants}</div></details>`:'';
  const selected=job.kind==='photo'?`<p>Selected <strong>${job.result.selected.length}</strong> photos</p>`:'';
  const capabilities=(job.automatic_capabilities||[]).map(item=>`<li><strong>${h(item.capability)}</strong>
    · ${h(item.status)} — ${h(item.detail)}</li>`).join('');
  const intelligence=capabilities?`<details><summary>Automatic intelligence decisions</summary>
    <ul>${capabilities}</ul></details>`:'';
  return `${preview}${selected}<div class="artifact-links">${links}</div>${intelligence}${variantPanel}${revisionPanel(job)}`;}
async function refresh(){const response=await fetch('/api/jobs');const data=await response.json();
  jobs.innerHTML=data.jobs.map(job=>`<article><strong>${h(job.kind)}</strong> · <strong>${h(job.status)}</strong> · ${h(job.stage)}
    <progress value="${job.progress}" max="1"></progress>
    ${job.error?`<p class="error">${h(job.error)}</p>`:''}
    ${['queued','running','cancelling'].includes(job.status)?
      `<button class="cancel" onclick="cancelJob('${h(job.job_id)}')">Cancel</button>`:''}
    ${jobOutputs(job)}</article>`).join('');}
async function refreshApprovals(){const response=await fetch('/api/approvals');const data=await response.json();
  const pending=data.approvals?.length?`<h2>Actions awaiting your approval</h2>`+
    data.approvals.map(item=>{const action=item.action;const adapter=action.arguments?.adapter_id||'application';
      const command=action.arguments?.command||action.kind;
      const reviewArguments=action.kind==='app_command'?action.arguments?.arguments||{}:action.arguments||{};
      const evidence=item.evidence;
      const evidenceImage=evidence?.data_base64?`<img class="artifact-image" src="data:${
        h(evidence.mime_type)};base64,${h(evidence.data_base64)}" alt="Action approval evidence">
        <small>Observation ${h(evidence.observation_id)} · ${h(evidence.width)}×${h(evidence.height)} · SHA-256 ${
          h(evidence.sha256)}</small>`:'';
      return `<article><strong>${h(adapter)}</strong> · ${h(command)}
      <p>Expires ${h(new Date(item.expires_at*1000).toLocaleTimeString())}</p>
      ${evidenceImage}
      <pre><code>${h(JSON.stringify(reviewArguments,null,2))}</code></pre>
      <button onclick="decideApproval('${h(item.approval_id)}','approve')">Approve exact action</button>
      ${action.kind==='write_clipboard'?'':`<button onclick="decideApproval('${h(item.approval_id)}',
        'approve-temporary')">Approve scope for 10 minutes</button>`}
      <button onclick="decideApproval('${h(item.approval_id)}','reject')">Reject</button></article>`;}).join(''):'';
  const rules=(data.temporary_rules||[]).map(rule=>`<article><strong>Temporary approval</strong>
    <p>${h(rule.description)} · ${h(rule.remaining_uses)} uses remain · expires ${
      h(new Date(rule.expires_at*1000).toLocaleTimeString())}</p>
    <button onclick="revokeApprovalRule('${h(rule.rule_id)}')">Revoke</button></article>`).join('');
  approvals.innerHTML=pending+(rules?`<h2>Active temporary approvals</h2>${rules}`:'');}
async function refreshHealth(){const response=await fetch('/api/health');const data=await response.json();
  const capabilities=(data.capabilities||[]).join(', ')||'none';health.innerHTML=`<article>
    <strong>Desktop runtime: ${h(data.status)}</strong><p>Backend: ${h(data.backend||'unavailable')} ·
    Capabilities: ${h(capabilities)}</p>${data.error?`<p class="error">${h(data.error)}</p>`:''}</article>`;}
async function refreshIntelligence(){const response=await fetch('/api/intelligence');const data=await response.json();
  const items=(data.capabilities||[]).map(item=>`<li><strong>${h(item.capability)}</strong> ·
    ${h(item.status)} — ${h(item.detail)}</li>`).join('');intelligence.innerHTML=`<article>
    <strong>Creative intelligence readiness</strong><ul>${items}</ul></article>`;}
async function decideApproval(approvalId,decision){const response=await fetch(
  `/api/approvals/${approvalId}/${decision}`,{method:'POST',headers:{'content-type':'application/json'}});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refreshApprovals();}
async function revokeApprovalRule(ruleId){const response=await fetch(
  `/api/approval-rules/${ruleId}/revoke`,{method:'POST'});const result=await response.json();
  if(!response.ok){alert(result.error);return;}refreshApprovals();}
async function cancelJob(jobId){await fetch(`/api/jobs/${jobId}/cancel`,{method:'POST'});refresh();}
async function selectVariant(jobId,variantId){const response=await fetch(
  `/api/jobs/${jobId}/variants/${variantId}/select`,{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({ffmpeg_render:true,davinci:false,davinci_render:false})});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refresh();}
async function loadStyleProfiles(){const response=await fetch('/api/style-profiles');const data=await response.json();
  const select=form.elements.styleProfile;select.innerHTML='<option value="">No saved profile</option>'+
    data.profiles.map(profile=>`<option value="${h(profile.profile_id)}">${h(profile.name)}</option>`).join('');
  select.addEventListener('change',()=>{const profile=data.profiles.find(item=>item.profile_id===select.value);
    if(!profile)return;const defaults=profile.defaults;
    const fields={audience:'audience',platform:'platform',aspect_ratio:'ratio',pace:'pace',mood:'mood',
      color_look:'colorLook',autonomy:'autonomy'};for(const [key,name] of Object.entries(fields))if(defaults[key]!=null)form.elements[name].value=defaults[key];
    if(defaults.captions!=null)form.elements.captions.checked=defaults.captions;
    if(defaults.music!=null)form.elements.musicEnabled.checked=defaults.music;});}
jobs.addEventListener('submit',async event=>{if(!event.target.matches('.revision-form'))return;
  event.preventDefault();const revisionForm=event.target;const data=new FormData(revisionForm);
  const segmentIds=[...revisionForm.querySelectorAll('input[name="locked"]')].map(input=>input.value);
  const locked=new Set(data.getAll('locked'));
  const payload={changes:{target_duration_seconds:Number(data.get('duration')),pace:data.get('pace'),
    color_look:data.get('color'),lock_segment_ids:segmentIds.filter(id=>locked.has(id)),
    unlock_segment_ids:segmentIds.filter(id=>!locked.has(id))},ffmpeg_render:data.has('render'),
    davinci:data.has('davinci'),davinci_render:false};
  const response=await fetch(`/api/jobs/${revisionForm.dataset.jobId}/revisions`,{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refresh();});
refresh();refreshSessions();refreshApprovals();refreshHealth();refreshIntelligence();loadStyleProfiles();setInterval(()=>{refreshSessions();refreshApprovals();
  refreshHealth();
  if(!document.querySelector('.revision-form:focus-within'))refresh();},2000);
</script></body></html>"""
