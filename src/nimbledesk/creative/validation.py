from __future__ import annotations

from pathlib import Path

from nimbledesk.creative.models import (
    EditPlan,
    PlanValidationReport,
    ValidationIssue,
)
from nimbledesk.media.models import MediaMetadata


class PlanValidationError(RuntimeError):
    def __init__(self, report: PlanValidationReport) -> None:
        self.report = report
        messages = "; ".join(
            issue.message for issue in report.issues if issue.severity == "blocking"
        )
        super().__init__(messages or "creative plan validation failed")


def validate_edit_plan(plan: EditPlan, source: MediaMetadata) -> PlanValidationReport:
    issues: list[ValidationIssue] = []
    timeline_cursor = 0.0
    if not plan.segments:
        issues.append(_issue("no_segments", "The edit plan contains no segments"))
    for segment in plan.segments:
        if segment.source_path.resolve() != source.path.resolve():
            issues.append(
                _issue(
                    "unknown_source",
                    "Segment refers to a source outside the indexed asset",
                    segment.segment_id,
                )
            )
        if segment.source_range.end_seconds > source.duration_seconds + 0.001:
            issues.append(
                _issue(
                    "source_bounds",
                    "Segment ends after the source media",
                    segment.segment_id,
                )
            )
        transition_overlap = (
            segment.visual.transition_duration_seconds
            if segment.visual.transition_in == "cross_dissolve"
            else 0
        )
        expected_start = max(0, timeline_cursor - transition_overlap)
        if abs(segment.timeline_start_seconds - expected_start) > 0.02:
            issues.append(
                _issue(
                    "timeline_gap_or_overlap",
                    "Timeline segment placement must match its incoming transition",
                    segment.segment_id,
                )
            )
        if not segment.evidence:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="missing_evidence",
                    message="Segment has no recorded selection evidence",
                    segment_id=segment.segment_id,
                )
            )
        if transition_overlap and segment is plan.segments[0]:
            issues.append(
                _issue(
                    "invalid_transition",
                    "The first segment cannot have an incoming cross-dissolve",
                    segment.segment_id,
                )
            )
        if transition_overlap >= segment.timeline_duration_seconds:
            issues.append(
                _issue(
                    "invalid_transition",
                    "Cross-dissolve duration must be shorter than the incoming segment",
                    segment.segment_id,
                )
            )
        timeline_cursor = segment.timeline_start_seconds + segment.timeline_duration_seconds
    if timeline_cursor > plan.brief.target_duration_seconds + 0.05:
        issues.append(_issue("target_duration", "Timeline exceeds the requested target duration"))
    for caption in plan.captions:
        if caption.timeline_range.end_seconds > timeline_cursor + 0.001:
            issues.append(_issue("caption_bounds", "Caption extends past the timeline"))
    if plan.music_cue:
        music_cue = plan.music_cue
        if not music_cue.asset.path.expanduser().is_file():
            issues.append(_issue("music_offline", "Selected music file is unavailable"))
        if not music_cue.asset.license.strip():
            issues.append(_issue("music_license", "Selected music has no license evidence"))
        if music_cue.source_range.end_seconds > music_cue.asset.duration_seconds + 0.001:
            issues.append(_issue("music_bounds", "Music cue extends past the selected asset"))
        if music_cue.timeline_range.end_seconds > timeline_cursor + 0.001:
            issues.append(_issue("music_timeline_bounds", "Music cue extends past the timeline"))
    segment_ids = {segment.segment_id for segment in plan.segments}
    for sound_cue in plan.sound_cues:
        if sound_cue.segment_id not in segment_ids:
            issues.append(_issue("sound_segment", "Sound cue refers to a removed segment"))
        if not sound_cue.asset.path.expanduser().is_file():
            issues.append(_issue("sound_offline", "Selected sound file is unavailable"))
        if not sound_cue.asset.license.strip():
            issues.append(_issue("sound_license", "Selected sound has no license evidence"))
        if sound_cue.source_range.end_seconds > sound_cue.asset.duration_seconds + 0.001:
            issues.append(_issue("sound_bounds", "Sound cue exceeds its source asset"))
        if sound_cue.timeline_range.end_seconds > timeline_cursor + 0.001:
            issues.append(_issue("sound_timeline_bounds", "Sound cue extends past the timeline"))
    for review in plan.review_items:
        if review.severity == "blocking":
            issues.append(_issue("blocking_review", review.message, review.segment_id))
    return PlanValidationReport(
        valid=not any(issue.severity == "blocking" for issue in issues),
        issues=tuple(issues),
        measured_duration_seconds=round(timeline_cursor, 3),
    )


def require_valid_plan(plan: EditPlan, source: MediaMetadata) -> PlanValidationReport:
    report = validate_edit_plan(plan, source)
    if not report.valid:
        raise PlanValidationError(report)
    return report


def write_validation_report(report: PlanValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _issue(code: str, message: str, segment_id: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        severity="blocking",
        code=code,
        message=message,
        segment_id=segment_id,
    )
