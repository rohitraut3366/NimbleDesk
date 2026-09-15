from pathlib import Path

from nimbledesk.creative.models import (
    AccessibilityRequirements,
    BrandRules,
    CaptionCue,
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    PlanRevisionRequest,
    SoundAsset,
    SoundCue,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.creative.revision import revise_edit_plan
from nimbledesk.creative.validation import validate_edit_plan
from nimbledesk.media.models import MediaMetadata


def test_revision_preserves_locked_segment_and_reflows_to_target(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    revision = revise_edit_plan(
        plan,
        PlanRevisionRequest(
            target_duration_seconds=15,
            pace="fast",
            color_look="vivid",
            lock_segment_ids=("segment-002",),
        ),
    )

    assert revision.plan.duration_seconds == 15
    assert len(revision.plan.segments) == 2
    first, locked = revision.plan.segments
    assert first.speed.rate == 1.25
    assert first.visual.color_look == "vivid"
    assert locked.locked
    assert locked.speed == plan.segments[1].speed
    assert locked.visual == plan.segments[1].visual
    assert locked.timeline_start_seconds == 5
    assert revision.plan.captions[0].timeline_range.start_seconds == 0.8
    assert revision.changes
    report = validate_edit_plan(revision.plan, _metadata(tmp_path))
    assert report.valid


def test_validation_rejects_source_overrun_and_timeline_gap(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    broken = plan.model_copy(
        update={
            "segments": (
                plan.segments[0],
                plan.segments[1].model_copy(
                    update={
                        "timeline_start_seconds": 14,
                        "source_range": TimeRange(start_seconds=20, end_seconds=40),
                    }
                ),
            )
        }
    )

    report = validate_edit_plan(broken, _metadata(tmp_path))

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "source_bounds",
        "timeline_gap_or_overlap",
    }


def test_validation_blocks_missing_required_brand_and_accessibility_assets(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    plan = plan.model_copy(
        update={
            "captions": (),
            "brief": plan.brief.model_copy(
                update={
                    "brand": BrandRules(required=True),
                    "accessibility": AccessibilityRequirements(
                        captions_required=True,
                        audio_description_required=True,
                    ),
                }
            )
        }
    )

    report = validate_edit_plan(plan, _metadata(tmp_path))

    assert not report.valid
    assert {issue.code for issue in report.issues} >= {
        "brand_assets_missing",
        "required_captions_missing",
        "audio_description_unavailable",
    }


def test_validation_accepts_cross_dissolve_overlap(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    second = plan.segments[1].model_copy(
        update={
            "timeline_start_seconds": 9.65,
            "visual": plan.segments[1].visual.model_copy(
                update={"transition_in": "cross_dissolve"}
            ),
        }
    )
    third = plan.segments[2].model_copy(update={"timeline_start_seconds": 19.65})
    plan = plan.model_copy(update={"segments": (plan.segments[0], second, third)})

    report = validate_edit_plan(plan, _metadata(tmp_path))

    assert report.valid
    assert report.measured_duration_seconds == 29.65


def test_revision_preserves_and_reflows_cross_dissolve(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    second = plan.segments[1].model_copy(
        update={
            "timeline_start_seconds": 9.65,
            "visual": plan.segments[1].visual.model_copy(
                update={"transition_in": "cross_dissolve"}
            ),
        }
    )
    third = plan.segments[2].model_copy(update={"timeline_start_seconds": 19.65})
    plan = plan.model_copy(update={"segments": (plan.segments[0], second, third)})

    revision = revise_edit_plan(
        plan,
        PlanRevisionRequest(target_duration_seconds=25),
    )

    assert revision.plan.segments[1].timeline_start_seconds == 9.65
    assert revision.plan.segments[1].visual.transition_in == "cross_dissolve"
    assert revision.plan.segments[2].timeline_start_seconds == 19.65
    assert revision.plan.duration_seconds == 25
    assert validate_edit_plan(revision.plan, _metadata(tmp_path)).valid


def test_revision_remaps_sound_cue_with_its_segment(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    sound_path = tmp_path / "impact.wav"
    sound_path.write_bytes(b"fixture")
    plan = plan.model_copy(
        update={
            "sound_cues": (
                SoundCue(
                    asset=SoundAsset(
                        path=sound_path,
                        duration_seconds=2,
                        title="Impact",
                        tags=("impact",),
                        license="fixture",
                    ),
                    source_range=TimeRange(start_seconds=0, end_seconds=1),
                    timeline_range=TimeRange(start_seconds=12, end_seconds=13),
                    segment_id="segment-002",
                    purpose="accent payoff",
                    rationale="fixture",
                ),
            )
        }
    )

    revision = revise_edit_plan(
        plan,
        PlanRevisionRequest(
            target_duration_seconds=15,
            pace="fast",
            lock_segment_ids=("segment-002",),
        ),
    )

    assert revision.plan.sound_cues[0].timeline_range == TimeRange(
        start_seconds=7, end_seconds=8
    )


def _plan(tmp_path: Path) -> EditPlan:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    segments = tuple(
        EditSegment(
            segment_id=f"segment-{index + 1:03d}",
            role="hook" if index == 0 else "payoff" if index == 1 else "outro",
            source_path=source,
            source_range=TimeRange(start_seconds=index * 10, end_seconds=(index + 1) * 10),
            timeline_start_seconds=index * 10,
            speed=SpeedTreatment(rate=1, rationale="original treatment"),
            visual=VisualTreatment(rationale="original treatment"),
            score=1 - index * 0.1,
            evidence=(
                Evidence(
                    analyzer="fixture",
                    analyzer_version="1",
                    confidence=1,
                    description="fixture event",
                ),
            ),
        )
        for index in range(3)
    )
    return EditPlan(
        source_path=source,
        brief=CreativeBrief(
            title="Revision fixture",
            target_duration_seconds=30,
            captions=True,
            music=False,
        ),
        segments=segments,
        captions=(
            CaptionCue(
                timeline_range=TimeRange(start_seconds=1, end_seconds=2),
                source_range=TimeRange(start_seconds=1, end_seconds=2),
                segment_id="segment-001",
                text="Caption",
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )


def _metadata(tmp_path: Path) -> MediaMetadata:
    return MediaMetadata(
        path=tmp_path / "source.mp4",
        duration_seconds=30,
        width=1920,
        height=1080,
        frame_rate=30,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
