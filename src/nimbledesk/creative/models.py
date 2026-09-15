from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContentKind(StrEnum):
    AUTO = "auto"
    GAMEPLAY = "gameplay"
    TALKING_HEAD = "talking_head"
    TUTORIAL = "tutorial"
    VLOG = "vlog"


class Pace(StrEnum):
    CALM = "calm"
    BALANCED = "balanced"
    FAST = "fast"


class AspectRatio(StrEnum):
    LANDSCAPE = "16:9"
    VERTICAL = "9:16"
    SQUARE = "1:1"


class CreativeBrief(CreativeModel):
    title: str = "Untitled creation"
    content_kind: ContentKind = ContentKind.AUTO
    audience: str = "general"
    platform: str = "youtube"
    target_duration_seconds: Annotated[float, Field(ge=5, le=14_400)] = 60
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE
    pace: Pace = Pace.BALANCED
    mood: str = "engaging"
    clip_count: Annotated[int, Field(ge=1, le=100)] = 10
    captions: bool = True
    music: bool = True
    color_look: str = "natural_contrast"
    mandatory_event_types: tuple[str, ...] = ()
    excluded_event_types: tuple[str, ...] = ()


class TimeRange(CreativeModel):
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def ordered(self) -> TimeRange:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("time range end must be after start")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class Evidence(CreativeModel):
    analyzer: str
    analyzer_version: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    description: str
    source_range: TimeRange | None = None


class TranscriptSegment(CreativeModel):
    source_range: TimeRange
    text: str
    confidence: Annotated[float, Field(ge=0, le=1)] = 1
    speaker: str | None = None


class SemanticEvent(CreativeModel):
    source_range: TimeRange
    event_type: str
    label: str
    importance: Annotated[float, Field(ge=0, le=1)]
    evidence: tuple[Evidence, ...]


class MusicAsset(CreativeModel):
    path: Path
    duration_seconds: Annotated[float, Field(gt=0)]
    title: str
    mood: tuple[str, ...] = ()
    bpm: Annotated[float, Field(gt=0, le=400)] | None = None
    energy: Annotated[float, Field(ge=0, le=1)] = 0.5
    instrumental: bool = True
    license: str
    attribution: str | None = None


class SpeedTreatment(CreativeModel):
    rate: Annotated[float, Field(ge=0.25, le=8)] = 1
    interpolation: Literal["nearest", "frame_blend", "optical_flow"] = "nearest"
    preserve_pitch: bool = True
    rationale: str


class VisualTreatment(CreativeModel):
    transition_in: Literal["cut", "cross_dissolve", "dip_to_black"] = "cut"
    punch_in_scale: Annotated[float, Field(ge=1, le=2)] = 1
    color_look: str = "natural_contrast"
    exposure_adjustment_stops: Annotated[float, Field(ge=-2, le=2)] = 0
    saturation_multiplier: Annotated[float, Field(ge=0.5, le=1.5)] = 1
    title: str | None = None
    rationale: str


class EditSegment(CreativeModel):
    segment_id: str
    role: Literal["hook", "setup", "development", "payoff", "outro"]
    source_path: Path
    source_range: TimeRange
    timeline_start_seconds: Annotated[float, Field(ge=0)]
    speed: SpeedTreatment
    visual: VisualTreatment
    score: Annotated[float, Field(ge=0)]
    evidence: tuple[Evidence, ...]

    @property
    def timeline_duration_seconds(self) -> float:
        return self.source_range.duration_seconds / self.speed.rate


class MusicCue(CreativeModel):
    asset: MusicAsset
    source_range: TimeRange
    timeline_range: TimeRange
    gain_db: Annotated[float, Field(ge=-60, le=12)] = -18
    duck_under_dialogue_db: Annotated[float, Field(ge=-60, le=0)] = -8
    beat_interval_seconds: Annotated[float, Field(gt=0)] | None = None
    beat_aligned_timeline_seconds: Annotated[float, Field(ge=0)] | None = None
    rationale: str


class CaptionCue(CreativeModel):
    timeline_range: TimeRange
    text: str
    speaker: str | None = None


class ReviewItem(CreativeModel):
    severity: Literal["info", "warning", "blocking"]
    message: str
    segment_id: str | None = None


class DeliverySpec(CreativeModel):
    width: Annotated[int, Field(ge=320, le=7680)]
    height: Annotated[int, Field(ge=320, le=7680)]
    frame_rate: Annotated[float, Field(gt=0, le=240)]
    video_codec: str = "h264"
    audio_codec: str = "aac"
    audio_loudness_lufs: Annotated[float, Field(ge=-30, le=-5)] = -14


class EditPlan(CreativeModel):
    plan_version: Literal["1.0.0"] = "1.0.0"
    source_path: Path
    brief: CreativeBrief
    segments: tuple[EditSegment, ...]
    music_cue: MusicCue | None = None
    captions: tuple[CaptionCue, ...] = ()
    delivery: DeliverySpec
    review_items: tuple[ReviewItem, ...] = ()

    @property
    def duration_seconds(self) -> float:
        if not self.segments:
            return 0
        last = self.segments[-1]
        return last.timeline_start_seconds + last.timeline_duration_seconds
