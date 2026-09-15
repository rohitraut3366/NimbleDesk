from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy as np

from nimbledesk.analysis.models import (
    AnalysisTrack,
    AssetRecord,
    ContentIndex,
    Provenance,
    RationalRange,
    TrackPoint,
)
from nimbledesk.creative.models import TranscriptSegment
from nimbledesk.media.ffmpeg import MediaToolError, probe_media
from nimbledesk.media.models import TimelineEvent
from nimbledesk.media.process import (
    CancellationCheck,
    check_cancelled,
    check_process_cancelled,
)
from nimbledesk.media.signals import extract_audio_signal, extract_motion_signal, normalize_signal

ANALYZER_VERSION = "1.1.0"


class ContentIndexer:
    def build(
        self,
        source: Path,
        cache_directory: Path,
        *,
        transcripts: tuple[TranscriptSegment, ...] = (),
        events: tuple[TimelineEvent, ...] = (),
        progress: Callable[[str, float], None] | None = None,
        cancelled: CancellationCheck | None = None,
    ) -> ContentIndex:
        report = progress or (lambda _stage, _value: None)
        cache_directory.mkdir(parents=True, exist_ok=True)
        asset = _asset_record(source, cache_directory / "asset.json")
        sample_rate = _sample_rate(asset.metadata.duration_seconds)
        configuration = {"sample_rate": sample_rate, "version": ANALYZER_VERSION}
        base_hash = _configuration_hash(asset.sha256, configuration)
        tracks: list[AnalysisTrack] = []
        cache_hits: list[str] = []

        report("analyzing motion", 0.05)
        motion, hit = _cached_track(
            cache_directory,
            "motion",
            base_hash,
            lambda: _motion_track(asset, sample_rate, base_hash, cancelled),
        )
        tracks.append(motion)
        if hit:
            cache_hits.append("motion")

        report("analyzing audio", 0.25)
        audio, hit = _cached_track(
            cache_directory,
            "audio",
            base_hash,
            lambda: _audio_track(asset, base_hash, cancelled),
        )
        tracks.append(audio)
        if hit:
            cache_hits.append("audio")

        report("analyzing color and shots", 0.45)
        color, hit = _cached_track(
            cache_directory,
            "color",
            base_hash,
            lambda: _color_track(asset, min(0.5, sample_rate), base_hash, cancelled),
        )
        tracks.append(color)
        if hit:
            cache_hits.append("color")
        shot_hash = _configuration_hash(base_hash, {"color": _track_digest(color)})
        shots, hit = _cached_track(
            cache_directory,
            "shots",
            shot_hash,
            lambda: _shot_track(color, shot_hash),
        )
        tracks.append(shots)
        if hit:
            cache_hits.append("shots")

        semantic_inputs = {
            "transcripts": [segment.model_dump(mode="json") for segment in transcripts],
            "events": [event.model_dump(mode="json") for event in events],
            "motion": _track_digest(motion),
            "audio": _track_digest(audio),
        }
        semantic_hash = _configuration_hash(base_hash, semantic_inputs)
        check_cancelled(cancelled)
        report("building semantic index", 0.7)
        semantic, hit = _cached_track(
            cache_directory,
            "semantic",
            semantic_hash,
            lambda: _semantic_track(transcripts, events, motion, audio, semantic_hash),
        )
        tracks.append(semantic)
        if hit:
            cache_hits.append("semantic")
        semantic_events = _events_from_track(semantic, events)
        index = ContentIndex(
            asset=asset,
            tracks=tuple(tracks),
            semantic_events=semantic_events,
            cache_hits=tuple(cache_hits),
            analyzer_metadata={
                "sample_frames_per_second": sample_rate,
                "hierarchical_profile": _profile(asset.metadata.duration_seconds),
            },
        )
        _atomic_write(cache_directory / "content_index.json", index.model_dump_json(indent=2))
        report("content index complete", 1)
        return index


def _asset_record(source: Path, cache_path: Path) -> AssetRecord:
    resolved = source.expanduser().resolve()
    stat = resolved.stat()
    if cache_path.is_file():
        cached = AssetRecord.model_validate_json(cache_path.read_text(encoding="utf-8"))
        if (
            cached.path == resolved
            and cached.size_bytes == stat.st_size
            and cached.modified_ns == stat.st_mtime_ns
        ):
            return cached
    digest = hashlib.sha256()
    with resolved.open("rb") as source_file:
        while chunk := source_file.read(4 * 1024 * 1024):
            digest.update(chunk)
    asset = AssetRecord(
        asset_id=digest.hexdigest()[:24],
        path=resolved,
        sha256=digest.hexdigest(),
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        metadata=probe_media(resolved),
    )
    _atomic_write(cache_path, asset.model_dump_json(indent=2))
    return asset


def _motion_track(
    asset: AssetRecord,
    sample_rate: float,
    configuration_hash: str,
    cancelled: CancellationCheck | None,
) -> AnalysisTrack:
    raw = extract_motion_signal(asset.path, sample_rate, cancelled=cancelled)
    normalized = normalize_signal(raw)
    step = 1 / sample_rate
    points = tuple(
        TrackPoint(
            source_range=RationalRange.from_seconds(index * step, step),
            confidence=0.9,
            metrics={"motion": round(float(value), 5)},
            evidence=("mean absolute luminance-frame difference",),
        )
        for index, value in enumerate(normalized)
    )
    return _track("motion", "motion", configuration_hash, points)


def _audio_track(
    asset: AssetRecord, configuration_hash: str, cancelled: CancellationCheck | None
) -> AnalysisTrack:
    if not asset.metadata.has_audio:
        return _track("audio", "audio", configuration_hash, ())
    window = 0.5
    raw = extract_audio_signal(asset.path, window, cancelled=cancelled)
    normalized = normalize_signal(raw)
    silence_limit = max(0.003, float(np.percentile(raw, 15)) * 1.25) if raw.size else 0.003
    points = tuple(
        TrackPoint(
            source_range=RationalRange.from_seconds(index * window, window),
            confidence=0.95,
            metrics={
                "rms": round(float(raw[index]), 6),
                "energy": round(float(normalized[index]), 5),
                "silence": float(raw[index] <= silence_limit),
            },
            labels=("silence",) if raw[index] <= silence_limit else (),
            evidence=("decoded mono PCM RMS",),
        )
        for index in range(raw.size)
    )
    return _track("audio", "audio", configuration_hash, points)


def _color_track(
    asset: AssetRecord,
    sample_rate: float,
    configuration_hash: str,
    cancelled: CancellationCheck | None,
) -> AnalysisTrack:
    width, height = 64, 36
    frame_size = width * height * 3
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(asset.path),
        "-an",
        "-vf",
        f"fps={sample_rate},scale={width}:{height},format=rgb24",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise MediaToolError("ffmpeg did not create a color-analysis stream")
    points: list[TrackPoint] = []
    step = 1 / sample_rate
    index = 0
    previous_luminance: np.ndarray | None = None
    while frame_bytes := process.stdout.read(frame_size):
        check_process_cancelled(process, cancelled)
        if len(frame_bytes) != frame_size:
            break
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, 3)) / 255
        maximum = np.max(frame, axis=2)
        minimum = np.min(frame, axis=2)
        saturation = np.divide(
            maximum - minimum,
            maximum,
            out=np.zeros_like(maximum),
            where=maximum > 0,
        )
        luminance = 0.2126 * frame[:, :, 0] + 0.7152 * frame[:, :, 1] + 0.0722 * frame[:, :, 2]
        center_x, center_y, spatial_motion = _spatial_motion(luminance, previous_luminance)
        points.append(
            TrackPoint(
                source_range=RationalRange.from_seconds(index * step, step),
                confidence=0.9,
                metrics={
                    "red": round(float(np.mean(frame[:, :, 0])), 5),
                    "green": round(float(np.mean(frame[:, :, 1])), 5),
                    "blue": round(float(np.mean(frame[:, :, 2])), 5),
                    "luminance": round(float(np.mean(luminance)), 5),
                    "saturation": round(float(np.mean(saturation)), 5),
                    "shadow_clip": round(float(np.mean(luminance < 0.02)), 5),
                    "highlight_clip": round(float(np.mean(luminance > 0.98)), 5),
                    "motion_center_x": round(center_x, 5),
                    "motion_center_y": round(center_y, 5),
                    "spatial_motion": round(spatial_motion, 5),
                },
                evidence=("decoded RGB frame statistics",),
            )
        )
        index += 1
        previous_luminance = luminance
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise MediaToolError(stderr.decode(errors="replace").strip() or "color analysis failed")
    return _track("color", "color", configuration_hash, tuple(points))


def _spatial_motion(
    luminance: np.ndarray, previous: np.ndarray | None
) -> tuple[float, float, float]:
    if previous is None:
        return 0.5, 0.5, 0.0
    difference = np.abs(luminance - previous)
    total = float(np.sum(difference))
    if total <= 0.001:
        return 0.5, 0.5, 0.0
    height, width = difference.shape
    x_coordinates = (np.arange(width, dtype=np.float64) + 0.5) / width
    y_coordinates = (np.arange(height, dtype=np.float64) + 0.5) / height
    center_x = float(np.sum(difference * x_coordinates[None, :]) / total)
    center_y = float(np.sum(difference * y_coordinates[:, None]) / total)
    return center_x, center_y, min(1.0, float(np.mean(difference)) * 8)


def _shot_track(color: AnalysisTrack, configuration_hash: str) -> AnalysisTrack:
    points: list[TrackPoint] = []
    previous: tuple[float, float, float] | None = None
    for point in color.points:
        current = (
            point.metrics["red"],
            point.metrics["green"],
            point.metrics["blue"],
        )
        difference = (
            0.0
            if previous is None
            else sum(abs(a - b) for a, b in zip(current, previous, strict=True)) / 3
        )
        if previous is None or difference >= 0.12:
            points.append(
                TrackPoint(
                    source_range=point.source_range,
                    confidence=min(1, 0.6 + difference),
                    metrics={"color_difference": round(difference, 5)},
                    labels=("shot_start",),
                    evidence=("inter-sample RGB discontinuity",),
                )
            )
        previous = current
    return _track("shots", "shot", configuration_hash, tuple(points))


def _semantic_track(
    transcripts: tuple[TranscriptSegment, ...],
    events: tuple[TimelineEvent, ...],
    motion: AnalysisTrack,
    audio: AnalysisTrack,
    configuration_hash: str,
) -> AnalysisTrack:
    points: list[TrackPoint] = []
    for event in events:
        points.append(
            TrackPoint(
                source_range=RationalRange.from_seconds(event.time_seconds, 1),
                confidence=event.importance,
                labels=(event.event_type,),
                text=event.label,
                evidence=("supplied or domain-pack event",),
            )
        )
    for segment in transcripts:
        text = segment.text.strip()
        labels = _text_labels(text)
        if labels:
            points.append(
                TrackPoint(
                    source_range=RationalRange.from_seconds(
                        segment.source_range.start_seconds,
                        segment.source_range.duration_seconds,
                    ),
                    confidence=segment.confidence,
                    labels=labels,
                    text=text,
                    evidence=("time-aligned transcript language cues",),
                )
            )
    audio_by_time = {round(point.source_range.start.seconds, 1): point for point in audio.points}
    last_peak = -60.0
    for point in motion.points:
        timestamp = point.source_range.start.seconds
        matching_audio = audio_by_time.get(round(timestamp, 1))
        audio_energy = matching_audio.metrics.get("energy", 0) if matching_audio else 0
        is_payoff = (
            point.metrics.get("motion", 0) >= 0.85
            and audio_energy >= 0.8
            and timestamp - last_peak >= 8
        )
        if is_payoff:
            points.append(
                TrackPoint(
                    source_range=RationalRange.from_seconds(timestamp, 1),
                    confidence=0.7,
                    metrics={"motion": point.metrics["motion"], "audio_energy": audio_energy},
                    labels=("audiovisual_payoff",),
                    evidence=("coincident high motion and audio energy",),
                )
            )
            last_peak = timestamp
    points.sort(key=lambda point: point.source_range.start.seconds)
    return _track("semantic", "semantic", configuration_hash, tuple(points))


def _text_labels(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    labels: list[str] = []
    if "?" in text or any(term in lowered for term in ("how to", "why ", "what if")):
        labels.append("question_or_hook")
    if "!" in text or any(term in lowered for term in ("wow", "no way", "insane", "amazing")):
        labels.append("reaction")
    if any(term in lowered for term in ("first", "next", "then", "finally", "step")):
        labels.append("instruction")
    if any(term in lowered for term in ("result", "there you go", "we did it", "won")):
        labels.append("payoff")
    return tuple(labels)


def _events_from_track(
    semantic: AnalysisTrack,
    original: tuple[TimelineEvent, ...],
) -> tuple[TimelineEvent, ...]:
    events = list(original)
    existing = {(round(event.time_seconds, 1), event.event_type) for event in original}
    for point in semantic.points:
        for label in point.labels:
            key = (round(point.source_range.start.seconds, 1), label)
            if key in existing:
                continue
            events.append(
                TimelineEvent(
                    time_seconds=point.source_range.start.seconds,
                    event_type=label,
                    label=point.text or label.replace("_", " ").title(),
                    importance=point.confidence,
                )
            )
            existing.add(key)
    return tuple(sorted(events, key=lambda event: event.time_seconds))


def _cached_track(
    directory: Path,
    name: str,
    configuration_hash: str,
    build: Callable[[], AnalysisTrack],
) -> tuple[AnalysisTrack, bool]:
    path = directory / f"{name}.json"
    if path.is_file():
        cached = AnalysisTrack.model_validate_json(path.read_text(encoding="utf-8"))
        if cached.provenance.configuration_hash == configuration_hash:
            return cached, True
    track = build()
    _atomic_write(path, track.model_dump_json(indent=2))
    return track, False


def _track(
    name: str,
    kind: Literal["motion", "audio", "color", "shot", "transcript", "semantic"],
    configuration_hash: str,
    points: tuple[TrackPoint, ...],
) -> AnalysisTrack:
    return AnalysisTrack(
        name=name,
        kind=kind,
        provenance=Provenance(
            analyzer=f"nimbledesk-{name}",
            analyzer_version=ANALYZER_VERSION,
            configuration_hash=configuration_hash,
        ),
        points=points,
    )


def _configuration_hash(asset_hash: str, configuration: object) -> str:
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(asset_hash.encode() + encoded).hexdigest()


def _track_digest(track: AnalysisTrack) -> str:
    return hashlib.sha256(track.model_dump_json().encode()).hexdigest()


def _sample_rate(duration_seconds: float) -> float:
    if duration_seconds > 7_200:
        return 0.25
    if duration_seconds > 1_800:
        return 0.5
    return 2


def _profile(duration_seconds: float) -> str:
    if duration_seconds > 7_200:
        return "multi_hour_coarse"
    if duration_seconds > 1_800:
        return "long_form"
    return "standard"


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
