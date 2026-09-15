from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.workflow import CreationWorkflow
from nimbledesk.media.ffmpeg import probe_media


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_creation_workflow_produces_plan_timeline_and_validated_render(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    output = tmp_path / "creation"
    brief = CreativeBrief(
        title="Fixture creation",
        target_duration_seconds=5,
        clip_count=1,
        captions=False,
        music=False,
    )

    result = CreationWorkflow().create(source, output, brief)

    assert result.plan_path.is_file()
    assert result.timeline_path.is_file()
    assert result.render_path is not None
    assert result.render_path.is_file()
    rendered = probe_media(result.render_path)
    assert rendered.width == 1920
    assert rendered.height == 1080
    assert rendered.has_audio


def test_creation_workflow_enforces_mandatory_events(tmp_path: Path) -> None:
    brief = CreativeBrief(
        title="Must include clutch",
        mandatory_event_types=("clutch",),
        captions=False,
        music=False,
    )

    with pytest.raises(ValueError, match="mandatory event types were not detected: clutch"):
        CreationWorkflow().create(tmp_path / "source.mp4", tmp_path / "output", brief)
