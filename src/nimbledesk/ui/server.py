# ruff: noqa: E501
from __future__ import annotations

import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import uvicorn
from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.davinci import connect_to_resolve, execute_in_davinci
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.models import (
    CreativeBrief,
    EditPlan,
    PlanRevisionRequest,
    PlanValidationReport,
)
from nimbledesk.creative.render import render_edit_plan
from nimbledesk.creative.revision import revise_edit_plan, write_revision
from nimbledesk.creative.validation import validate_edit_plan, write_validation_report
from nimbledesk.creative.workflow import CreationResult, CreationWorkflow
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.media.process import ProcessCancelled


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Path
    output_directory: Path
    brief: CreativeBrief
    events: Path | None = None
    game_ocr: bool = False
    game_pack: Path | None = None
    transcript: Path | None = None
    transcribe: bool = False
    whisper_model: str = "small"
    language: str | None = None
    music_catalog: Path | None = None
    ffmpeg_render: bool = True
    davinci: bool = False
    davinci_render: bool = False


class ReviseJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Path
    output_directory: Path
    changes: PlanRevisionRequest
    ffmpeg_render: bool = False
    davinci: bool = False
    davinci_render: bool = False


class RevisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_directory: Path
    plan_path: Path
    diff_path: Path
    validation_path: Path
    timeline_path: Path
    render_path: Path | None
    validation: PlanValidationReport
    plan: EditPlan


class RevisionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: PlanRevisionRequest
    ffmpeg_render: bool = False
    davinci: bool = False
    davinci_render: bool = False


JobStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "interrupted",
]


class PersistedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    kind: Literal["create", "revision"] = "create"
    request: CreateJobRequest | ReviseJobRequest
    parent_job_id: str | None = None
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: float = 0
    result: CreationResult | RevisionResult | None = None
    error: str | None = None
    created_at: float
    updated_at: float


@dataclass
class JobRecord:
    state: PersistedJob
    lock: threading.Lock = field(default_factory=threading.Lock)
    save_lock: threading.Lock = field(default_factory=threading.Lock)
    cancellation: CancellationToken = field(default_factory=CancellationToken)

    def response(self) -> dict[str, Any]:
        with self.lock:
            return self.state.model_dump(mode="json")


class JobService:
    def __init__(
        self,
        storage_directory: Path | None = None,
        maximum_workers: int = 1,
    ) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        configured = os.getenv("NIMBLEDESK_JOB_DIR")
        self._storage_directory = storage_directory or (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".nimbledesk" / "jobs"
        )
        self._executor = ThreadPoolExecutor(
            max_workers=maximum_workers,
            thread_name_prefix="nimbledesk-creation",
        )
        self._load()

    def submit(self, request: CreateJobRequest) -> JobRecord:
        source = request.source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source video does not exist: {source}")
        now = time.time()
        state = PersistedJob(
            job_id=str(uuid4()),
            request=request,
            created_at=now,
            updated_at=now,
        )
        job = JobRecord(state=state)
        with self._lock:
            self._jobs[state.job_id] = job
        self._save(job)
        self._executor.submit(self._run, job)
        return job

    def submit_revision(
        self,
        parent_job_id: str,
        request: ReviseJobRequest,
    ) -> JobRecord:
        plan_path = request.plan.expanduser().resolve()
        if not plan_path.is_file():
            raise FileNotFoundError(f"edit plan does not exist: {plan_path}")
        now = time.time()
        state = PersistedJob(
            job_id=str(uuid4()),
            kind="revision",
            parent_job_id=parent_job_id,
            request=request,
            created_at=now,
            updated_at=now,
        )
        job = JobRecord(state=state)
        with self._lock:
            self._jobs[state.job_id] = job
        self._save(job)
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda job: job.state.created_at,
                reverse=True,
            )
        return [job.response() for job in jobs]

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def cancel(self, job_id: str) -> JobRecord | None:
        job = self.get(job_id)
        if job is None:
            return None
        with job.lock:
            if job.state.status in {"cancelled", "completed", "failed", "interrupted"}:
                return job
            job.cancellation.cancel()
            job.state.status = "cancelling"
            job.state.stage = "cancelling"
            job.state.updated_at = time.time()
        self._save(job)
        return job

    def _run(self, job: JobRecord) -> None:
        with job.lock:
            job.state.status = "running"
            job.state.stage = "analyzing and creating"
            job.state.progress = 0.01
            job.state.updated_at = time.time()
            request = job.state.request
        self._save(job)
        try:
            result: CreationResult | RevisionResult
            if isinstance(request, ReviseJobRequest):
                result = self._run_revision(job, request)
            else:
                result = CreationWorkflow().create(
                    request.source.expanduser().resolve(),
                    request.output_directory.expanduser().resolve(),
                    request.brief,
                    supplied_events=request.events,
                    automatic_game_ocr=request.game_ocr,
                    game_pack=request.game_pack,
                    supplied_transcript=request.transcript,
                    automatic_transcription=request.transcribe,
                    whisper_model=request.whisper_model,
                    language=request.language,
                    music_catalog=request.music_catalog,
                    render=request.ffmpeg_render,
                    execute_davinci=request.davinci or request.davinci_render,
                    render_in_davinci=request.davinci_render,
                    progress=lambda stage, value: self._update_progress(job, stage, value),
                    cancellation=job.cancellation,
                )
        except ProcessCancelled:
            with job.lock:
                job.state.status = "cancelled"
                job.state.stage = "cancelled"
                job.state.error = None
                job.state.updated_at = time.time()
            self._save(job)
            return
        except Exception as error:
            with job.lock:
                job.state.status = "failed"
                job.state.stage = "failed"
                job.state.error = str(error)
                job.state.updated_at = time.time()
            self._save(job)
            return
        with job.lock:
            job.state.status = "completed"
            job.state.stage = "completed"
            job.state.progress = 1
            job.state.result = result
            job.state.updated_at = time.time()
        self._save(job)

    def _run_revision(self, job: JobRecord, request: ReviseJobRequest) -> RevisionResult:
        token = job.cancellation
        self._update_progress(job, "revising approved plan", 0.1)
        plan = EditPlan.model_validate_json(
            request.plan.expanduser().resolve().read_text(encoding="utf-8")
        )
        revision = revise_edit_plan(plan, request.changes)
        output = request.output_directory.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        plan_path = output / "edit_plan.json"
        diff_path = output / "plan_diff.json"
        validation_path = output / "validation.json"
        timeline_path = output / "davinci_timeline.fcpxml"
        write_revision(revision, plan_path, diff_path)
        token.check()
        self._update_progress(job, "validating revision", 0.3)
        validation = validate_edit_plan(revision.plan, probe_media(revision.plan.source_path))
        write_validation_report(validation, validation_path)
        if not validation.valid:
            messages = "; ".join(issue.message for issue in validation.issues)
            raise ValueError(f"revised plan is invalid: {messages}")
        export_fcpxml(revision.plan, timeline_path)
        token.check()
        render_path = output / "final.mp4" if request.ffmpeg_render else None
        if render_path:
            self._update_progress(job, "rendering revised video", 0.55)
            render_edit_plan(revision.plan, render_path, cancelled=token.is_cancelled)
        if request.davinci or request.davinci_render:
            self._update_progress(job, "executing revision in DaVinci Resolve", 0.85)
            execute_in_davinci(
                connect_to_resolve(),
                revision.plan,
                timeline_path,
                output,
                render=request.davinci_render,
                cancelled=token.is_cancelled,
            )
        return RevisionResult(
            output_directory=output,
            plan_path=plan_path,
            diff_path=diff_path,
            validation_path=validation_path,
            timeline_path=timeline_path,
            render_path=render_path,
            validation=validation,
            plan=revision.plan,
        )

    def _update_progress(self, job: JobRecord, stage: str, value: float) -> None:
        with job.lock:
            job.state.stage = stage
            job.state.progress = value
            job.state.updated_at = time.time()
        self._save(job)

    def _save(self, job: JobRecord) -> None:
        with job.save_lock:
            self._storage_directory.mkdir(parents=True, exist_ok=True)
            path = self._storage_directory / f"{job.state.job_id}.json"
            temporary = path.with_suffix(".json.tmp")
            with job.lock:
                content = job.state.model_dump_json(indent=2)
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)

    def _load(self) -> None:
        if not self._storage_directory.is_dir():
            return
        for path in self._storage_directory.glob("*.json"):
            try:
                state = PersistedJob.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if state.status in {"queued", "running", "cancelling"}:
                state.status = "interrupted"
                state.stage = "interrupted; submit again to resume from cached analysis"
                state.updated_at = time.time()
            job = JobRecord(state=state)
            self._jobs[state.job_id] = job
            if state.status == "interrupted":
                self._save(job)


JOB_SERVICE = JobService()


async def home(request: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)


async def create_job(request: Request) -> JSONResponse:
    try:
        payload = CreateJobRequest.model_validate(await request.json())
        job = JOB_SERVICE.submit(payload)
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(job.response(), status_code=202)


async def list_jobs(request: Request) -> JSONResponse:
    return JSONResponse({"jobs": JOB_SERVICE.list()})


async def get_job(request: Request) -> JSONResponse:
    job = JOB_SERVICE.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(job.response())


async def cancel_job(request: Request) -> JSONResponse:
    job = JOB_SERVICE.cancel(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return JSONResponse(job.response())


async def revise_job(request: Request) -> JSONResponse:
    parent = JOB_SERVICE.get(request.path_params["job_id"])
    if parent is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    with parent.lock:
        if parent.state.status != "completed" or parent.state.result is None:
            return JSONResponse(
                {"error": "only a completed job can be revised"}, status_code=409
            )
        plan_path = parent.state.result.plan_path
        output_root = parent.state.result.output_directory
    try:
        payload = RevisionSubmission.model_validate(await request.json())
        revision_id = f"revision-{int(time.time())}-{uuid4().hex[:8]}"
        revision = JOB_SERVICE.submit_revision(
            parent.state.job_id,
            ReviseJobRequest(
                plan=plan_path,
                output_directory=output_root / "revisions" / revision_id,
                changes=payload.changes,
                ffmpeg_render=payload.ffmpeg_render,
                davinci=payload.davinci,
                davinci_render=payload.davinci_render,
            ),
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(revision.response(), status_code=202)


app = Starlette(
    debug=False,
    routes=[
        Route("/", home),
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}", get_job, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
        Route("/api/jobs/{job_id}/revisions", revise_job, methods=["POST"]),
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
    input, select { box-sizing: border-box; width: 100%; border: 1px solid #3a4668;
      background: #0e1528; color: white; border-radius: 9px; padding: 11px 12px; }
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
    @media (max-width: 680px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body><main>
  <h1>NimbleDesk Studio</h1>
  <p>Turn long footage into a planned, rendered, and editable DaVinci Resolve timeline.</p>
  <form id="create">
    <div class="grid">
      <label>Source video
        <input name="source" required placeholder="/absolute/path/gameplay.mp4">
      </label>
      <label>Output directory
        <input name="output" required placeholder="/absolute/path/output">
      </label>
      <label>Title<input name="title" value="My creation"></label>
      <label>Content type<select name="kind"><option>auto</option><option>gameplay</option>
        <option>talking_head</option><option>tutorial</option><option>vlog</option></select></label>
      <label>Target seconds<input name="duration" type="number" min="5" value="60"></label>
      <label>Aspect ratio<select name="ratio"><option>16:9</option><option>9:16</option>
        <option>1:1</option></select></label>
      <label>Pace<select name="pace"><option>balanced</option><option>fast</option>
        <option>calm</option></select></label>
      <label>Mood<input name="mood" value="engaging"></label>
      <label>Transcript JSON<input name="transcript" placeholder="Optional"></label>
      <label>Licensed music catalog JSON<input name="music" placeholder="Optional"></label>
      <label>Timeline events JSON<input name="events" placeholder="Optional"></label>
      <label>Game domain pack JSON<input name="gamePack" placeholder="Optional"></label>
    </div>
    <div class="checks">
      <label><input name="gameOcr" type="checkbox"> Detect game events</label>
      <label><input name="transcribe" type="checkbox"> Run Whisper</label>
      <label><input name="ffmpegRender" type="checkbox" checked> Render review MP4</label>
      <label><input name="davinci" type="checkbox"> Import into DaVinci</label>
      <label><input name="davinciRender" type="checkbox"> Render in DaVinci</label>
    </div>
    <button>Create video</button>
  </form>
  <section id="jobs"></section>
</main><script>
const form = document.querySelector('#create'); const jobs = document.querySelector('#jobs');
form.addEventListener('submit', async event => {
  event.preventDefault(); const data = new FormData(form);
  const optional = name => data.get(name) || null;
  const payload = {source:data.get('source'), output_directory:data.get('output'),
    brief:{title:data.get('title'),content_kind:data.get('kind'),platform:'youtube',
      target_duration_seconds:Number(data.get('duration')),aspect_ratio:data.get('ratio'),
      pace:data.get('pace'),mood:data.get('mood'),clip_count:10,captions:true,music:true,
      color_look:'natural_contrast',mandatory_event_types:[],excluded_event_types:[]},
    transcript:optional('transcript'),music_catalog:optional('music'),events:optional('events'),
    game_pack:optional('gamePack'),
    game_ocr:data.has('gameOcr'),transcribe:data.has('transcribe'),whisper_model:'small',
    ffmpeg_render:data.has('ffmpegRender'),davinci:data.has('davinci'),
    davinci_render:data.has('davinciRender')};
  const response = await fetch('/api/jobs',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)
  }); const result=await response.json();
  if(!response.ok){alert(result.error);return;} refresh();
});
const h=value=>String(value??'').replace(/[&<>"']/g,char=>({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[char]));
function revisionPanel(job){const plan=job.result?.plan;if(!plan||job.status!=='completed')return '';
  const segments=plan.segments.map(segment=>{const confidence=Math.max(0,...segment.evidence.map(e=>e.confidence));
    const evidence=segment.evidence.map(e=>e.description).join(' · ');
    return `<label class="segment"><input type="checkbox" name="locked" value="${h(segment.segment_id)}"
      ${segment.locked?'checked':''}><span><strong>${h(segment.role)}</strong> · ${h(segment.segment_id)}
      <small>Source ${segment.source_range.start_seconds.toFixed(1)}–${segment.source_range.end_seconds.toFixed(1)}s · ${segment.speed.rate}× · ${h(segment.visual.color_look)}</small>
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
async function refresh(){const response=await fetch('/api/jobs');const data=await response.json();
  jobs.innerHTML=data.jobs.map(job=>`<article><strong>${h(job.kind)}</strong> · <strong>${h(job.status)}</strong> · ${h(job.stage)}
    <progress value="${job.progress}" max="1"></progress>
    ${job.error?`<p class="error">${h(job.error)}</p>`:''}
    ${['queued','running','cancelling'].includes(job.status)?
      `<button class="cancel" onclick="cancelJob('${h(job.job_id)}')">Cancel</button>`:''}
    ${job.result?`<p>Render: <code>${h(job.result.render_path||'plan only')}</code><br>
      DaVinci timeline: <code>${h(job.result.timeline_path)}</code></p>${revisionPanel(job)}`:''}</article>`).join('');}
async function cancelJob(jobId){await fetch(`/api/jobs/${jobId}/cancel`,{method:'POST'});refresh();}
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
refresh(); setInterval(()=>{if(!document.querySelector('.revision-form:focus-within'))refresh();},2000);
</script></body></html>"""
