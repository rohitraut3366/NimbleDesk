import csv
from datetime import date, timedelta
from pathlib import Path

from nimbledesk.creative.cue_sheet import build_cue_sheet, write_cue_sheet
from nimbledesk.creative.models import (
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    MusicAsset,
    MusicCue,
    SoundAsset,
    SoundCue,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.creative.validation import validate_edit_plan
from nimbledesk.media.models import MediaMetadata


def test_cue_sheet_exports_licensed_and_generated_asset_provenance(tmp_path: Path) -> None:
    plan, metadata = _plan_with_cues(tmp_path)

    sheet = build_cue_sheet(plan)
    json_path, csv_path = write_cue_sheet(plan, tmp_path / "output")

    assert [entry.cue_type for entry in sheet.entries] == ["music", "sound_effect"]
    assert sheet.entries[0].composer == "Fixture Composer"
    assert sheet.entries[0].allowed_platforms == ("youtube",)
    assert sheet.entries[1].generated
    assert sheet.entries[1].generation_model == "sound-model-v1"
    assert json_path.is_file()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert rows[0]["title"] == "Licensed score"
    assert rows[1]["generation_provider"] == "fixture-provider"
    assert validate_edit_plan(plan, metadata).valid


def test_validation_rejects_expired_or_wrong_platform_audio_license(tmp_path: Path) -> None:
    plan, metadata = _plan_with_cues(tmp_path)
    assert plan.music_cue is not None
    restricted_asset = plan.music_cue.asset.model_copy(
        update={
            "allowed_platforms": ("instagram",),
            "license_expires": date.today() - timedelta(days=1),
        }
    )
    plan = plan.model_copy(
        update={"music_cue": plan.music_cue.model_copy(update={"asset": restricted_asset})}
    )

    report = validate_edit_plan(plan, metadata)

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "music_platform_license",
        "music_license_expired",
    }


def _plan_with_cues(tmp_path: Path) -> tuple[EditPlan, MediaMetadata]:
    source = tmp_path / "source.mp4"
    music = tmp_path / "music.wav"
    sound = tmp_path / "impact.wav"
    source.write_bytes(b"source")
    music.write_bytes(b"music")
    sound.write_bytes(b"sound")
    timeline = TimeRange(start_seconds=0, end_seconds=5)
    plan = EditPlan(
        source_path=source,
        brief=CreativeBrief(
            title="Cue fixture",
            platform="youtube",
            target_duration_seconds=5,
        ),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=source,
                source_range=timeline,
                timeline_start_seconds=0,
                speed=SpeedTreatment(rationale="fixture"),
                visual=VisualTreatment(rationale="fixture"),
                score=1,
                evidence=(),
            ),
        ),
        music_cue=MusicCue(
            asset=MusicAsset(
                path=music,
                duration_seconds=10,
                title="Licensed score",
                artist="Fixture Artist",
                composer="Fixture Composer",
                license="paid production license",
                allowed_platforms=("youtube",),
            ),
            source_range=timeline,
            timeline_range=timeline,
            rationale="support the opening",
        ),
        sound_cues=(
            SoundCue(
                asset=SoundAsset(
                    path=sound,
                    duration_seconds=2,
                    title="Generated impact",
                    tags=("impact",),
                    license="generated commercial use",
                    generated=True,
                    generation_provider="fixture-provider",
                    generation_model="sound-model-v1",
                    generation_prompt="restrained impact",
                    generation_seed=42,
                ),
                source_range=TimeRange(start_seconds=0, end_seconds=1),
                timeline_range=TimeRange(start_seconds=1, end_seconds=2),
                segment_id="segment-001",
                purpose="accent reveal",
                rationale="fixture",
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )
    metadata = MediaMetadata(
        path=source,
        duration_seconds=5,
        width=1920,
        height=1080,
        frame_rate=30,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
    return plan, metadata
