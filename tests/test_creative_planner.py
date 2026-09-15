from pathlib import Path
from xml.etree import ElementTree

from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.models import (
    CreativeBrief,
    MusicAsset,
    TimeRange,
    TranscriptSegment,
)
from nimbledesk.creative.planner import build_edit_plan
from nimbledesk.media.models import (
    AnalysisConfig,
    HighlightCandidate,
    HighlightManifest,
    MediaMetadata,
)


def test_planner_builds_treatments_captions_music_and_davinci_timeline(tmp_path: Path) -> None:
    source = tmp_path / "gameplay.mp4"
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"catalog fixture")
    manifest = HighlightManifest(
        source=MediaMetadata(
            path=source,
            duration_seconds=120,
            width=1920,
            height=1080,
            frame_rate=60,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
        ),
        config=AnalysisConfig(),
        candidates=(
            HighlightCandidate(
                rank=1,
                start_seconds=10,
                end_seconds=25,
                peak_seconds=18,
                score=1,
                reasons=("timeline event: clutch",),
                event_labels=("One versus three clutch",),
            ),
            HighlightCandidate(
                rank=2,
                start_seconds=50,
                end_seconds=65,
                peak_seconds=58,
                score=0.75,
                reasons=("high visual motion",),
            ),
        ),
        clips=(),
    )
    transcript = TranscriptSegment(
        source_range=TimeRange(start_seconds=17, end_seconds=19),
        text="That was close!",
        speaker="Rohit",
    )
    music = MusicAsset(
        path=music_path,
        duration_seconds=90,
        title="Licensed action bed",
        mood=("exciting",),
        energy=0.8,
        license="user-owned",
    )
    brief = CreativeBrief(
        title="Best round",
        content_kind="gameplay",
        target_duration_seconds=30,
        pace="fast",
        mood="exciting",
        clip_count=2,
    )

    plan = build_edit_plan(manifest, brief, (transcript,), (music,))
    timeline = export_fcpxml(plan, tmp_path / "timeline.fcpxml")

    assert len(plan.segments) == 2
    assert plan.segments[0].role == "hook"
    assert plan.segments[0].speed.rate == 0.85
    assert plan.segments[1].speed.rate == 1.25
    assert plan.captions[0].text == "That was close!"
    assert plan.segments[0].visual.title == "Best round"
    assert plan.segments[0].visual.lower_third == "Rohit"
    assert plan.music_cue is not None
    assert not plan.review_items
    parsed = ElementTree.parse(timeline)
    assert parsed.find(".//project").attrib["name"] == "Best round"
    assert len(parsed.findall(".//asset-clip")) == 5
    assert (tmp_path / "graphics" / "segment-001-title.png").is_file()
    assert (tmp_path / "graphics" / "segment-001-lower-third.png").is_file()


def test_planner_splits_long_caption_into_readable_proportional_cues(tmp_path: Path) -> None:
    source = tmp_path / "talk.mp4"
    manifest = HighlightManifest(
        source=MediaMetadata(
            path=source,
            duration_seconds=20,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
        ),
        config=AnalysisConfig(),
        candidates=(
            HighlightCandidate(
                rank=1,
                start_seconds=2,
                end_seconds=12,
                peak_seconds=7,
                score=1,
                reasons=("spoken explanation",),
            ),
        ),
        clips=(),
    )
    transcript = TranscriptSegment(
        source_range=TimeRange(start_seconds=2, end_seconds=12),
        text=(
            "This deliberately long transcript needs several readable subtitle cues so viewers "
            "can follow every sentence without a wall of text covering the video frame."
        ),
        speaker="Host",
    )
    brief = CreativeBrief(
        content_kind="talking_head",
        target_duration_seconds=12,
        clip_count=1,
        music=False,
    )

    plan = build_edit_plan(manifest, brief, (transcript,))

    assert len(plan.captions) >= 2
    assert all(len(line) <= 42 for cue in plan.captions for line in cue.text.splitlines())
    assert all(cue.speaker == "Host" for cue in plan.captions)
    assert all(cue.segment_id == "segment-001" for cue in plan.captions)
    assert plan.captions[0].source_range is not None
    assert plan.captions[-1].source_range is not None
    assert plan.captions[0].source_range.start_seconds == 2
    assert plan.captions[-1].source_range.end_seconds == 12
    for previous, current in zip(plan.captions, plan.captions[1:], strict=False):
        assert previous.timeline_range.end_seconds == current.timeline_range.start_seconds
        assert previous.source_range is not None
        assert current.source_range is not None
        assert previous.source_range.end_seconds == current.source_range.start_seconds
