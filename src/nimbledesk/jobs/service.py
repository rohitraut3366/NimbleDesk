from __future__ import annotations

import mimetypes
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.automatic import AutomaticIntelligenceReport
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
    automatic_intelligence: bool = True
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
    session_id: str | None = None
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
            response["automatic_capabilities"] = _automatic_capabilities(self.state)
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

    def submit(self, request: CreateJobRequest, session_id: str | None = None) -> JobRecord:
        source = request.source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source video does not exist: {source}")
        return self._submit_new(request, session_id=session_id)

    def submit_revision(
        self,
        parent_job_id: str,
        request: ReviseJobRequest,
        session_id: str | None = None,
    ) -> JobRecord:
        plan_path = request.plan.expanduser().resolve()
        if not plan_path.is_file():
            raise FileNotFoundError(f"edit plan does not exist: {plan_path}")
        return self._submit_new(
            request,
            session_id=session_id,
            kind="revision",
            parent_job_id=parent_job_id,
        )

    def submit_photo(
        self, request: PhotoJobRequest, session_id: str | None = None
    ) -> JobRecord:
        source = request.source.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"photo source does not exist: {source}")
        return self._submit_new(request, session_id=session_id, kind="photo")

    def _submit_new(
        self,
        request: CreateJobRequest | ReviseJobRequest | PhotoJobRequest,
        *,
        session_id: str | None,
        kind: Literal["create", "revision", "photo"] = "create",
        parent_job_id: str | None = None,
    ) -> JobRecord:
        now = time.time()
        state = PersistedJob(
            job_id=str(uuid4()),
            session_id=session_id,
            kind=kind,
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

    def close(self, *, cancel_running: bool = False) -> None:
        if cancel_running:
            with self._lock:
                job_ids = tuple(self._jobs)
            for job_id in job_ids:
                self.cancel(job_id)
        self._executor.shutdown(wait=True, cancel_futures=True)
        if cancel_running:
            with self._lock:
                jobs = tuple(self._jobs.values())
            for job in jobs:
                with job.lock:
                    if job.state.status not in {
                        "cancelled",
                        "completed",
                        "failed",
                        "interrupted",
                    }:
                        job.state.status = "interrupted"
                        job.state.stage = "interrupted during daemon shutdown"
                        job.state.updated_at = time.time()
                        interrupted = True
                    else:
                        interrupted = False
                if interrupted:
                    self._save(job)

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
            session_id=job.state.session_id,
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
                    automatic_intelligence=request.automatic_intelligence,
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


def artifact_paths(state: PersistedJob) -> dict[str, Path]:
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
            "automatic-intelligence": result.automatic_intelligence_path,
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
        for name, path in artifact_paths(state).items()
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


def _automatic_capabilities(state: PersistedJob) -> list[dict[str, object]]:
    if not isinstance(state.result, CreationResult):
        return []
    path = state.result.automatic_intelligence_path
    if path is None or not path.is_file():
        return []
    try:
        report = AutomaticIntelligenceReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return []
    return [capability.model_dump(mode="json") for capability in report.capabilities]
