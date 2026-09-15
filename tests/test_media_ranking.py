import numpy as np

from nimbledesk.media.models import AnalysisConfig, TimelineEvent
from nimbledesk.media.ranking import build_signal_points, rank_highlights
from nimbledesk.media.signals import normalize_signal


def test_normalize_signal_is_robust_to_flat_input() -> None:
    normalized = normalize_signal(np.asarray([2.0, 2.0, 2.0], dtype=np.float64))

    assert normalized.tolist() == [0.0, 0.0, 0.0]


def test_timeline_event_promotes_and_labels_highlight() -> None:
    motion = np.zeros(40, dtype=np.float64)
    audio = np.zeros(20, dtype=np.float64)
    events = (
        TimelineEvent(
            time_seconds=5,
            event_type="grenade_kill",
            label="Triple grenade kill",
            importance=1,
        ),
    )
    points = build_signal_points(10, motion, 2, audio, 0.5, events)

    candidates = rank_highlights(
        points,
        duration_seconds=10,
        config=AnalysisConfig(
            highlight_count=1,
            lead_in_seconds=2,
            aftermath_seconds=3,
            minimum_peak_separation_seconds=1,
        ),
        events=events,
    )

    assert len(candidates) == 1
    assert candidates[0].peak_seconds == 5
    assert candidates[0].start_seconds == 3
    assert candidates[0].end_seconds == 8
    assert candidates[0].event_labels == ("Triple grenade kill",)


def test_ranking_separates_nearby_peaks() -> None:
    motion = np.asarray([0, 0, 10, 9, 0, 0, 0, 0, 8, 0], dtype=np.float64)
    audio = np.asarray([0, 0, 10, 8, 0, 0, 0, 0, 9, 0], dtype=np.float64)
    points = build_signal_points(10, motion, 1, audio, 1)

    candidates = rank_highlights(
        points,
        10,
        AnalysisConfig(
            highlight_count=3,
            lead_in_seconds=1,
            aftermath_seconds=1,
            minimum_peak_separation_seconds=3,
        ),
    )

    assert [candidate.peak_seconds for candidate in candidates] == [2, 8]
