from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from nimbledesk.analysis.index import ContentIndexer, _spatial_motion
from nimbledesk.analysis.models import ContentIndex
from nimbledesk.creative.models import TimeRange, TranscriptSegment
from nimbledesk.media.models import TimelineEvent


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_content_index_is_time_aligned_semantic_and_resumable(tmp_path: Path) -> None:
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
            "testsrc2=size=320x180:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
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
    transcript = TranscriptSegment(
        source_range=TimeRange(start_seconds=0.5, end_seconds=1.5),
        text="Wow, that was insane!",
        confidence=0.95,
    )
    event = TimelineEvent(
        time_seconds=2,
        event_type="kill",
        label="Eliminated opponent",
        importance=0.9,
    )
    cache = tmp_path / "index"

    first = ContentIndexer().build(source, cache, transcripts=(transcript,), events=(event,))
    second = ContentIndexer().build(source, cache, transcripts=(transcript,), events=(event,))

    assert {track.name for track in first.tracks} == {
        "motion",
        "audio",
        "color",
        "shots",
        "semantic",
    }
    assert first.track("motion").points
    assert first.track("audio").points
    assert first.track("color").points
    assert {
        "motion_center_x",
        "motion_center_y",
        "spatial_motion",
    } <= set(first.track("color").points[-1].metrics)
    assert all(
        point.source_range.duration.value > 0
        for track in first.tracks
        for point in track.points
    )
    assert {event.event_type for event in first.semantic_events} >= {"kill", "reaction"}
    assert set(second.cache_hits) == {"motion", "audio", "color", "shots", "semantic"}
    persisted = ContentIndex.model_validate_json(
        (cache / "content_index.json").read_text(encoding="utf-8")
    )
    assert persisted.asset.sha256 == first.asset.sha256


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_transcript_change_only_invalidates_semantic_track(tmp_path: Path) -> None:
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
    cache = tmp_path / "index"
    ContentIndexer().build(source, cache)
    transcript = TranscriptSegment(
        source_range=TimeRange(start_seconds=0, end_seconds=0.5),
        text="How to begin?",
    )

    rebuilt = ContentIndexer().build(source, cache, transcripts=(transcript,))

    assert set(rebuilt.cache_hits) == {"motion", "audio", "color", "shots"}
    assert "question_or_hook" in {event.event_type for event in rebuilt.semantic_events}


def test_spatial_motion_centroid_tracks_off_center_activity() -> None:
    previous = np.zeros((4, 8), dtype=np.float64)
    current = previous.copy()
    current[:, 6:] = 1

    center_x, center_y, strength = _spatial_motion(current, previous)

    assert center_x > 0.8
    assert center_y == 0.5
    assert strength == 1
