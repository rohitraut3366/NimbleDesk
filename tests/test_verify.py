import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.creative.models import (
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.creative.verify import verify_render


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_render_verifier_detects_excessive_black_video(tmp_path: Path) -> None:
    render = tmp_path / "black.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=640x360:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(render),
        ],
        check=True,
    )
    segment = EditSegment(
        segment_id="segment-001",
        role="hook",
        source_path=render,
        source_range=TimeRange(start_seconds=0, end_seconds=2),
        timeline_start_seconds=0,
        speed=SpeedTreatment(rationale="fixture"),
        visual=VisualTreatment(rationale="fixture"),
        score=1,
        evidence=(),
    )
    plan = EditPlan(
        source_path=render,
        brief=CreativeBrief(target_duration_seconds=5, captions=False, music=False),
        segments=(segment,),
        delivery=DeliverySpec(width=640, height=360, frame_rate=24),
    )
    report_path = tmp_path / "verification.json"

    report = verify_render(plan, render, report_path)

    assert not report.valid
    assert "excessive_black" in {issue.code for issue in report.issues}
    assert report.checks["black_seconds"] == pytest.approx(2, abs=0.1)
    assert report_path.is_file()
