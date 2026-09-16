# ruff: noqa: N802
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    return Path(os.environ["NIMBLEDESK_DAVINCI_FIXTURE_STATE"])


def _read_state() -> dict[str, Any]:
    path = _state_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write_state(**updates: object) -> None:
    state = _read_state()
    state.update(updates)
    _state_path().write_text(json.dumps(state), encoding="utf-8")


class TimelineItem:
    def SetCDL(self, settings: dict[str, str]) -> bool:
        _write_state(cdl=settings)
        return True


class Timeline:
    def __init__(self, name: str, caption_count: int) -> None:
        self.name = name
        self.caption_count = caption_count

    def GetName(self) -> str:
        return self.name

    def GetTrackCount(self, kind: str) -> int:
        return 1 if kind in {"video", "subtitle"} else 0

    def GetItemListInTrack(self, kind: str, _track_index: int) -> list[object]:
        if kind == "video":
            return [TimelineItem()]
        if kind == "subtitle":
            return [object() for _ in range(self.caption_count)]
        return []


class MediaPool:
    def __init__(self, project: Project) -> None:
        self.project = project

    def ImportTimelineFromFile(
        self, _path: str, options: dict[str, object]
    ) -> Timeline:
        timeline = Timeline(str(options["timelineName"]), self.project.caption_count)
        self.project.timelines.append(timeline)
        self.project.persist()
        return timeline


class Project:
    def __init__(self) -> None:
        self.title = os.environ["NIMBLEDESK_DAVINCI_FIXTURE_TITLE"]
        self.caption_count = int(os.environ["NIMBLEDESK_DAVINCI_FIXTURE_CAPTIONS"])
        self.timelines = [
            Timeline(name, self.caption_count) for name in _read_state().get("timelines", [])
        ]
        self.render_settings: dict[str, object] = {}

    def persist(self) -> None:
        _write_state(timelines=[timeline.name for timeline in self.timelines])

    def GetName(self) -> str:
        return self.title

    def GetMediaPool(self) -> MediaPool:
        return MediaPool(self)

    def GetTimelineCount(self) -> int:
        return len(self.timelines)

    def GetTimelineByIndex(self, index: int) -> Timeline | None:
        return self.timelines[index - 1] if 1 <= index <= len(self.timelines) else None

    def SetCurrentTimeline(self, _timeline: Timeline) -> bool:
        return True

    def SetCurrentRenderFormatAndCodec(self, container: str, codec: str) -> bool:
        return container == "mp4" and codec == "H264"

    def SetRenderSettings(self, settings: dict[str, object]) -> bool:
        self.render_settings = settings
        return True

    def AddRenderJob(self) -> str:
        return "fixture-render-job"

    def StartRendering(self, _job_id: str) -> bool:
        if os.getenv("NIMBLEDESK_DAVINCI_FIXTURE_RENDERING") != "1":
            source = Path(os.environ["NIMBLEDESK_DAVINCI_FIXTURE_SOURCE"])
            target = Path(str(self.render_settings["TargetDir"])) / "davinci-final.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return True

    def IsRenderingInProgress(self) -> bool:
        return os.getenv("NIMBLEDESK_DAVINCI_FIXTURE_RENDERING") == "1"

    def StopRendering(self) -> None:
        _write_state(stopped=True)

    def GetRenderJobStatus(self, _job_id: str) -> dict[str, str]:
        return {"JobStatus": "Complete"}


class ProjectManager:
    def __init__(self) -> None:
        self.project = Project()

    def GetCurrentProject(self) -> Project:
        return self.project

    def LoadProject(self, _name: str) -> Project:
        return self.project

    def CreateProject(self, _name: str) -> Project:
        return self.project

    def SaveProject(self) -> bool:
        self.project.persist()
        return True


class Resolve:
    def GetVersionString(self) -> str:
        return "20.2.1-fixture"

    def GetProjectManager(self) -> ProjectManager:
        return ProjectManager()


def scriptapp(name: str) -> Resolve | None:
    return Resolve() if name == "Resolve" else None
