from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
