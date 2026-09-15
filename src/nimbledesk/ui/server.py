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
from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.workflow import CreationResult, CreationWorkflow
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
    request: CreateJobRequest
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: float = 0
    result: CreationResult | None = None
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


app = Starlette(
    debug=False,
    routes=[
        Route("/", home),
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}", get_job, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
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
    </div>
    <div class="checks">
      <label><input name="gameOcr" type="checkbox"> Detect game events</label>
      <label><input name="transcribe" type="checkbox"> Run Whisper</label>
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
    transcript:optional('transcript'),music_catalog:optional('music'),
    game_ocr:data.has('gameOcr'),transcribe:data.has('transcribe'),whisper_model:'small',
    ffmpeg_render:true,davinci:data.has('davinci'),davinci_render:data.has('davinciRender')};
  const response = await fetch('/api/jobs',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)
  }); const result=await response.json();
  if(!response.ok){alert(result.error);return;} refresh();
});
const h=value=>String(value??'').replace(/[&<>"']/g,char=>({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[char]));
async function refresh(){const response=await fetch('/api/jobs');const data=await response.json();
  jobs.innerHTML=data.jobs.map(job=>`<article><strong>${h(job.status)}</strong> · ${h(job.stage)}
    <progress value="${job.progress}" max="1"></progress>
    ${job.error?`<p class="error">${h(job.error)}</p>`:''}
    ${['queued','running','cancelling'].includes(job.status)?
      `<button class="cancel" onclick="cancelJob('${h(job.job_id)}')">Cancel</button>`:''}
    ${job.result?`<p>Render: <code>${h(job.result.render_path||'plan only')}</code><br>
      DaVinci timeline: <code>${h(job.result.timeline_path)}</code></p>`:''}</article>`).join('');}
async function cancelJob(jobId){await fetch(`/api/jobs/${jobId}/cancel`,{method:'POST'});refresh();}
refresh(); setInterval(refresh,2000);
</script></body></html>"""
