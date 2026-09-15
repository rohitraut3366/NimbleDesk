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
    selected_event_types: set[str] = set()
    while len(selected) < config.highlight_count:
        eligible = [
            point
            for point in ordered
            if point not in selected
            and point.combined > 0
            and all(
                abs(point.time_seconds - existing.time_seconds)
                >= config.minimum_peak_separation_seconds
                for existing in selected
            )
        ]
        if not eligible:
            break
        point = max(
            eligible,
            key=lambda candidate: candidate.combined
            + _diversity_bonus(candidate, events, selected_event_types, config),
        )
        selected.append(point)
        selected_event_types.update(
            event.event_type for event in _nearby_events(point, events, config)
        )
    candidates: list[HighlightCandidate] = []
    explained_event_types: set[str] = set()
    for rank, point in enumerate(selected, start=1):
        nearby_events = _nearby_events(point, events, config)
        event_types = {event.event_type for event in nearby_events}
        new_event_types = event_types - explained_event_types
        reasons = list(_reasons(point, nearby_events))
        if rank > 1 and new_event_types:
            reasons.append("adds event diversity: " + ", ".join(sorted(new_event_types)))
        explained_event_types.update(event_types)
        candidates.append(
            HighlightCandidate(
                rank=rank,
                start_seconds=max(0, point.time_seconds - config.lead_in_seconds),
                end_seconds=min(duration_seconds, point.time_seconds + config.aftermath_seconds),
                peak_seconds=point.time_seconds,
                score=round(point.combined, 4),
                reasons=tuple(reasons),
                event_labels=tuple(event.label or event.event_type for event in nearby_events),
            )
        )
    return tuple(candidates)


def _nearby_events(
    point: SignalPoint,
    events: tuple[TimelineEvent, ...],
    config: AnalysisConfig,
) -> tuple[TimelineEvent, ...]:
    context_seconds = max(config.lead_in_seconds, config.aftermath_seconds)
    return tuple(
        event for event in events if abs(event.time_seconds - point.time_seconds) <= context_seconds
    )


def _diversity_bonus(
    point: SignalPoint,
    events: tuple[TimelineEvent, ...],
    selected_event_types: set[str],
    config: AnalysisConfig,
) -> float:
    unseen = [
        event
        for event in _nearby_events(point, events, config)
        if event.event_type not in selected_event_types
    ]
    return 0.2 * max((event.importance for event in unseen), default=0)


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
