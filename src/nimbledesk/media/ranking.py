from __future__ import annotations

import math

import numpy as np

from nimbledesk.media.models import (
    AnalysisConfig,
    HighlightCandidate,
    SignalPoint,
    TimelineEvent,
)
from nimbledesk.media.signals import FloatArray, normalize_signal


def build_signal_points(
    duration_seconds: float,
    motion: FloatArray,
    motion_frames_per_second: float,
    audio: FloatArray,
    audio_window_seconds: float,
    events: tuple[TimelineEvent, ...] = (),
) -> tuple[SignalPoint, ...]:
    step_seconds = audio_window_seconds
    point_count = max(1, math.ceil(duration_seconds / step_seconds))
    normalized_motion = normalize_signal(motion)
    normalized_audio = normalize_signal(audio)
    event_scores = np.zeros(point_count, dtype=np.float64)
    for event in events:
        index = min(point_count - 1, int(event.time_seconds / step_seconds))
        event_scores[index] = max(event_scores[index], event.importance)
    points: list[SignalPoint] = []
    for index in range(point_count):
        time_seconds = index * step_seconds
        motion_index = min(
            normalized_motion.size - 1,
            max(0, int(time_seconds * motion_frames_per_second)),
        )
        motion_value = float(normalized_motion[motion_index]) if normalized_motion.size else 0.0
        audio_value = float(normalized_audio[index]) if index < normalized_audio.size else 0.0
        event_value = float(event_scores[index])
        combined = 0.35 * motion_value + 0.35 * audio_value + 0.8 * event_value
        points.append(
            SignalPoint(
                time_seconds=time_seconds,
                motion=motion_value,
                audio=audio_value,
                event=event_value,
                combined=combined,
            )
        )
    return tuple(points)


def rank_highlights(
    points: tuple[SignalPoint, ...],
    duration_seconds: float,
    config: AnalysisConfig,
    events: tuple[TimelineEvent, ...] = (),
) -> tuple[HighlightCandidate, ...]:
    ordered = sorted(points, key=lambda point: point.combined, reverse=True)
    selected: list[SignalPoint] = []
    for point in ordered:
        if point.combined <= 0:
            continue
        if any(
            abs(point.time_seconds - existing.time_seconds)
            < config.minimum_peak_separation_seconds
            for existing in selected
        ):
            continue
        selected.append(point)
        if len(selected) >= config.highlight_count:
            break
    selected.sort(key=lambda point: point.combined, reverse=True)
    candidates: list[HighlightCandidate] = []
    for rank, point in enumerate(selected, start=1):
        nearby_events = tuple(
            event
            for event in events
            if abs(event.time_seconds - point.time_seconds)
            <= max(config.lead_in_seconds, config.aftermath_seconds)
        )
        reasons = _reasons(point, nearby_events)
        candidates.append(
            HighlightCandidate(
                rank=rank,
                start_seconds=max(0, point.time_seconds - config.lead_in_seconds),
                end_seconds=min(duration_seconds, point.time_seconds + config.aftermath_seconds),
                peak_seconds=point.time_seconds,
                score=round(point.combined, 4),
                reasons=reasons,
                event_labels=tuple(event.label or event.event_type for event in nearby_events),
            )
        )
    return tuple(candidates)


def _reasons(
    point: SignalPoint,
    events: tuple[TimelineEvent, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if events:
        event_names = ", ".join(event.label or event.event_type for event in events)
        reasons.append("timeline event: " + event_names)
    if point.motion >= 0.7:
        reasons.append("high visual motion")
    if point.audio >= 0.7:
        reasons.append("high audio energy")
    if not reasons:
        reasons.append("combined audiovisual activity")
    return tuple(reasons)
