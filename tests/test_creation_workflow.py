from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.creative.automatic import (
    AutomaticCapability,
    AutomaticIntelligenceReport,
    AutomaticIntelligenceSelection,
)
from nimbledesk.creative.models import ContentKind, CreativeBrief, TimeRange, TranscriptSegment
from nimbledesk.creative.workflow import (
    CreationWorkflow,
    _bind_event_moments,
    _merge_events,
    _resolve_content_kind,
)
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.media.models import TimelineEvent


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
    assert result.cue_sheet_path is not None and result.cue_sheet_path.is_file()
    assert result.cue_sheet_csv_path is not None and result.cue_sheet_csv_path.is_file()
    assert result.variant_comparison_path is not None
    assert result.variant_comparison_path.is_file()
    assert result.verification_path is not None
    assert result.verification_path.is_file()
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
def test_creation_workflow_continues_after_automatic_component_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    report = AutomaticIntelligenceReport(
        enabled=True,
        capabilities=(
            AutomaticCapability(
                capability="game_ocr", status="enabled", detail="test discovery"
            ),
        ),
    )
    monkeypatch.setattr(
        "nimbledesk.creative.workflow.resolve_automatic_intelligence",
        lambda *_args, **_kwargs: AutomaticIntelligenceSelection(
            game_ocr=True,
            transcribe=False,
            vision_provider=None,
            music_catalog=None,
            sound_catalog=None,
            report=report,
        ),
    )
    monkeypatch.setattr(
        "nimbledesk.creative.workflow.detect_game_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("OCR engine crashed")),
    )

    result = CreationWorkflow().create(
        source,
        tmp_path / "output",
        CreativeBrief(content_kind="gameplay", captions=False, music=False),
        automatic_intelligence=True,
        render=False,
    )

    assert result.automatic_intelligence_path is not None
    automatic_report = json.loads(result.automatic_intelligence_path.read_text(encoding="utf-8"))
    assert automatic_report["capabilities"][0]["status"] == "failed"
    assert automatic_report["capabilities"][0]["detail"] == "OCR engine crashed"
    assert result.plan_path.is_file()


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


def test_required_event_is_bound_to_source_range_for_final_validation() -> None:
    brief = CreativeBrief(mandatory_event_types=("clutch",))
    events = (
        TimelineEvent(
            time_seconds=42,
            event_type="clutch",
            label="One versus four clutch",
            importance=0.95,
        ),
    )

    bound = _bind_event_moments(brief, events, source_duration_seconds=60)

    assert len(bound.mandatory_moments) == 1
    assert bound.mandatory_moments[0].label == "One versus four clutch"
    assert bound.mandatory_moments[0].start_seconds == 41
    assert bound.mandatory_moments[0].end_seconds == 43


def test_remote_vision_requires_explicit_frame_data_permission(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    provider = tmp_path / "remote-vision.json"
    provider.write_text(
        json.dumps(
            {
                "provider_id": "remote-vision",
                "model": "vision-model",
                "command": ["provider", "{request}", "{response}"],
                "execution_location": "remote",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not allow frames to leave the laptop"):
        CreationWorkflow().create(
            source,
            tmp_path / "output",
            CreativeBrief(captions=False, music=False),
            vision_provider=provider,
            render=False,
        )


def test_autonomy_blocks_unapproved_render_and_editor_execution(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"

    with pytest.raises(ValueError, match="requires plan approval"):
        CreationWorkflow().create(
            source,
            tmp_path / "review-first",
            CreativeBrief(autonomy="review_before_render"),
            render=True,
        )
    with pytest.raises(ValueError, match="does not allow editor execution"):
        CreationWorkflow().create(
            source,
            tmp_path / "editor",
            CreativeBrief(autonomy="render_review"),
            render=False,
            execute_davinci=True,
        )


def test_semantic_event_sources_are_fused_without_losing_evidence() -> None:
    events = _merge_events(
        (
            TimelineEvent(
                time_seconds=10,
                event_type="grenade_kill",
                label="Grenade marker",
                importance=0.8,
                provenance=("game-pack:ocr",),
                evidence=("kill feed text",),
            ),
            TimelineEvent(
                time_seconds=10.5,
                event_type="grenade_kill",
                label="Grenade double kill",
                importance=0.9,
                provenance=("vision:model",),
                evidence=("throw and two elimination markers",),
            ),
        )
    )

    assert len(events) == 1
    assert events[0].time_seconds == pytest.approx(10.265, abs=0.001)
    assert events[0].importance == 0.98
    assert events[0].provenance == ("game-pack:ocr", "vision:model")
    assert len(events[0].evidence) == 2
