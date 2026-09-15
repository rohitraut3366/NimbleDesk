# ruff: noqa: E501
from __future__ import annotations

import argparse
import mimetypes
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
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from nimbledesk.client import DaemonClient
from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.davinci import execute_davinci_isolated
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.models import (
    CreativeBrief,
    EditPlan,
    PlanRevisionRequest,
    PlanValidationReport,
)
from nimbledesk.creative.render import render_edit_plan
from nimbledesk.creative.revision import revise_edit_plan, write_revision
from nimbledesk.creative.style import StyleProfile, StyleProfileStore, resolve_brief
from nimbledesk.creative.validation import validate_edit_plan, write_validation_report
from nimbledesk.creative.verify import RenderVerificationError, verify_render
from nimbledesk.creative.workflow import CreationResult, CreationWorkflow
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.media.photos import PhotoManifest, PhotoPipeline
from nimbledesk.media.process import ProcessCancelled


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Path
    output_directory: Path
    brief: CreativeBrief
    events: Path | None = None
    game_ocr: bool = False
    game_pack: Path | None = None
    vision_provider: Path | None = None
    transcript: Path | None = None
    transcribe: bool = False
    whisper_model: str = "small"
    language: str | None = None
    music_catalog: Path | None = None
    sound_catalog: Path | None = None
    ffmpeg_render: bool = True
    davinci: bool = False
    davinci_render: bool = False
    style_profile_id: str | None = None


class ReviseJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Path
    output_directory: Path
    changes: PlanRevisionRequest
    ffmpeg_render: bool = False
    davinci: bool = False
    davinci_render: bool = False


class PhotoJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Path
    output_directory: Path
    count: int = 20
    create_slideshow: bool = False
    slideshow_width: int = 1920
    create_social_assets: bool = False
    create_animated_gif: bool = False
    title: str = "Photo story"
    platform: Literal["youtube", "instagram", "tiktok"] = "instagram"


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
    verification_path: Path | None = None


class RevisionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: PlanRevisionRequest
    ffmpeg_render: bool = False
    davinci: bool = False
    davinci_render: bool = False


class VariantSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    kind: Literal["create", "revision", "photo"] = "create"
    request: CreateJobRequest | ReviseJobRequest | PhotoJobRequest
    parent_job_id: str | None = None
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: float = 0
    result: CreationResult | RevisionResult | PhotoManifest | None = None
    error: str | None = None
    selected_variant_id: str | None = None
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
            response = self.state.model_dump(mode="json")
            response["artifacts"] = _artifact_catalog(self.state)
            response["variant_options"] = _variant_options(self.state)
            return response


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

    def submit_photo(self, request: PhotoJobRequest) -> JobRecord:
        source = request.source.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"photo source does not exist: {source}")
        now = time.time()
        state = PersistedJob(
            job_id=str(uuid4()),
            kind="photo",
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

    def select_variant(
        self,
        job: JobRecord,
        variant_id: str,
        request: VariantSelectionRequest,
    ) -> JobRecord:
        with job.lock:
            result = job.state.result
            if job.state.status != "completed" or not isinstance(result, CreationResult):
                raise ValueError("variants can only be selected from a completed creation")
            comparison_path = result.variant_comparison_path
            output_root = result.output_directory
        if comparison_path is None or not comparison_path.is_file():
            raise ValueError("this creation has no variant comparison")
        from nimbledesk.creative.variants import VariantComparison

        comparison = VariantComparison.model_validate_json(
            comparison_path.read_text(encoding="utf-8")
        )
        variant = next(
            (item for item in comparison.variants if item.variant_id == variant_id), None
        )
        if variant is None:
            raise ValueError(f"unknown variant: {variant_id}")
        with job.lock:
            job.state.selected_variant_id = variant_id
            job.state.updated_at = time.time()
        self._save(job)
        selection_id = f"selected-{variant_id}-{int(time.time())}-{uuid4().hex[:8]}"
        return self.submit_revision(
            job.state.job_id,
            ReviseJobRequest(
                plan=variant.plan_path,
                output_directory=output_root / "selections" / selection_id,
                changes=PlanRevisionRequest(),
                ffmpeg_render=request.ffmpeg_render,
                davinci=request.davinci or request.davinci_render,
                davinci_render=request.davinci_render,
            ),
        )

    def _run(self, job: JobRecord) -> None:
        with job.lock:
            job.state.status = "running"
            job.state.stage = "analyzing and creating"
            job.state.progress = 0.01
            job.state.updated_at = time.time()
            request = job.state.request
        self._save(job)
        try:
            result: CreationResult | RevisionResult | PhotoManifest
            if isinstance(request, ReviseJobRequest):
                result = self._run_revision(job, request)
            elif isinstance(request, PhotoJobRequest):
                result = PhotoPipeline().create(
                    request.source.expanduser().resolve(),
                    request.output_directory.expanduser().resolve(),
                    count=request.count,
                    create_slideshow=request.create_slideshow,
                    slideshow_width=request.slideshow_width,
                    create_social_assets=request.create_social_assets,
                    create_animated_gif=request.create_animated_gif,
                    title=request.title,
                    platform=request.platform,
                    progress=lambda stage, value: self._update_progress(job, stage, value),
                    cancelled=job.cancellation.is_cancelled,
                )
            else:
                result = CreationWorkflow().create(
                    request.source.expanduser().resolve(),
                    request.output_directory.expanduser().resolve(),
                    request.brief,
                    supplied_events=request.events,
                    automatic_game_ocr=request.game_ocr,
                    game_pack=request.game_pack,
                    vision_provider=request.vision_provider,
                    supplied_transcript=request.transcript,
                    automatic_transcription=request.transcribe,
                    whisper_model=request.whisper_model,
                    language=request.language,
                    music_catalog=request.music_catalog,
                    sound_catalog=request.sound_catalog,
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
        verification_path = None
        if render_path:
            self._update_progress(job, "rendering revised video", 0.55)
            render_edit_plan(revision.plan, render_path, cancelled=token.is_cancelled)
            verification_path = output / "render_verification.json"
            verification = verify_render(
                revision.plan,
                render_path,
                verification_path,
                cancelled=token.is_cancelled,
            )
            if not verification.valid:
                raise RenderVerificationError(verification)
        if request.davinci or request.davinci_render:
            self._update_progress(job, "executing revision in DaVinci Resolve", 0.85)
            execute_davinci_isolated(
                plan_path,
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
            verification_path=verification_path,
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
STYLE_PROFILE_STORE = StyleProfileStore()


def daemon_client() -> DaemonClient:
    configured = os.getenv("NIMBLEDESK_CONNECTION_FILE")
    connection_file = (
        Path(configured)
        if configured
        else Path.home() / ".nimbledesk" / "runtime" / "connection.json"
    )
    return DaemonClient.from_file(connection_file)


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
        job = JOB_SERVICE.submit(payload)
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(job.response(), status_code=202)


async def create_photo_job(request: Request) -> JSONResponse:
    try:
        payload = PhotoJobRequest.model_validate(await request.json())
        job = JOB_SERVICE.submit_photo(payload)
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


async def get_artifact(request: Request) -> Response:
    job = JOB_SERVICE.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    with job.lock:
        artifacts = _artifact_paths(job.state)
    artifact_name = request.path_params["artifact_name"]
    path = artifacts.get(artifact_name)
    if path is None or not path.is_file():
        return JSONResponse({"error": "unknown artifact"}, status_code=404)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if request.query_params.get("download") == "1" else None,
    )


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
        result = parent.state.result
        if isinstance(result, PhotoManifest):
            return JSONResponse({"error": "photo jobs do not contain edit plans"}, status_code=409)
        plan_path = result.plan_path
        output_root = result.output_directory
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


async def select_variant(request: Request) -> JSONResponse:
    job = JOB_SERVICE.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    try:
        payload = VariantSelectionRequest.model_validate(await request.json())
        selected = JOB_SERVICE.select_variant(
            job, request.path_params["variant_id"], payload
        )
        with job.lock:
            parent_request = job.state.request
            profile_id = (
                parent_request.style_profile_id
                if isinstance(parent_request, CreateJobRequest)
                else None
            )
        if profile_id:
            STYLE_PROFILE_STORE.record_feedback(
                profile_id, request.path_params["variant_id"]
            )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(selected.response(), status_code=202)


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


async def decide_approval(request: Request) -> JSONResponse:
    decision = request.path_params["decision"]
    if decision not in {"approve", "reject"}:
        return JSONResponse({"error": "unknown approval decision"}, status_code=404)
    try:
        result = await daemon_client().call(
            f"approval_{decision}", {"approval_id": request.path_params["approval_id"]}
        )
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse({"status": decision} if decision == "approve" else result)


def _artifact_paths(state: PersistedJob) -> dict[str, Path]:
    result = state.result
    if result is None:
        return {}
    candidates: dict[str, Path | None]
    if isinstance(result, CreationResult):
        candidates = {
            "render": result.render_path,
            "plan": result.plan_path,
            "validation": result.validation_path,
            "timeline": result.timeline_path,
            "content-index": result.content_index_path,
            "transcript": result.transcript_path,
            "events": result.events_path,
            "vision-analysis": result.vision_analysis_path,
            "cue-sheet": result.cue_sheet_path,
            "cue-sheet-csv": result.cue_sheet_csv_path,
            "variant-comparison": result.variant_comparison_path,
            "render-verification": result.verification_path,
            "davinci-verification": result.davinci_verification_path,
            "davinci-render": result.davinci.render_path if result.davinci else None,
            "verification-contact-sheet": result.output_directory
            / "verification-contact-sheet.png",
            "verification-waveform": result.output_directory / "verification-waveform.png",
        }
        root = result.output_directory.expanduser().resolve()
    elif isinstance(result, RevisionResult):
        candidates = {
            "render": result.render_path,
            "plan": result.plan_path,
            "plan-diff": result.diff_path,
            "validation": result.validation_path,
            "timeline": result.timeline_path,
            "render-verification": result.verification_path,
            "verification-contact-sheet": result.output_directory
            / "verification-contact-sheet.png",
            "verification-waveform": result.output_directory / "verification-waveform.png",
        }
        root = result.output_directory.expanduser().resolve()
    else:
        candidates = {
            "contact-sheet": result.contact_sheet,
            "slideshow": result.slideshow,
            "thumbnail": result.thumbnail,
            "poster": result.poster,
            "collage": result.collage,
            "animated-gif": result.animated_gif,
        }
        candidates.update(
            {f"carousel-{index:02d}": path for index, path in enumerate(result.carousel, 1)}
        )
        root = result.contact_sheet.expanduser().resolve().parent
    artifacts: dict[str, Path] = {}
    for name, candidate in candidates.items():
        if candidate is None:
            continue
        path = candidate.expanduser().resolve()
        if path.is_relative_to(root) and path.is_file():
            artifacts[name] = path
    return artifacts


def _artifact_catalog(state: PersistedJob) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "filename": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "size_bytes": path.stat().st_size,
            "url": f"/api/jobs/{state.job_id}/artifacts/{name}",
            "download_url": f"/api/jobs/{state.job_id}/artifacts/{name}?download=1",
        }
        for name, path in _artifact_paths(state).items()
    ]


def _variant_options(state: PersistedJob) -> list[dict[str, object]]:
    if not isinstance(state.result, CreationResult):
        return []
    path = state.result.variant_comparison_path
    if path is None or not path.is_file():
        return []
    from nimbledesk.creative.variants import VariantComparison

    try:
        comparison = VariantComparison.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [
        {
            "variant_id": variant.variant_id,
            "strategy": variant.strategy,
            "overall_score": variant.overall_score,
            "tradeoff": variant.tradeoff,
            "recommended": variant.variant_id == comparison.recommended_variant_id,
            "selected": variant.variant_id == state.selected_variant_id,
            "metrics": [metric.model_dump(mode="json") for metric in variant.metrics],
        }
        for variant in comparison.variants
    ]


app = Starlette(
    debug=False,
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
        Route(
            "/api/approvals/{approval_id}/{decision}", decide_approval, methods=["POST"]
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
      <label>Autonomy<select name="autonomy"><option value="render_review">Render review</option>
        <option value="review_before_render">Review before render</option><option value="plan_only">Plan only</option>
        <option value="execute_editor">Allow editor execution</option></select></label>
      <label>Required event types<input name="mandatoryEvents" placeholder="clutch, victory"></label>
      <label>Excluded event types<input name="excludedEvents" placeholder="death, loading"></label>
      <label>Transcript JSON<input name="transcript" placeholder="Optional"></label>
      <label>Whisper model<input name="whisperModel" value="small"></label>
      <label>Transcript language<input name="language" placeholder="Auto detect"></label>
      <label>Licensed music catalog JSON<input name="music" placeholder="Optional"></label>
      <label>Licensed sound catalog JSON<input name="sounds" placeholder="Optional"></label>
      <label>Timeline events JSON<input name="events" placeholder="Optional"></label>
      <label>Game domain pack JSON<input name="gamePack" placeholder="Optional"></label>
      <label>Semantic vision provider JSON<input name="visionProvider" placeholder="Optional"></label>
    </div>
    <div class="checks">
      <label><input name="gameOcr" type="checkbox"> Detect game events</label>
      <label><input name="transcribe" type="checkbox"> Run Whisper</label>
      <label><input name="captions" type="checkbox" checked> Burn captions</label>
      <label><input name="musicEnabled" type="checkbox" checked> Select music</label>
      <label><input name="ffmpegRender" type="checkbox" checked> Render review MP4</label>
      <label><input name="davinci" type="checkbox"> Import into DaVinci</label>
      <label><input name="davinciRender" type="checkbox"> Render in DaVinci</label>
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
form.addEventListener('submit', async event => {
  event.preventDefault(); const data = new FormData(form);
  const optional = name => data.get(name) || null;
  const list = name => String(data.get(name)||'').split(',').map(value=>value.trim()).filter(Boolean);
  const payload = {source:data.get('source'), output_directory:data.get('output'),
    brief:{title:data.get('title'),content_kind:data.get('kind'),audience:data.get('audience'),
      platform:data.get('platform'),
      target_duration_seconds:Number(data.get('duration')),aspect_ratio:data.get('ratio'),
      pace:data.get('pace'),mood:data.get('mood'),clip_count:Number(data.get('clipCount')),
      captions:data.has('captions'),music:data.has('musicEnabled'),color_look:data.get('colorLook'),
      autonomy:data.get('autonomy'),
      mandatory_event_types:list('mandatoryEvents'),excluded_event_types:list('excludedEvents')},
    transcript:optional('transcript'),music_catalog:optional('music'),sound_catalog:optional('sounds'),
    events:optional('events'),
    game_pack:optional('gamePack'),
    vision_provider:optional('visionProvider'),
    game_ocr:data.has('gameOcr'),transcribe:data.has('transcribe'),
    whisper_model:data.get('whisperModel'),language:optional('language'),
    style_profile_id:optional('styleProfile'),ffmpeg_render:data.has('ffmpegRender'),davinci:data.has('davinci'),
    davinci_render:data.has('davinciRender')};
  const response = await fetch('/api/jobs',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)
  }); const result=await response.json();
  if(!response.ok){alert(result.error);return;} refresh();
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
  return `${preview}${selected}<div class="artifact-links">${links}</div>${variantPanel}${revisionPanel(job)}`;}
async function refresh(){const response=await fetch('/api/jobs');const data=await response.json();
  jobs.innerHTML=data.jobs.map(job=>`<article><strong>${h(job.kind)}</strong> · <strong>${h(job.status)}</strong> · ${h(job.stage)}
    <progress value="${job.progress}" max="1"></progress>
    ${job.error?`<p class="error">${h(job.error)}</p>`:''}
    ${['queued','running','cancelling'].includes(job.status)?
      `<button class="cancel" onclick="cancelJob('${h(job.job_id)}')">Cancel</button>`:''}
    ${jobOutputs(job)}</article>`).join('');}
async function refreshApprovals(){const response=await fetch('/api/approvals');const data=await response.json();
  approvals.innerHTML=data.approvals?.length?`<h2>Actions awaiting your approval</h2>`+
    data.approvals.map(item=>{const action=item.action;const adapter=action.arguments?.adapter_id||'application';
      const command=action.arguments?.command||action.kind;return `<article><strong>${h(adapter)}</strong> · ${h(command)}
      <p>Expires ${h(new Date(item.expires_at*1000).toLocaleTimeString())}</p>
      <pre><code>${h(JSON.stringify(action.arguments?.arguments||{},null,2))}</code></pre>
      <button onclick="decideApproval('${h(item.approval_id)}','approve')">Approve exact action</button>
      <button onclick="decideApproval('${h(item.approval_id)}','reject')">Reject</button></article>`;}).join('):'';}
async function decideApproval(approvalId,decision){const response=await fetch(
  `/api/approvals/${approvalId}/${decision}`,{method:'POST',headers:{'content-type':'application/json'}});
  const result=await response.json();if(!response.ok){alert(result.error);return;}refreshApprovals();}
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
refresh();refreshApprovals();loadStyleProfiles();setInterval(()=>{refreshApprovals();
  if(!document.querySelector('.revision-form:focus-within'))refresh();},2000);
</script></body></html>"""
