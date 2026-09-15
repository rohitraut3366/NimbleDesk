from pathlib import Path
from typing import Any

from nimbledesk.creative.davinci import execute_in_davinci
from nimbledesk.creative.models import (
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)


class FakeTimeline:
    def GetName(self) -> str:
        return "Imported timeline"


class FakeMediaPool:
    def __init__(self) -> None:
        self.imported: Path | None = None

    def ImportTimelineFromFile(self, path: str, options: dict[str, Any]) -> FakeTimeline:
        self.imported = Path(path)
        assert options["importSourceClips"] is True
        return FakeTimeline()


class FakeProject:
    def __init__(self, output_directory: Path) -> None:
        self.output_directory = output_directory
        self.media_pool = FakeMediaPool()
        self.settings: dict[str, Any] = {}

    def GetName(self) -> str:
        return "Fixture project"

    def GetMediaPool(self) -> FakeMediaPool:
        return self.media_pool

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:
        return True

    def SetCurrentRenderFormatAndCodec(self, format_name: str, codec: str) -> bool:
        return format_name == "mp4" and codec == "H264"

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:
        self.settings = settings
        return True

    def AddRenderJob(self) -> str:
        return "job-1"

    def StartRendering(self, job_id: str) -> bool:
        output = Path(self.settings["TargetDir"]) / f"{self.settings['CustomName']}.mp4"
        output.write_bytes(b"rendered")
        return job_id == "job-1"

    def IsRenderingInProgress(self) -> bool:
        return False

    def GetRenderJobStatus(self, job_id: str) -> dict[str, str]:
        return {"JobStatus": "Complete"}


class FakeProjectManager:
    def __init__(self, project: FakeProject) -> None:
        self.project = project

    def GetCurrentProject(self) -> FakeProject:
        return self.project

    def CreateProject(self, name: str) -> FakeProject:
        return self.project


class FakeResolve:
    def __init__(self, project: FakeProject) -> None:
        self.manager = FakeProjectManager(project)

    def GetProjectManager(self) -> FakeProjectManager:
        return self.manager


def test_davinci_adapter_imports_timeline_and_validates_render(tmp_path: Path) -> None:
    source_range = TimeRange(start_seconds=1, end_seconds=3)
    plan = EditPlan(
        source_path=tmp_path / "source.mp4",
        brief=CreativeBrief(title="Fixture project", captions=False, music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=tmp_path / "source.mp4",
                source_range=source_range,
                timeline_start_seconds=0,
                speed=SpeedTreatment(rate=1, rationale="preserve timing"),
                visual=VisualTreatment(rationale="straight cut"),
                score=1,
                evidence=(
                    Evidence(
                        analyzer="fixture",
                        analyzer_version="1",
                        confidence=1,
                        description="known event",
                        source_range=source_range,
                    ),
                ),
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )
    timeline_path = tmp_path / "timeline.fcpxml"
    timeline_path.write_text("<fcpxml />", encoding="utf-8")
    project = FakeProject(tmp_path)

    result = execute_in_davinci(FakeResolve(project), plan, timeline_path, tmp_path)

    assert project.media_pool.imported == timeline_path
    assert result.timeline_name == "Imported timeline"
    assert result.render_job_id == "job-1"
    assert result.render_path == tmp_path / "davinci-final.mp4"
