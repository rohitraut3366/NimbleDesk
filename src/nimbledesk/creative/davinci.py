from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import EditPlan


class DaVinciError(RuntimeError):
    pass


class DaVinciResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_name: str
    timeline_name: str
    render_job_id: str | None = None
    render_path: Path | None = None


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
    project.SetCurrentTimeline(timeline)
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
    if not project.SetRenderSettings(settings):
        raise DaVinciError("DaVinci Resolve rejected the render settings")
    job_id = project.AddRenderJob()
    if not job_id:
        raise DaVinciError("DaVinci Resolve could not add the render job")
    if not project.StartRendering(job_id):
        raise DaVinciError("DaVinci Resolve could not start the render job")
    deadline = time.monotonic() + timeout_seconds
    while project.IsRenderingInProgress():
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
