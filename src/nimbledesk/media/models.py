from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class MediaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaMetadata(MediaModel):
    path: Path
    duration_seconds: Annotated[float, Field(gt=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    frame_rate: Annotated[float, Field(gt=0)]
    has_audio: bool
    video_codec: str
    audio_codec: str | None = None
    container: str = "unknown"
    nominal_frame_rate: Annotated[float, Field(gt=0)] | None = None
    variable_frame_rate: bool = False
    time_base: str | None = None
    pixel_aspect_ratio: str = "1:1"
    rotation_degrees: Literal[0, 90, 180, 270] = 0
    pixel_format: str | None = None
    bit_depth: Annotated[int, Field(ge=1, le=64)] | None = None
    color_range: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    hdr: bool = False
    audio_channels: Annotated[int, Field(gt=0)] | None = None
    audio_channel_layout: str | None = None
    audio_sample_rate: Annotated[int, Field(gt=0)] | None = None
    embedded_timecode: str | None = None
    format_start_seconds: float = 0
    bitrate: Annotated[int, Field(ge=0)] | None = None


class MediaIntegrityReport(MediaModel):
    source_sha256: str | None = None
    valid: bool
    video_decoded: bool
    audio_decoded: bool
    error: str | None = None


class AnalysisConfig(MediaModel):
    sample_frames_per_second: Annotated[float, Field(gt=0, le=10)] = 2
    audio_window_seconds: Annotated[float, Field(ge=0.25, le=2)] = 0.5
    lead_in_seconds: Annotated[float, Field(ge=0, le=60)] = 8
    aftermath_seconds: Annotated[float, Field(ge=0, le=60)] = 12
    highlight_count: Annotated[int, Field(ge=1, le=100)] = 10
    minimum_peak_separation_seconds: Annotated[float, Field(ge=1, le=300)] = 20
    output_width: Annotated[int, Field(ge=320, le=7680)] | None = None
    video_quality: Annotated[int, Field(ge=0, le=51)] = 20


class TimelineEvent(MediaModel):
    time_seconds: Annotated[float, Field(ge=0)]
    event_type: str
    label: str | None = None
    importance: Annotated[float, Field(ge=0, le=1)] = 1


class SignalPoint(MediaModel):
    time_seconds: float
    motion: float
    audio: float
    event: float
    combined: float


class HighlightCandidate(MediaModel):
    rank: int
    start_seconds: float
    end_seconds: float
    peak_seconds: float
    score: float
    reasons: tuple[str, ...]
    event_labels: tuple[str, ...] = ()


class RenderedClip(MediaModel):
    candidate: HighlightCandidate
    output_path: Path
    duration_seconds: float
    width: int
    height: int


class HighlightManifest(MediaModel):
    source: MediaMetadata
    config: AnalysisConfig
    candidates: tuple[HighlightCandidate, ...]
    clips: tuple[RenderedClip, ...]
