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
        if abs(segment.timeline_start_seconds - timeline_cursor) > 0.02:
            issues.append(
                _issue(
                    "timeline_gap_or_overlap",
                    "Timeline segments must be contiguous and non-overlapping",
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
        timeline_cursor = segment.timeline_start_seconds + segment.timeline_duration_seconds
    if timeline_cursor > plan.brief.target_duration_seconds + 0.05:
        issues.append(_issue("target_duration", "Timeline exceeds the requested target duration"))
    for caption in plan.captions:
        if caption.timeline_range.end_seconds > timeline_cursor + 0.001:
            issues.append(_issue("caption_bounds", "Caption extends past the timeline"))
    if plan.music_cue:
        cue = plan.music_cue
        if not cue.asset.path.expanduser().is_file():
            issues.append(_issue("music_offline", "Selected music file is unavailable"))
        if not cue.asset.license.strip():
            issues.append(_issue("music_license", "Selected music has no license evidence"))
        if cue.source_range.end_seconds > cue.asset.duration_seconds + 0.001:
            issues.append(_issue("music_bounds", "Music cue extends past the selected asset"))
        if cue.timeline_range.end_seconds > timeline_cursor + 0.001:
            issues.append(_issue("music_timeline_bounds", "Music cue extends past the timeline"))
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
