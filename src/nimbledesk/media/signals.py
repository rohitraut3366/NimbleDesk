from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from nimbledesk.media.ffmpeg import MediaToolError

FloatArray = NDArray[np.float64]


def extract_motion_signal(path: Path, frames_per_second: float) -> FloatArray:
    frame_width = 160
    frame_height = 90
    frame_size = frame_width * frame_height
    filter_graph = (
        f"fps={frames_per_second},"
        f"scale={frame_width}:{frame_height}:force_original_aspect_ratio=decrease,"
        f"pad={frame_width}:{frame_height}:(ow-iw)/2:(oh-ih)/2,format=gray"
    )
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-an",
        "-vf",
        filter_graph,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise MediaToolError("ffmpeg did not create a motion-analysis stream")
    scores: list[float] = []
    previous: NDArray[np.uint8] | None = None
    while frame_bytes := process.stdout.read(frame_size):
        if len(frame_bytes) != frame_size:
            break
        frame = np.frombuffer(frame_bytes, dtype=np.uint8)
        score = 0.0
        if previous is not None:
            difference = np.abs(frame.astype(np.int16) - previous.astype(np.int16))
            score = float(np.mean(difference))
        scores.append(score)
        previous = frame
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise MediaToolError(stderr.decode(errors="replace").strip() or "motion analysis failed")
    return np.asarray(scores, dtype=np.float64)


def extract_audio_signal(path: Path, window_seconds: float, sample_rate: int = 8_000) -> FloatArray:
    samples_per_window = max(1, int(sample_rate * window_seconds))
    bytes_per_window = samples_per_window * 2
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
        "s16le",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise MediaToolError("ffmpeg did not create an audio-analysis stream")
    scores: list[float] = []
    while audio_bytes := process.stdout.read(bytes_per_window):
        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64)
        if samples.size:
            scores.append(float(np.sqrt(np.mean(np.square(samples))) / 32768.0))
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise MediaToolError(stderr.decode(errors="replace").strip() or "audio analysis failed")
    return np.asarray(scores, dtype=np.float64)


def normalize_signal(values: FloatArray) -> FloatArray:
    if values.size == 0:
        return values
    lower = float(np.percentile(values, 20))
    upper = float(np.percentile(values, 95))
    if upper <= lower:
        return np.zeros(values.shape, dtype=np.float64)
    return np.clip((values - lower) / (upper - lower), 0, 1)
