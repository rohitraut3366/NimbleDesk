from __future__ import annotations

import argparse
import threading
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

from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.workflow import CreationResult, CreationWorkflow


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


@dataclass
class JobRecord:
    job_id: str
    request: CreateJobRequest
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    stage: str = "queued"
    progress: float = 0
    result: CreationResult | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def response(self) -> dict[str, Any]:
        with self.lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "stage": self.stage,
                "progress": self.progress,
                "error": self.error,
                "result": self.result.model_dump(mode="json") if self.result else None,
            }


class JobService:
    def __init__(self, maximum_workers: int = 1) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=maximum_workers,
            thread_name_prefix="nimbledesk-creation",
        )

    def submit(self, request: CreateJobRequest) -> JobRecord:
        source = request.source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source video does not exist: {source}")
        job = JobRecord(job_id=str(uuid4()), request=request)
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.response() for job in reversed(jobs)]

    def _run(self, job: JobRecord) -> None:
        with job.lock:
            job.status = "running"
            job.stage = "analyzing and creating"
            job.progress = 0.05
        request = job.request
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
            )
        except Exception as error:
            with job.lock:
                job.status = "failed"
                job.stage = "failed"
                job.error = str(error)
            return
        with job.lock:
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1
            job.result = result


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


app = Starlette(
    debug=False,
    routes=[
        Route("/", home),
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}", get_job, methods=["GET"]),
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
    ${job.result?`<p>Render: <code>${h(job.result.render_path||'plan only')}</code><br>
      DaVinci timeline: <code>${h(job.result.timeline_path)}</code></p>`:''}</article>`).join('');}
refresh(); setInterval(refresh,2000);
</script></body></html>"""
