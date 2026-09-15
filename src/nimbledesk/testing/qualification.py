from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.client import DaemonClient
from nimbledesk.media.models import TimelineEvent
from nimbledesk.testing.smoke import _move_and_restore_pointer, validate_capture


class QualificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventTypeMetrics(QualificationModel):
    expected: Annotated[int, Field(ge=0)]
    detected: Annotated[int, Field(ge=0)]
    matched: Annotated[int, Field(ge=0)]
    precision: Annotated[float, Field(ge=0, le=1)]
    recall: Annotated[float, Field(ge=0, le=1)]
    f1: Annotated[float, Field(ge=0, le=1)]


class EventMatch(QualificationModel):
    event_type: str
    expected_seconds: Annotated[float, Field(ge=0)]
    detected_seconds: Annotated[float, Field(ge=0)]
    boundary_error_seconds: Annotated[float, Field(ge=0)]


class EventBenchmarkReport(QualificationModel):
    report_version: str = "1.0.0"
    tolerance_seconds: Annotated[float, Field(gt=0)]
    expected: Annotated[int, Field(ge=0)]
    detected: Annotated[int, Field(ge=0)]
    matched: Annotated[int, Field(ge=0)]
    precision: Annotated[float, Field(ge=0, le=1)]
    recall: Annotated[float, Field(ge=0, le=1)]
    f1: Annotated[float, Field(ge=0, le=1)]
    mean_boundary_error_seconds: Annotated[float, Field(ge=0)] | None
    p95_boundary_error_seconds: Annotated[float, Field(ge=0)] | None
    event_types: dict[str, EventTypeMetrics]
    matches: tuple[EventMatch, ...]
    missed: tuple[TimelineEvent, ...]
    false_positives: tuple[TimelineEvent, ...]


class EnduranceReport(QualificationModel):
    report_version: str = "1.0.0"
    started_at: float
    completed_at: float
    requested_seconds: Annotated[float, Field(gt=0)]
    elapsed_seconds: Annotated[float, Field(ge=0)]
    iterations: Annotated[int, Field(ge=0)]
    captures: Annotated[int, Field(ge=0)]
    input_round_trips: Annotated[int, Field(ge=0)]
    failures: tuple[str, ...]
    mean_observation_latency_ms: Annotated[float, Field(ge=0)] | None
    p95_observation_latency_ms: Annotated[float, Field(ge=0)] | None
    backend: str | None
    capabilities: tuple[str, ...]
    passed: bool


def benchmark_events(
    expected: tuple[TimelineEvent, ...],
    detected: tuple[TimelineEvent, ...],
    tolerance_seconds: float,
) -> EventBenchmarkReport:
    if tolerance_seconds <= 0:
        raise ValueError("event matching tolerance must be positive")
    unmatched_detected = set(range(len(detected)))
    matches: list[EventMatch] = []
    missed: list[TimelineEvent] = []
    for expected_event in sorted(expected, key=lambda event: event.time_seconds):
        candidates = [
            (abs(detected[index].time_seconds - expected_event.time_seconds), index)
            for index in unmatched_detected
            if detected[index].event_type == expected_event.event_type
            and abs(detected[index].time_seconds - expected_event.time_seconds)
            <= tolerance_seconds
        ]
        if not candidates:
            missed.append(expected_event)
            continue
        error, detected_index = min(candidates)
        unmatched_detected.remove(detected_index)
        matches.append(
            EventMatch(
                event_type=expected_event.event_type,
                expected_seconds=expected_event.time_seconds,
                detected_seconds=detected[detected_index].time_seconds,
                boundary_error_seconds=round(error, 6),
            )
        )
    false_positives = tuple(detected[index] for index in sorted(unmatched_detected))
    event_types = sorted({event.event_type for event in (*expected, *detected)})
    by_type = {
        event_type: _event_type_metrics(event_type, expected, detected, tuple(matches))
        for event_type in event_types
    }
    precision, recall, f1 = _rates(len(matches), len(expected), len(detected))
    errors = [match.boundary_error_seconds for match in matches]
    return EventBenchmarkReport(
        tolerance_seconds=tolerance_seconds,
        expected=len(expected),
        detected=len(detected),
        matched=len(matches),
        precision=precision,
        recall=recall,
        f1=f1,
        mean_boundary_error_seconds=round(statistics.fmean(errors), 6) if errors else None,
        p95_boundary_error_seconds=_percentile(errors, 0.95),
        event_types=by_type,
        matches=tuple(matches),
        missed=tuple(missed),
        false_positives=false_positives,
    )


def _event_type_metrics(
    event_type: str,
    expected: tuple[TimelineEvent, ...],
    detected: tuple[TimelineEvent, ...],
    matches: tuple[EventMatch, ...],
) -> EventTypeMetrics:
    expected_count = sum(event.event_type == event_type for event in expected)
    detected_count = sum(event.event_type == event_type for event in detected)
    matched_count = sum(match.event_type == event_type for match in matches)
    precision, recall, f1 = _rates(matched_count, expected_count, detected_count)
    return EventTypeMetrics(
        expected=expected_count,
        detected=detected_count,
        matched=matched_count,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _rates(matched: int, expected: int, detected: int) -> tuple[float, float, float]:
    precision = matched / detected if detected else float(expected == 0)
    recall = matched / expected if expected else float(detected == 0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return round(precision, 6), round(recall, 6), round(f1, 6)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 6)


async def run_endurance(
    connection_file: Path,
    duration_seconds: float,
    interval_seconds: float,
    capture_every: int,
    input_every: int | None,
) -> EnduranceReport:
    if duration_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("duration and interval must be positive")
    if capture_every < 1 or (input_every is not None and input_every < 1):
        raise ValueError("capture and input intervals must be at least one iteration")
    client = DaemonClient.from_file(connection_file)
    health = await client.call("health")
    session = await client.call(
        "session_start",
        {
            "reason": "NimbleDesk release endurance qualification",
            "config": {"input_enabled": input_every is not None, "max_actions": 100_000},
        },
    )
    session_id = str(session["session_id"])
    started_at = time.time()
    deadline = time.monotonic() + duration_seconds
    iterations = captures = input_round_trips = 0
    latencies: list[float] = []
    failures: list[str] = []
    try:
        while time.monotonic() < deadline:
            iteration_started = time.monotonic()
            try:
                observation = await client.call("desktop_observe", {"session_id": session_id})
                latencies.append((time.monotonic() - iteration_started) * 1_000)
                iterations += 1
                if iterations % capture_every == 0:
                    capture = await client.call(
                        "screen_capture",
                        {
                            "session_id": session_id,
                            "observation_id": observation["observation_id"],
                            "options": {
                                "image_format": "jpeg",
                                "max_width": 640,
                                "max_height": 400,
                                "jpeg_quality": 60,
                            },
                        },
                    )
                    validate_capture(capture)
                    captures += 1
                if input_every is not None and iterations % input_every == 0:
                    await _move_and_restore_pointer(client, session_id, observation)
                    input_round_trips += 1
            except Exception as error:
                failures.append(f"iteration {iterations + 1}: {type(error).__name__}: {error}")
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(interval_seconds, remaining))
    finally:
        try:
            await client.call("session_set_state", {"session_id": session_id, "state": "stopped"})
        except Exception as error:
            failures.append(f"session cleanup: {type(error).__name__}: {error}")
    completed_at = time.time()
    elapsed = completed_at - started_at
    return EnduranceReport(
        started_at=started_at,
        completed_at=completed_at,
        requested_seconds=duration_seconds,
        elapsed_seconds=round(elapsed, 3),
        iterations=iterations,
        captures=captures,
        input_round_trips=input_round_trips,
        failures=tuple(failures),
        mean_observation_latency_ms=(round(statistics.fmean(latencies), 3) if latencies else None),
        p95_observation_latency_ms=_percentile(latencies, 0.95),
        backend=str(health.get("backend")) if health.get("backend") else None,
        capabilities=tuple(str(value) for value in health.get("capabilities", [])),
        passed=not failures and elapsed >= duration_seconds * 0.99 and iterations > 0,
    )


def _load_events(path: Path) -> tuple[TimelineEvent, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list")
    return tuple(TimelineEvent.model_validate(event) for event in payload)


def _write_report(report: QualificationModel, output: Path | None) -> None:
    content = report.model_dump_json(indent=2)
    if output is None:
        print(content)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content + "\n", encoding="utf-8")
    print(output)


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NimbleDesk release qualification checks")
    commands = parser.add_subparsers(dest="command", required=True)
    events = commands.add_parser("events", help="measure semantic event precision and recall")
    events.add_argument("--expected", required=True, type=Path)
    events.add_argument("--detected", required=True, type=Path)
    events.add_argument("--tolerance-seconds", type=float, default=3)
    events.add_argument("--output", type=Path)
    endurance = commands.add_parser("endurance", help="exercise a running daemon over time")
    endurance.add_argument("--connection-file", required=True, type=Path)
    endurance.add_argument("--hours", type=float, default=8)
    endurance.add_argument("--interval-seconds", type=float, default=5)
    endurance.add_argument("--capture-every", type=int, default=12)
    endurance.add_argument("--input-every", type=int)
    endurance.add_argument("--output", type=Path)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    parsed = parse_args(arguments)
    report: QualificationModel
    if parsed.command == "events":
        report = benchmark_events(
            _load_events(parsed.expected),
            _load_events(parsed.detected),
            parsed.tolerance_seconds,
        )
    else:
        report = asyncio.run(
            run_endurance(
                parsed.connection_file,
                parsed.hours * 3_600,
                parsed.interval_seconds,
                parsed.capture_every,
                parsed.input_every,
            )
        )
    _write_report(report, parsed.output)


if __name__ == "__main__":
    main()
