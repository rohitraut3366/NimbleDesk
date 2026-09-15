from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from nimbledesk.creative.models import MusicAsset
from nimbledesk.media.ffmpeg import MediaToolError, require_media_tools

SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


def index_music_directory(
    source: Path,
    *,
    license_terms: str,
    attribution: str | None = None,
    declared_moods: tuple[str, ...] = (),
    instrumental: bool = True,
) -> tuple[MusicAsset, ...]:
    if not license_terms.strip():
        raise ValueError("music license terms cannot be empty")
    resolved = source.expanduser().resolve()
    paths: tuple[Path, ...]
    if resolved.is_file():
        paths = (resolved,)
    elif resolved.is_dir():
        paths = tuple(
            sorted(
                path
                for path in resolved.rglob("*")
                if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
            )
        )
    else:
        raise FileNotFoundError(f"music source does not exist: {resolved}")
    if not paths:
        raise ValueError(f"no supported music found in {resolved}")
    return tuple(
        analyze_music(
            path,
            license_terms=license_terms,
            attribution=attribution,
            declared_moods=declared_moods,
            instrumental=instrumental,
        )
        for path in paths
    )


def analyze_music(
    path: Path,
    *,
    license_terms: str,
    attribution: str | None,
    declared_moods: tuple[str, ...],
    instrumental: bool,
) -> MusicAsset:
    require_media_tools()
    duration = _audio_duration(path)
    sample_rate = 8_000
    samples = _decode_audio(path, sample_rate)
    energy = min(1.0, float(np.sqrt(np.mean(np.square(samples)))) * 4) if samples.size else 0
    bpm = _estimate_tempo(samples, sample_rate)
    inferred_moods = _infer_moods(energy, bpm)
    moods = tuple(dict.fromkeys((*declared_moods, *inferred_moods)))
    return MusicAsset(
        path=path.resolve(),
        duration_seconds=round(duration, 3),
        title=path.stem.replace("_", " ").replace("-", " ").strip().title(),
        mood=moods,
        bpm=round(bpm, 2) if bpm else None,
        energy=round(energy, 4),
        instrumental=instrumental,
        license=license_terms.strip(),
        attribution=attribution,
    )


def write_music_catalog(assets: tuple[MusicAsset, ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asset.model_dump(mode="json") for asset in assets], indent=2),
        encoding="utf-8",
    )


def _audio_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or f"could not inspect {path}")
    payload: dict[str, Any] = json.loads(completed.stdout)
    duration = float(payload.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise MediaToolError(f"music duration is unavailable: {path}")
    return duration


def _decode_audio(path: Path, sample_rate: int) -> np.ndarray[Any, np.dtype[np.float64]]:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.decode(errors="replace").strip())
    return np.frombuffer(completed.stdout, dtype=np.float32).astype(np.float64)


def _estimate_tempo(
    samples: np.ndarray[Any, np.dtype[np.float64]], sample_rate: int
) -> float | None:
    window_size = max(1, round(sample_rate * 0.05))
    window_count = samples.size // window_size
    if window_count < 40:
        return None
    framed = samples[: window_count * window_size].reshape((window_count, window_size))
    envelope = np.sqrt(np.mean(np.square(framed), axis=1))
    onset = np.maximum(0, np.diff(envelope, prepend=envelope[0]))
    if float(np.max(onset)) <= 1e-6:
        return None
    onset -= np.mean(onset)
    correlation = np.correlate(onset, onset, mode="full")[onset.size - 1 :]
    windows_per_second = sample_rate / window_size
    minimum_lag = max(1, math.floor(windows_per_second * 60 / 180))
    maximum_lag = min(correlation.size - 1, math.ceil(windows_per_second * 60 / 60))
    if maximum_lag <= minimum_lag:
        return None
    lag = minimum_lag + int(np.argmax(correlation[minimum_lag : maximum_lag + 1]))
    strength = correlation[lag] / max(correlation[0], 1e-12)
    if strength < 0.08:
        return None
    return 60 * windows_per_second / lag


def _infer_moods(energy: float, bpm: float | None) -> tuple[str, ...]:
    if energy < 0.25 and (bpm is None or bpm < 100):
        return ("calm", "reflective")
    if energy > 0.7 and (bpm is None or bpm >= 110):
        return ("exciting", "energetic")
    if bpm and bpm >= 125:
        return ("driving", "tense")
    return ("balanced",)
