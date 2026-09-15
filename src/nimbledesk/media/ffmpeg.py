from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from nimbledesk.media.models import MediaMetadata


class MediaToolError(RuntimeError):
    pass


def require_media_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise MediaToolError(f"required media tools are missing: {', '.join(missing)}")


def probe_media(path: Path) -> MediaMetadata:
    require_media_tools()
    resolved_path = path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"media file does not exist: {resolved_path}")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(resolved_path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or "ffprobe failed")
    payload: dict[str, Any] = json.loads(completed.stdout)
    video_stream = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise MediaToolError("input does not contain a video stream")
    audio_stream = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    duration = _duration(payload, video_stream)
    frame_rate = _frame_rate(video_stream)
    return MediaMetadata(
        path=resolved_path,
        duration_seconds=duration,
        width=int(video_stream["width"]),
        height=int(video_stream["height"]),
        frame_rate=frame_rate,
        has_audio=audio_stream is not None,
        video_codec=str(video_stream.get("codec_name", "unknown")),
        audio_codec=str(audio_stream.get("codec_name")) if audio_stream else None,
    )


def _duration(payload: dict[str, Any], video_stream: dict[str, Any]) -> float:
    raw_duration = payload.get("format", {}).get("duration") or video_stream.get("duration")
    if raw_duration is None:
        raise MediaToolError("input duration is unavailable")
    duration = float(raw_duration)
    if duration <= 0:
        raise MediaToolError("input duration must be positive")
    return duration


def _frame_rate(video_stream: dict[str, Any]) -> float:
    raw_rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    try:
        rate = float(Fraction(str(raw_rate)))
    except (ValueError, ZeroDivisionError) as error:
        raise MediaToolError(f"invalid frame rate: {raw_rate}") from error
    if rate <= 0:
        raise MediaToolError("frame rate must be positive")
    return rate
