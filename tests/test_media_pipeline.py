from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.media.models import AnalysisConfig
from nimbledesk.media.pipeline import HighlightPipeline


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_pipeline_creates_valid_clip_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output_directory = tmp_path / "highlights"
    command = [
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
    ]
    subprocess.run(command, check=True)

    manifest = HighlightPipeline().analyze_and_render(
        source,
        output_directory,
        AnalysisConfig(
            sample_frames_per_second=1,
            audio_window_seconds=1,
            lead_in_seconds=1,
            aftermath_seconds=1,
            highlight_count=1,
            minimum_peak_separation_seconds=1,
            output_width=320,
        ),
    )

    assert len(manifest.clips) == 1
    assert manifest.clips[0].output_path.is_file()
    assert manifest.clips[0].width == 320
    assert (output_directory / "highlights.json").is_file()
