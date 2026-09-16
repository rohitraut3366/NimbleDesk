from __future__ import annotations

from pathlib import Path
from typing import Any

from nimbledesk.creative.models import (
    CaptionCue,
    EditPlan,
    EditSegment,
    PlanChange,
    PlanRevisionRequest,
    PlanRevisionResult,
    SoundCue,
    SpeedTreatment,
    TimeRange,
)
from nimbledesk.creative.planner import plan_scene_music


def revise_edit_plan(plan: EditPlan, request: PlanRevisionRequest) -> PlanRevisionResult:
    segment_ids = {segment.segment_id for segment in plan.segments}
    requested_ids = set(request.lock_segment_ids) | set(request.unlock_segment_ids)
    unknown = requested_ids - segment_ids
    if unknown:
        raise ValueError("unknown segment IDs: " + ", ".join(sorted(unknown)))
    overlap = set(request.lock_segment_ids) & set(request.unlock_segment_ids)
    if overlap:
        raise ValueError("segments cannot be locked and unlocked together: " + ", ".join(overlap))

    brief_updates: dict[str, object] = {}
    if request.target_duration_seconds is not None:
        brief_updates["target_duration_seconds"] = request.target_duration_seconds
    if request.pace is not None:
        brief_updates["pace"] = request.pace
    if request.color_look is not None:
        brief_updates["color_look"] = request.color_look
    brief = plan.brief.model_copy(update=brief_updates)

    treated = tuple(_revise_treatment(segment, request) for segment in plan.segments)
    target = brief.target_duration_seconds
    if sum(segment.timeline_duration_seconds for segment in treated if segment.locked) > target:
        raise ValueError("locked segments exceed the revised target duration")
    selected = _fit_duration(treated, target)
    reflowed = _reflow(selected)
    captions = _remap_captions(plan.captions, reflowed)
    sound_cues = _remap_sound_cues(plan, reflowed)
    music_assets = tuple(dict.fromkeys(cue.asset for cue in plan.all_music_cues))
    music_cues = plan_scene_music(
        brief, music_assets, reflowed, _duration(reflowed)
    )
    revised = plan.model_copy(
        update={
            "brief": brief,
            "segments": reflowed,
            "captions": captions,
            "sound_cues": sound_cues,
            "music_cue": music_cues[0] if music_cues else None,
            "music_cues": music_cues,
        }
    )
    return PlanRevisionResult(plan=revised, changes=_diff(plan, revised))


def compare_edit_plans(before: EditPlan, after: EditPlan) -> tuple[PlanChange, ...]:
    """Return a deterministic field-level diff without modifying either plan."""
    return _diff(before, after)


def write_revision(result: PlanRevisionResult, plan_path: Path, diff_path: Path) -> None:
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(result.plan.model_dump_json(indent=2), encoding="utf-8")
    diff_path.write_text(
        PlanRevisionResult(plan=result.plan, changes=result.changes).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _revise_treatment(segment: EditSegment, request: PlanRevisionRequest) -> EditSegment:
    locked = segment.locked
    if segment.segment_id in request.lock_segment_ids:
        locked = True
    if segment.segment_id in request.unlock_segment_ids:
        locked = False
    if locked:
        return segment.model_copy(update={"locked": locked})
    speed = segment.speed
    if request.pace is not None:
        rate = speed.rate
        if request.pace.value == "fast" and rate >= 1:
            rate = 1.25
        elif request.pace.value in {"calm", "balanced"} and rate > 1:
            rate = 1
        speed = SpeedTreatment(
            rate=rate,
            interpolation=speed.interpolation,
            preserve_pitch=speed.preserve_pitch,
            rationale=f"revised for {request.pace.value} pace",
        )
    visual = segment.visual
    if request.color_look is not None:
        visual = visual.model_copy(update={"color_look": request.color_look})
    return segment.model_copy(update={"locked": locked, "speed": speed, "visual": visual})


def _fit_duration(segments: tuple[EditSegment, ...], target: float) -> tuple[EditSegment, ...]:
    selected: list[EditSegment] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        locked_after = sum(
            item.timeline_duration_seconds for item in segments[index + 1 :] if item.locked
        )
        overlap = (
            segment.visual.transition_duration_seconds
            if selected and segment.visual.transition_in == "cross_dissolve"
            else 0
        )
        available = target - cursor - locked_after
        timeline_contribution = segment.timeline_duration_seconds - overlap
        if segment.locked:
            selected.append(segment)
            cursor += timeline_contribution
            continue
        if available < 0.5:
            continue
        if timeline_contribution <= available:
            selected.append(segment)
            cursor += timeline_contribution
            continue
        source_duration = (available + overlap) * segment.speed.rate
        trimmed_range = segment.source_range.model_copy(
            update={"end_seconds": segment.source_range.start_seconds + source_duration}
        )
        trimmed = segment.model_copy(update={"source_range": trimmed_range})
        selected.append(trimmed)
        cursor += trimmed.timeline_duration_seconds - overlap
    return tuple(selected)


def _reflow(segments: tuple[EditSegment, ...]) -> tuple[EditSegment, ...]:
    result: list[EditSegment] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        if index == 0 and segment.visual.transition_in == "cross_dissolve":
            segment = segment.model_copy(
                update={
                    "visual": segment.visual.model_copy(update={"transition_in": "cut"})
                }
            )
        overlap = (
            segment.visual.transition_duration_seconds
            if segment.visual.transition_in == "cross_dissolve"
            else 0
        )
        updated = segment.model_copy(
            update={"timeline_start_seconds": round(cursor - overlap, 3)}
        )
        result.append(updated)
        cursor = updated.timeline_start_seconds + updated.timeline_duration_seconds
    return tuple(result)


def _remap_captions(
    captions: tuple[CaptionCue, ...], segments: tuple[EditSegment, ...]
) -> tuple[CaptionCue, ...]:
    by_id = {segment.segment_id: segment for segment in segments}
    remapped: list[CaptionCue] = []
    for caption in captions:
        if caption.segment_id is None or caption.source_range is None:
            continue
        segment = by_id.get(caption.segment_id)
        if segment is None:
            continue
        start = max(caption.source_range.start_seconds, segment.source_range.start_seconds)
        end = min(caption.source_range.end_seconds, segment.source_range.end_seconds)
        if end <= start:
            continue
        timeline_start = segment.timeline_start_seconds + (
            start - segment.source_range.start_seconds
        ) / segment.speed.rate
        timeline_end = segment.timeline_start_seconds + (
            end - segment.source_range.start_seconds
        ) / segment.speed.rate
        remapped.append(
            caption.model_copy(
                update={
                    "timeline_range": TimeRange(
                        start_seconds=round(timeline_start, 3),
                        end_seconds=round(timeline_end, 3),
                    )
                }
            )
        )
    return tuple(remapped)


def _duration(segments: tuple[EditSegment, ...]) -> float:
    if not segments:
        return 0
    last = segments[-1]
    return last.timeline_start_seconds + last.timeline_duration_seconds


def _remap_sound_cues(
    plan: EditPlan, segments: tuple[EditSegment, ...]
) -> tuple[SoundCue, ...]:
    old_segments = {segment.segment_id: segment for segment in plan.segments}
    new_segments = {segment.segment_id: segment for segment in segments}
    result: list[SoundCue] = []
    for cue in plan.sound_cues:
        old_segment = old_segments.get(cue.segment_id)
        new_segment = new_segments.get(cue.segment_id)
        if old_segment is None or new_segment is None:
            continue
        relative_start = cue.timeline_range.start_seconds - old_segment.timeline_start_seconds
        start = new_segment.timeline_start_seconds + min(
            max(0, relative_start), max(0, new_segment.timeline_duration_seconds - 0.05)
        )
        available = (
            new_segment.timeline_start_seconds + new_segment.timeline_duration_seconds - start
        )
        duration = min(cue.timeline_range.duration_seconds, available)
        if duration <= 0.04:
            continue
        result.append(
            cue.model_copy(
                update={
                    "source_range": TimeRange(
                        start_seconds=cue.source_range.start_seconds,
                        end_seconds=cue.source_range.start_seconds + duration,
                    ),
                    "timeline_range": TimeRange(
                        start_seconds=round(start, 3),
                        end_seconds=round(start + duration, 3),
                    ),
                }
            )
        )
    return tuple(result)


def _diff(before: EditPlan, after: EditPlan) -> tuple[PlanChange, ...]:
    changes: list[PlanChange] = []
    _walk_diff("", before.model_dump(mode="json"), after.model_dump(mode="json"), changes)
    return tuple(changes)


def _walk_diff(path: str, before: Any, after: Any, changes: list[PlanChange]) -> None:
    if type(before) is not type(after):
        changes.append(PlanChange(path=path or "/", before=before, after=after))
        return
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}/{key}"
            if key not in before or key not in after:
                changes.append(
                    PlanChange(path=child_path, before=before.get(key), after=after.get(key))
                )
            else:
                _walk_diff(child_path, before[key], after[key], changes)
        return
    if isinstance(before, list):
        if before != after:
            changes.append(PlanChange(path=path or "/", before=before, after=after))
        return
    if before != after:
        changes.append(PlanChange(path=path or "/", before=before, after=after))
