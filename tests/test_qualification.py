from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import nimbledesk.testing.qualification as qualification
from nimbledesk.media.models import HighlightCandidate, TimelineEvent


def test_event_benchmark_measures_per_type_accuracy_and_boundary_error() -> None:
    expected = (
        TimelineEvent(time_seconds=10, event_type="kill"),
        TimelineEvent(time_seconds=30, event_type="clutch"),
        TimelineEvent(time_seconds=50, event_type="kill"),
    )
    detected = (
        TimelineEvent(time_seconds=11, event_type="kill"),
        TimelineEvent(time_seconds=32, event_type="clutch"),
        TimelineEvent(time_seconds=80, event_type="kill"),
    )

    report = qualification.benchmark_events(
        expected,
        detected,
        tolerance_seconds=3,
        corpus_id="ranked-fps-session-01",
        source_duration_seconds=7_200,
    )

    assert report.matched == 2
    assert report.precision == 0.666667
    assert report.recall == 0.666667
    assert report.mean_boundary_error_seconds == 1.5
    assert report.p95_boundary_error_seconds == 2
    assert report.event_types["clutch"].f1 == 1
    assert report.missed[0].time_seconds == 50
    assert report.false_positives[0].time_seconds == 80
    assert report.corpus_id == "ranked-fps-session-01"
    assert report.source_duration_seconds == 7_200


def test_event_benchmark_handles_empty_corpus() -> None:
    report = qualification.benchmark_events((), (), tolerance_seconds=3)

    assert report.precision == 1
    assert report.recall == 1
    assert report.f1 == 1
    assert report.p95_boundary_error_seconds is None


def test_ranking_benchmark_measures_order_diversity_and_context() -> None:
    expected = (
        qualification.HighlightAnnotation(
            moment_id="clutch-1",
            peak_seconds=30,
            event_type="clutch",
            relevance=5,
            context_start_seconds=24,
            context_end_seconds=35,
        ),
        qualification.HighlightAnnotation(
            moment_id="kill-1", peak_seconds=60, event_type="kill", relevance=3
        ),
        qualification.HighlightAnnotation(
            moment_id="survival-1",
            peak_seconds=90,
            event_type="narrow_survival",
            relevance=4,
        ),
    )
    detected = (
        HighlightCandidate(
            rank=1,
            start_seconds=25,
            end_seconds=34,
            peak_seconds=31,
            score=1,
            reasons=("semantic event",),
        ),
        HighlightCandidate(
            rank=2,
            start_seconds=84,
            end_seconds=100,
            peak_seconds=91,
            score=0.9,
            reasons=("semantic event",),
        ),
        HighlightCandidate(
            rank=3,
            start_seconds=110,
            end_seconds=120,
            peak_seconds=115,
            score=0.8,
            reasons=("motion",),
        ),
    )

    report = qualification.benchmark_ranking(
        expected,
        detected,
        cutoff=3,
        tolerance_seconds=3,
        corpus_id="ranked-fps-session-01",
        source_duration_seconds=7_200,
    )

    assert report.precision_at_k == 0.666667
    assert report.recall_at_k == 0.666667
    assert report.mean_average_precision == 0.666667
    assert report.normalized_discounted_cumulative_gain > 0.8
    assert report.event_type_coverage == 0.666667
    assert report.context_retention == 0
    assert report.missed_moment_ids == ("kill-1",)
    assert report.corpus_id == "ranked-fps-session-01"


@pytest.mark.asyncio
async def test_endurance_runner_records_observations_and_captures(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    image = b"qualification-image"

    class FakeClient:
        @classmethod
        def from_file(cls, _path: Path) -> FakeClient:
            return cls()

        async def call(
            self, method: str, _parameters: dict[str, object] | None = None
        ) -> dict[str, object]:
            if method == "health":
                return {
                    "status": "ok",
                    "backend": "simulator",
                    "capabilities": ["screen_capture"],
                }
            if method == "session_start":
                return {"session_id": "session-1"}
            if method == "desktop_observe":
                return {"observation_id": "observation-1"}
            if method == "screen_capture":
                return {
                    "data_base64": base64.b64encode(image).decode(),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "width": 10,
                    "height": 10,
                }
            if method == "session_set_state":
                return {"state": "stopped"}
            raise AssertionError(method)

    monkeypatch.setattr(qualification, "DaemonClient", FakeClient)

    report = await qualification.run_endurance(
        tmp_path / "connection.json",
        duration_seconds=0.01,
        interval_seconds=0.001,
        capture_every=1,
        input_every=None,
    )

    assert report.passed
    assert report.iterations > 0
    assert report.captures == report.iterations
    assert report.backend == "simulator"
