from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import EditPlan
from nimbledesk.media.process import ProcessCancelled


class DaVinciError(RuntimeError):
    pass


class DaVinciResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_name: str
    timeline_name: str
    render_job_id: str | None = None
    render_path: Path | None = None


class DaVinciWorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    result: DaVinciResult | None = None
    error: str | None = None


def execute_davinci_isolated(
    plan_path: Path,
    timeline_path: Path,
    output_directory: Path,
    *,
    render: bool = True,
    timeout_seconds: float = 3_600,
    cancelled: Callable[[], bool] | None = None,
    worker_command: tuple[str, ...] | None = None,
) -> DaVinciResult:
    command_prefix = worker_command or (
        sys.executable,
        "-m",
        "nimbledesk.creative.davinci_worker",
    )
    with tempfile.TemporaryDirectory(prefix="nimbledesk-davinci-") as temporary:
        working = Path(temporary)
        result_path = working / "result.json"
        cancel_path = working / "cancel"
        command = [
            *command_prefix,
            str(plan_path.resolve()),
            str(timeline_path.resolve()),
            str(output_directory.resolve()),
            "1" if render else "0",
            str(timeout_seconds),
            str(result_path),
            str(cancel_path),
        ]
        allowed_environment = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "TMPDIR",
            "RESOLVE_SCRIPT_API",
            "RESOLVE_SCRIPT_LIB",
        }
        environment = {
            key: value for key, value in os.environ.items() if key in allowed_environment
        }
        environment["PYTHONNOUSERSITE"] = "1"
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
            cwd=tempfile.gettempdir(),
        )
        cancel_requested = False
        deadline = time.monotonic() + timeout_seconds + 10
        while process.poll() is None:
            if cancelled and cancelled() and not cancel_requested:
                cancel_path.touch()
                cancel_requested = True
            if time.monotonic() >= deadline:
                cancel_path.touch()
                process.terminate()
                _wait_or_kill(process)
                raise DaVinciError("isolated DaVinci worker timed out")
            if cancel_requested and cancel_path.stat().st_mtime < time.time() - 5:
                process.terminate()
                _wait_or_kill(process)
                raise ProcessCancelled("DaVinci worker did not stop after cancellation")
            time.sleep(0.1)
        assert process.stderr is not None
        stderr = process.stderr.read(65_537)
        if cancel_requested:
            raise ProcessCancelled("creation was cancelled; DaVinci rendering was stopped")
        if len(stderr) > 65_536:
            raise DaVinciError("DaVinci worker stderr exceeded 64 kilobytes")
        if process.returncode != 0 and not result_path.is_file():
            message = stderr.decode("utf-8", errors="replace").strip()
            raise DaVinciError(message or f"DaVinci worker exited with {process.returncode}")
        if not result_path.is_file() or result_path.stat().st_size > 65_536:
            raise DaVinciError("DaVinci worker returned no valid bounded result")
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            response = DaVinciWorkerResponse.model_validate(payload)
        except Exception as error:
            raise DaVinciError("DaVinci worker returned malformed output") from error
        if not response.success or response.result is None:
            raise DaVinciError(response.error or "DaVinci worker failed")
        return response.result


def connect_to_resolve() -> Any:
    configured = os.getenv("RESOLVE_SCRIPT_API")
    search_paths = [Path(configured)] if configured else _default_module_paths()
    for path in search_paths:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.append(str(path))
    try:
        module = importlib.import_module("DaVinciResolveScript")
    except ImportError as error:
        raise DaVinciError(
            "DaVinciResolveScript was not found; set RESOLVE_SCRIPT_API to Resolve's "
            "Developer/Scripting/Modules directory"
        ) from error
    resolve = module.scriptapp("Resolve")
    if resolve is None:
        raise DaVinciError(
            "DaVinci Resolve is not available; open Resolve and enable external scripting"
        )
    return resolve


def execute_in_davinci(
    resolve: Any,
    plan: EditPlan,
    timeline_path: Path,
    output_directory: Path,
    *,
    render: bool = True,
    timeout_seconds: float = 3_600,
    cancelled: Callable[[], bool] | None = None,
) -> DaVinciResult:
    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise DaVinciError("DaVinci Resolve did not provide a project manager")
    project = project_manager.GetCurrentProject()
    if project is None or project.GetName() != plan.brief.title:
        project = project_manager.CreateProject(plan.brief.title)
    if project is None:
        raise DaVinciError(
            f"could not create project {plan.brief.title!r}; a project with that name may exist"
        )
    media_pool = project.GetMediaPool()
    if media_pool is None:
        raise DaVinciError("DaVinci Resolve did not provide a media pool")
    timeline = media_pool.ImportTimelineFromFile(
        str(timeline_path.resolve()),
        {
            "timelineName": plan.brief.title,
            "importSourceClips": True,
        },
    )
    if timeline is None:
        raise DaVinciError("DaVinci Resolve could not import the generated FCPXML timeline")
    if not project.SetCurrentTimeline(timeline):
        raise DaVinciError("DaVinci Resolve could not activate the imported timeline")
    _validate_and_apply_timeline(timeline, plan)
    timeline_name = str(timeline.GetName())
    if not render:
        return DaVinciResult(project_name=str(project.GetName()), timeline_name=timeline_name)

    output_directory.mkdir(parents=True, exist_ok=True)
    custom_name = "davinci-final"
    if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
        raise DaVinciError("DaVinci Resolve does not support the requested MP4/H.264 preset")
    settings = {
        "TargetDir": str(output_directory.resolve()),
        "CustomName": custom_name,
        "SelectAllFrames": True,
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": "aac",
        "AudioSampleRate": 48_000,
        "AudioBitDepth": 24,
    }
    if plan.captions:
        settings.update({"ExportSubtitle": True, "SubtitleFormat": "BurnIn"})
    if not project.SetRenderSettings(settings):
        raise DaVinciError("DaVinci Resolve rejected the render settings")
    job_id = project.AddRenderJob()
    if not job_id:
        raise DaVinciError("DaVinci Resolve could not add the render job")
    if not project.StartRendering(job_id):
        raise DaVinciError("DaVinci Resolve could not start the render job")
    deadline = time.monotonic() + timeout_seconds
    while project.IsRenderingInProgress():
        if cancelled and cancelled():
            project.StopRendering()
            raise ProcessCancelled("creation was cancelled; DaVinci rendering was stopped")
        if time.monotonic() >= deadline:
            project.StopRendering()
            raise DaVinciError("DaVinci Resolve render timed out and was stopped")
        time.sleep(0.5)
    status = project.GetRenderJobStatus(job_id)
    if isinstance(status, dict) and status.get("JobStatus") not in {"Complete", "Completed"}:
        raise DaVinciError(f"DaVinci Resolve render failed: {status}")
    render_path = output_directory / f"{custom_name}.mp4"
    if not render_path.is_file():
        raise DaVinciError(f"DaVinci Resolve reported completion but {render_path} is missing")
    return DaVinciResult(
        project_name=str(project.GetName()),
        timeline_name=timeline_name,
        render_job_id=str(job_id),
        render_path=render_path,
    )


def _validate_and_apply_timeline(timeline: Any, plan: EditPlan) -> None:
    try:
        video_tracks = int(timeline.GetTrackCount("video"))
        primary_items = list(timeline.GetItemListInTrack("video", 1))
    except (AttributeError, TypeError, ValueError) as error:
        raise DaVinciError("DaVinci Resolve did not expose the imported video timeline") from error
    if video_tracks < 1 or len(primary_items) < len(plan.segments):
        raise DaVinciError(
            "DaVinci Resolve imported fewer primary video clips than the approved edit plan"
        )
    for item, segment in zip(primary_items, plan.segments, strict=False):
        exposure_scale = 2 ** segment.visual.exposure_adjustment_stops
        cdl = {
            "NodeIndex": "1",
            "Slope": f"{exposure_scale:.6f} {exposure_scale:.6f} {exposure_scale:.6f}",
            "Offset": "0 0 0",
            "Power": "1 1 1",
            "Saturation": f"{segment.visual.saturation_multiplier:.6f}",
        }
        try:
            applied = item.SetCDL(cdl)
        except AttributeError as error:
            raise DaVinciError("DaVinci Resolve does not expose timeline color controls") from error
        if not applied:
            raise DaVinciError(f"DaVinci Resolve rejected color for {segment.segment_id}")
    if plan.captions:
        try:
            subtitle_count = sum(
                len(timeline.GetItemListInTrack("subtitle", track_index))
                for track_index in range(1, int(timeline.GetTrackCount("subtitle")) + 1)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise DaVinciError("DaVinci Resolve did not expose imported captions") from error
        if subtitle_count < len(plan.captions):
            raise DaVinciError(
                "DaVinci Resolve imported fewer captions than the approved edit plan"
            )


def _default_module_paths() -> list[Path]:
    if sys.platform == "darwin":
        return [
            Path(
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
                "Developer/Scripting/Modules"
            )
        ]
    if sys.platform == "win32":
        program_data = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
        return [
            program_data
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Support"
            / "Developer"
            / "Scripting"
            / "Modules"
        ]
    return [Path("/opt/resolve/Developer/Scripting/Modules")]


def _wait_or_kill(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
