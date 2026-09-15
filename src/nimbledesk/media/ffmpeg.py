from __future__ import annotations

import json
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from nimbledesk.media.models import MediaIntegrityReport, MediaMetadata
from nimbledesk.media.process import CancellationCheck, run_cancellable


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
    nominal_frame_rate = _rate(video_stream.get("r_frame_rate"))
    rotation = _rotation(video_stream)
    width = int(video_stream["width"])
    height = int(video_stream["height"])
    if rotation in {90, 270}:
        width, height = height, width
    transfer = _optional_text(video_stream.get("color_transfer"))
    return MediaMetadata(
        path=resolved_path,
        duration_seconds=duration,
        width=width,
        height=height,
        frame_rate=frame_rate,
        has_audio=audio_stream is not None,
        video_codec=str(video_stream.get("codec_name", "unknown")),
        audio_codec=str(audio_stream.get("codec_name")) if audio_stream else None,
        container=str(payload.get("format", {}).get("format_name", "unknown")),
        nominal_frame_rate=nominal_frame_rate,
        variable_frame_rate=_is_variable_frame_rate(frame_rate, nominal_frame_rate),
        time_base=_optional_text(video_stream.get("time_base")),
        pixel_aspect_ratio=_pixel_aspect_ratio(video_stream),
        rotation_degrees=rotation,
        pixel_format=_optional_text(video_stream.get("pix_fmt")),
        bit_depth=_bit_depth(video_stream),
        color_range=_optional_text(video_stream.get("color_range")),
        color_primaries=_optional_text(video_stream.get("color_primaries")),
        color_transfer=transfer,
        color_space=_optional_text(video_stream.get("color_space")),
        hdr=_is_hdr(video_stream, transfer),
        audio_channels=(
            int(audio_stream["channels"])
            if audio_stream and audio_stream.get("channels")
            else None
        ),
        audio_channel_layout=(
            _optional_text(audio_stream.get("channel_layout")) if audio_stream else None
        ),
        audio_sample_rate=(
            int(audio_stream["sample_rate"])
            if audio_stream and audio_stream.get("sample_rate")
            else None
        ),
        embedded_timecode=_timecode(payload, video_stream),
        format_start_seconds=float(payload.get("format", {}).get("start_time") or 0),
        bitrate=_optional_int(payload.get("format", {}).get("bit_rate")),
    )


def check_media_integrity(
    path: Path,
    metadata: MediaMetadata | None = None,
    *,
    source_sha256: str | None = None,
    cancelled: CancellationCheck | None = None,
) -> MediaIntegrityReport:
    """Decode every source stream and fail on corrupt or truncated media."""
    inspected = metadata or probe_media(path)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-xerror",
        "-err_detect",
        "explode",
        "-i",
        str(inspected.path),
        "-map",
        "0:v:0",
    ]
    if inspected.has_audio:
        command.extend(["-map", "0:a:0"])
    command.extend(["-f", "null", "-"])
    completed = run_cancellable(command, cancelled=cancelled)
    error = completed.stderr.strip() if completed.returncode != 0 else None
    return MediaIntegrityReport(
        source_sha256=source_sha256,
        valid=completed.returncode == 0,
        video_decoded=completed.returncode == 0,
        audio_decoded=inspected.has_audio and completed.returncode == 0,
        error=error or None,
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


def _rate(value: object) -> float | None:
    if value in {None, "0/0", "N/A"}:
        return None
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _is_variable_frame_rate(average: float, nominal: float | None) -> bool:
    if nominal is None:
        return False
    return abs(average - nominal) / nominal > 0.01


def _rotation(stream: dict[str, Any]) -> Literal[0, 90, 180, 270]:
    raw: object = stream.get("tags", {}).get("rotate")
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            raw = side_data["rotation"]
            break
    try:
        normalized = round(float(str(raw or 0))) % 360
    except (TypeError, ValueError):
        normalized = 0
    return min((0, 90, 180, 270), key=lambda value: abs(value - normalized))


def _pixel_aspect_ratio(stream: dict[str, Any]) -> str:
    value = str(stream.get("sample_aspect_ratio") or "1:1")
    return "1:1" if value in {"0:1", "N/A"} else value


def _bit_depth(stream: dict[str, Any]) -> int | None:
    explicit = _optional_int(stream.get("bits_per_raw_sample"))
    if explicit:
        return explicit
    pixel_format = str(stream.get("pix_fmt") or "")
    match = re.search(r"(?:p|le|be)(9|10|12|14|16)(?:le|be)?$", pixel_format)
    return int(match.group(1)) if match else 8 if pixel_format else None


def _is_hdr(stream: dict[str, Any], transfer: str | None) -> bool:
    if transfer in {"smpte2084", "arib-std-b67"}:
        return True
    hdr_metadata_types = {
        "Mastering display metadata",
        "Content light level metadata",
        "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)",
    }
    return any(
        side_data.get("side_data_type") in hdr_metadata_types
        for side_data in stream.get("side_data_list", [])
    )


def _timecode(payload: dict[str, Any], stream: dict[str, Any]) -> str | None:
    stream_timecode = stream.get("tags", {}).get("timecode")
    format_timecode = payload.get("format", {}).get("tags", {}).get("timecode")
    return _optional_text(stream_timecode or format_timecode)


def _optional_text(value: object) -> str | None:
    if value in {None, "", "unknown", "N/A"}:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
