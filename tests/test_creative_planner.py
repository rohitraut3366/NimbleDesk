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
    assert plan.music_cue is not None
    assert not plan.review_items
    parsed = ElementTree.parse(timeline)
    assert parsed.find(".//project").attrib["name"] == "Best round"
    assert len(parsed.findall(".//asset-clip")) == 3
