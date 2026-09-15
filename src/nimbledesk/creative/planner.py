from __future__ import annotations

from pathlib import Path
from typing import Literal

from nimbledesk.analysis.models import ContentIndex
from nimbledesk.creative.models import (
    AspectRatio,
    CaptionCue,
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    MusicAsset,
    MusicCue,
    ReviewItem,
    SpeedTreatment,
    TimeRange,
    TranscriptSegment,
    VisualTreatment,
)
from nimbledesk.creative.music import recommend_music
from nimbledesk.media.models import HighlightCandidate, HighlightManifest


def build_edit_plan(
    manifest: HighlightManifest,
    brief: CreativeBrief,
    transcripts: tuple[TranscriptSegment, ...] = (),
    music_assets: tuple[MusicAsset, ...] = (),
    content_index: ContentIndex | None = None,
) -> EditPlan:
    ordered = _story_order(manifest.candidates[: brief.clip_count])
    segments: list[EditSegment] = []
    timeline_cursor = 0.0
    for index, candidate in enumerate(ordered):
        remaining = brief.target_duration_seconds - timeline_cursor
        if remaining < 0.5:
            break
        rate, speed_reason = _speed_treatment(candidate, brief)
        maximum_source_duration = remaining * rate
        source_range = _trim_around_peak(candidate, maximum_source_duration)
        evidence = list(
            Evidence(
                analyzer="nimbledesk-highlight-ranker",
                analyzer_version="1.0.0",
                confidence=min(1, candidate.score),
                description=reason,
                source_range=source_range,
            )
            for reason in candidate.reasons
        )
        evidence.extend(_semantic_evidence(candidate, content_index))
        exposure, saturation, color_reason = _color_treatment(candidate, content_index)
        role = _role(index, len(ordered))
        segments.append(
            EditSegment(
                segment_id=f"segment-{index + 1:03d}",
                role=role,
                source_path=manifest.source.path,
                source_range=source_range,
                timeline_start_seconds=round(timeline_cursor, 3),
                speed=SpeedTreatment(
                    rate=rate,
                    interpolation="frame_blend" if rate < 1 else "nearest",
                    rationale=speed_reason,
                ),
                visual=VisualTreatment(
                    transition_in="dip_to_black" if role == "outro" else "cut",
                    punch_in_scale=1.08 if role in {"hook", "payoff"} else 1,
                    color_look=brief.color_look,
                    exposure_adjustment_stops=exposure,
                    saturation_multiplier=saturation,
                    title=brief.title if role == "hook" else None,
                    rationale=f"{_visual_reason(role)}; {color_reason}",
                ),
                score=candidate.score,
                evidence=tuple(evidence),
            )
        )
        timeline_cursor += source_range.duration_seconds / rate

    captions = _map_captions(tuple(segments), transcripts) if brief.captions else ()
    music_asset = recommend_music(brief, music_assets, timeline_cursor)
    music_cue = _music_cue(music_asset, timeline_cursor, brief) if music_asset else None
    review_items = _review_items(brief, transcripts, music_assets, tuple(segments))
    width, height = _delivery_size(brief.aspect_ratio)
    return EditPlan(
        source_path=manifest.source.path,
        brief=brief,
        segments=tuple(segments),
        music_cue=music_cue,
        captions=captions,
        delivery=DeliverySpec(
            width=width,
            height=height,
            frame_rate=min(manifest.source.frame_rate, 60),
        ),
        review_items=review_items,
    )


def write_edit_plan(plan: EditPlan, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def _story_order(candidates: tuple[HighlightCandidate, ...]) -> tuple[HighlightCandidate, ...]:
    if not candidates:
        return ()
    hook = max(candidates, key=lambda candidate: candidate.score)
    remainder = sorted(
        (candidate for candidate in candidates if candidate is not hook),
        key=lambda candidate: candidate.peak_seconds,
    )
    return (hook, *remainder)


def _role(
    index: int, total: int
) -> Literal["hook", "setup", "development", "payoff", "outro"]:
    if index == 0:
        return "hook"
    if index == total - 1:
        return "outro"
    if index == 1:
        return "setup"
    if index >= max(2, total - 2):
        return "payoff"
    return "development"


def _speed_treatment(candidate: HighlightCandidate, brief: CreativeBrief) -> tuple[float, str]:
    event_text = " ".join(candidate.event_labels).casefold()
    if any(term in event_text for term in ("clutch", "multi", "grenade", "reveal")):
        return 0.85, "slow the important action slightly to make the payoff readable"
    if brief.pace.value == "fast" and not candidate.event_labels:
        return 1.25, "compress generic activity to maintain a fast pace"
    return 1.0, "preserve natural timing for comprehension and source authenticity"


def _trim_around_peak(candidate: HighlightCandidate, maximum_duration: float) -> TimeRange:
    duration = candidate.end_seconds - candidate.start_seconds
    if duration <= maximum_duration:
        return TimeRange(start_seconds=candidate.start_seconds, end_seconds=candidate.end_seconds)
    before = min(candidate.peak_seconds - candidate.start_seconds, maximum_duration * 0.45)
    start = max(candidate.start_seconds, candidate.peak_seconds - before)
    end = min(candidate.end_seconds, start + maximum_duration)
    start = max(candidate.start_seconds, end - maximum_duration)
    return TimeRange(start_seconds=round(start, 3), end_seconds=round(end, 3))


def _visual_reason(role: str) -> str:
    if role == "hook":
        return "open on the strongest moment with a restrained emphasis punch-in"
    if role == "payoff":
        return "emphasize the narrative payoff without obscuring source action"
    if role == "outro":
        return "close the sequence cleanly"
    return "use a straight cut to preserve clarity and avoid unmotivated effects"


def _map_captions(
    segments: tuple[EditSegment, ...],
    transcripts: tuple[TranscriptSegment, ...],
) -> tuple[CaptionCue, ...]:
    cues: list[CaptionCue] = []
    for segment in segments:
        for transcript in transcripts:
            start = max(segment.source_range.start_seconds, transcript.source_range.start_seconds)
            end = min(segment.source_range.end_seconds, transcript.source_range.end_seconds)
            if end <= start or not transcript.text.strip():
                continue
            timeline_start = segment.timeline_start_seconds + (
                start - segment.source_range.start_seconds
            ) / segment.speed.rate
            timeline_end = segment.timeline_start_seconds + (
                end - segment.source_range.start_seconds
            ) / segment.speed.rate
            cues.append(
                CaptionCue(
                    timeline_range=TimeRange(
                        start_seconds=round(timeline_start, 3),
                        end_seconds=round(timeline_end, 3),
                    ),
                    text=transcript.text.strip(),
                    speaker=transcript.speaker,
                )
            )
    return tuple(cues)


def _music_cue(asset: MusicAsset, duration: float, brief: CreativeBrief) -> MusicCue:
    cue_duration = min(duration, asset.duration_seconds)
    return MusicCue(
        asset=asset,
        source_range=TimeRange(start_seconds=0, end_seconds=cue_duration),
        timeline_range=TimeRange(start_seconds=0, end_seconds=cue_duration),
        gain_db=-22 if brief.captions else -16,
        rationale=f"best licensed catalog match for {brief.mood} mood and {brief.pace.value} pace",
    )


def _review_items(
    brief: CreativeBrief,
    transcripts: tuple[TranscriptSegment, ...],
    music_assets: tuple[MusicAsset, ...],
    segments: tuple[EditSegment, ...],
) -> tuple[ReviewItem, ...]:
    items: list[ReviewItem] = []
    if not segments:
        items.append(ReviewItem(severity="blocking", message="No usable highlight segments found"))
    if brief.captions and not transcripts:
        items.append(
            ReviewItem(
                severity="warning",
                message="Captions were requested but no transcript was available",
            )
        )
    if brief.music and not music_assets:
        items.append(
            ReviewItem(
                severity="warning",
                message="Music was requested but no licensed music catalog was supplied",
            )
        )
    return tuple(items)


def _delivery_size(aspect_ratio: AspectRatio) -> tuple[int, int]:
    if aspect_ratio is AspectRatio.VERTICAL:
        return 1080, 1920
    if aspect_ratio is AspectRatio.SQUARE:
        return 1080, 1080
    return 1920, 1080


def _semantic_evidence(
    candidate: HighlightCandidate, content_index: ContentIndex | None
) -> tuple[Evidence, ...]:
    if content_index is None:
        return ()
    semantic = content_index.track("semantic")
    evidence: list[Evidence] = []
    for point in semantic.points:
        start = point.source_range.start.seconds
        if not candidate.start_seconds <= start <= candidate.end_seconds:
            continue
        description = ", ".join(point.labels)
        if point.text:
            description += f": {point.text}"
        evidence.append(
            Evidence(
                analyzer=semantic.provenance.analyzer,
                analyzer_version=semantic.provenance.analyzer_version,
                confidence=point.confidence,
                description=description,
                source_range=TimeRange(
                    start_seconds=start,
                    end_seconds=start + point.source_range.duration.seconds,
                ),
            )
        )
    return tuple(evidence)


def _color_treatment(
    candidate: HighlightCandidate, content_index: ContentIndex | None
) -> tuple[float, float, str]:
    if content_index is None:
        return 0, 1, "retain a conservative source-neutral correction"
    color = content_index.track("color")
    points = [
        point
        for point in color.points
        if candidate.start_seconds <= point.source_range.start.seconds <= candidate.end_seconds
    ]
    if not points:
        return 0, 1, "no color samples overlap this segment"
    luminance = sum(point.metrics["luminance"] for point in points) / len(points)
    source_saturation = sum(point.metrics["saturation"] for point in points) / len(points)
    exposure = round(min(0.5, (0.38 - luminance) * 1.5), 3) if luminance < 0.38 else 0
    if luminance > 0.72:
        exposure = round(max(-0.35, 0.62 - luminance), 3)
    saturation = 1.08 if source_saturation < 0.22 else 0.95 if source_saturation > 0.65 else 1
    reason = (
        f"technical correction from sampled luminance {luminance:.2f} and "
        f"saturation {source_saturation:.2f}"
    )
    return exposure, saturation, reason
