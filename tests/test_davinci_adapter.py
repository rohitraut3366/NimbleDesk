import sys
from pathlib import Path
from typing import Any

import pytest

from nimbledesk.creative.davinci import execute_davinci_isolated, execute_in_davinci
from nimbledesk.creative.models import (
    CaptionCue,
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.media.process import ProcessCancelled


class FakeTimelineItem:
    def __init__(self) -> None:
        self.cdl: dict[str, str] | None = None

    def SetCDL(self, cdl: dict[str, str]) -> bool:
        self.cdl = cdl
        return True


class FakeTimeline:
    def __init__(self) -> None:
        self.primary_items = [FakeTimelineItem()]
        self.subtitle_items = [FakeTimelineItem()]

    def GetName(self) -> str:
        return "Imported timeline"

    def GetTrackCount(self, track_type: str) -> int:
        return 1 if track_type in {"video", "subtitle"} else 0

    def GetItemListInTrack(self, track_type: str, index: int) -> list[FakeTimelineItem]:
        if track_type == "video" and index == 1:
            return self.primary_items
        if track_type == "subtitle" and index == 1:
            return self.subtitle_items
        return []


class FakeMediaPool:
    def __init__(self) -> None:
        self.imported: Path | None = None
        self.timeline = FakeTimeline()

    def ImportTimelineFromFile(self, path: str, options: dict[str, Any]) -> FakeTimeline:
        self.imported = Path(path)
        assert options["importSourceClips"] is True
        return self.timeline


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


class FakeRenderingProject(FakeProject):
    stopped = False

    def StartRendering(self, job_id: str) -> bool:
        return True

    def IsRenderingInProgress(self) -> bool:
        return True

    def StopRendering(self) -> None:
        self.stopped = True


def test_davinci_adapter_imports_timeline_and_validates_render(tmp_path: Path) -> None:
    source_range = TimeRange(start_seconds=1, end_seconds=3)
    plan = EditPlan(
        source_path=tmp_path / "source.mp4",
        brief=CreativeBrief(title="Fixture project", captions=True, music=False),
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
        captions=(
            CaptionCue(
                timeline_range=TimeRange(start_seconds=0, end_seconds=1),
                text="Editable caption",
                segment_id="segment-001",
                source_range=TimeRange(start_seconds=1, end_seconds=2),
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
    assert project.settings["ExportSubtitle"] is True
    assert project.settings["SubtitleFormat"] == "BurnIn"
    assert project.media_pool.timeline.primary_items[0].cdl == {
        "NodeIndex": "1",
        "Slope": "1.000000 1.000000 1.000000",
        "Offset": "0 0 0",
        "Power": "1 1 1",
        "Saturation": "1.000000",
    }


def test_davinci_adapter_stops_active_render_when_cancelled(tmp_path: Path) -> None:
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
                evidence=(),
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )
    timeline_path = tmp_path / "timeline.fcpxml"
    timeline_path.write_text("<fcpxml />", encoding="utf-8")
    project = FakeRenderingProject(tmp_path)

    with pytest.raises(ProcessCancelled):
        execute_in_davinci(
            FakeResolve(project),
            plan,
            timeline_path,
            tmp_path,
            cancelled=lambda: True,
        )

    assert project.stopped


def test_isolated_davinci_worker_returns_bounded_typed_result(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    timeline_path = tmp_path / "timeline.fcpxml"
    plan_path.write_text("{}", encoding="utf-8")
    timeline_path.write_text("<fcpxml />", encoding="utf-8")
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import json
import sys
json.dump({
  'success': True,
  'result': {
    'project_name': 'Isolated project',
    'timeline_name': 'Imported timeline',
    'render_job_id': None,
    'render_path': None
  },
  'error': None
}, open(sys.argv[6], 'w', encoding='utf-8'))
""".strip(),
        encoding="utf-8",
    )

    result = execute_davinci_isolated(
        plan_path,
        timeline_path,
        tmp_path,
        render=False,
        worker_command=(sys.executable, str(worker)),
    )

    assert result.project_name == "Isolated project"
    assert result.timeline_name == "Imported timeline"


def test_isolated_davinci_worker_cooperatively_cancels(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    timeline_path = tmp_path / "timeline.fcpxml"
    plan_path.write_text("{}", encoding="utf-8")
    timeline_path.write_text("<fcpxml />", encoding="utf-8")
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import json
import sys
import time
from pathlib import Path
cancel = Path(sys.argv[7])
while not cancel.exists():
    time.sleep(0.01)
json.dump({'success': False, 'result': None, 'error': 'cancelled'},
          open(sys.argv[6], 'w', encoding='utf-8'))
raise SystemExit(1)
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ProcessCancelled, match="DaVinci rendering was stopped"):
        execute_davinci_isolated(
            plan_path,
            timeline_path,
            tmp_path,
            worker_command=(sys.executable, str(worker)),
            cancelled=lambda: True,
        )
