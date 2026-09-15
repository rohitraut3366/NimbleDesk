from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.creative.models import ContentKind, CreativeBrief, TimeRange, TranscriptSegment
from nimbledesk.creative.workflow import CreationWorkflow, _resolve_content_kind
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
    music = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            str(music),
        ],
        check=True,
    )
    catalog = tmp_path / "music.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "path": str(music),
                    "duration_seconds": 4,
                    "title": "Music",
                    "mood": ["engaging"],
                    "bpm": 120,
                    "energy": 0.6,
                    "instrumental": True,
                    "license": "test fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    sound = tmp_path / "whoosh.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=0.5",
            str(sound),
        ],
        check=True,
    )
    sound_catalog = tmp_path / "sounds.json"
    sound_catalog.write_text(
        json.dumps(
            [
                {
                    "path": str(sound),
                    "duration_seconds": 0.5,
                    "title": "Licensed whoosh",
                    "tags": ["whoosh", "hook"],
                    "license": "test fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "creation"
    brief = CreativeBrief(
        title="Fixture creation",
        target_duration_seconds=5,
        clip_count=1,
        captions=False,
        aspect_ratio="9:16",
    )

    result = CreationWorkflow().create(
        source,
        output,
        brief,
        music_catalog=catalog,
        sound_catalog=sound_catalog,
    )

    assert result.plan_path.is_file()
    assert result.validation_path.is_file()
    assert result.content_index_path.is_file()
    assert result.timeline_path.is_file()
    assert result.render_path is not None
    assert result.render_path.is_file()
    rendered = probe_media(result.render_path)
    assert rendered.width == 1080
    assert rendered.height == 1920
    assert rendered.has_audio
    assert "sampled luminance" in result.plan.segments[0].visual.rationale
    assert result.plan.segments[0].visual.reframe_mode == "spatial_motion"
    assert "measured motion center" in result.plan.segments[0].visual.rationale
    assert result.plan.music_cue is not None
    assert result.plan.music_cue.beat_interval_seconds == 0.5
    assert len(result.plan.sound_cues) == 1
    assert result.plan.sound_cues[0].asset.title == "Licensed whoosh"
    assert 'audioRole="effects"' in result.timeline_path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_creation_workflow_enforces_mandatory_events(tmp_path: Path) -> None:
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
            "color=black:size=320x180:rate=30:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    brief = CreativeBrief(
        title="Must include clutch",
        mandatory_event_types=("clutch",),
        captions=False,
        music=False,
    )

    with pytest.raises(ValueError, match="mandatory event types were not detected: clutch"):
        CreationWorkflow().create(source, tmp_path / "output", brief)


def test_content_kind_detection_does_not_mistake_transcript_semantics_for_gameplay() -> None:
    transcript = TranscriptSegment(
        source_range=TimeRange(start_seconds=0, end_seconds=1),
        text="How did we do that?",
    )

    resolved = _resolve_content_kind(CreativeBrief(), (), (transcript,))

    assert resolved.content_kind is ContentKind.TALKING_HEAD
