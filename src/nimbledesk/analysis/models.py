from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nimbledesk.media.models import MediaMetadata, TimelineEvent


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RationalTime(AnalysisModel):
    value: Annotated[int, Field(ge=0)]
    timescale: Annotated[int, Field(gt=0)] = 1_000

    @classmethod
    def from_seconds(cls, seconds: float, timescale: int = 1_000) -> RationalTime:
        return cls(value=max(0, round(seconds * timescale)), timescale=timescale)

    @property
    def seconds(self) -> float:
        return self.value / self.timescale


class RationalRange(AnalysisModel):
    start: RationalTime
    duration: RationalTime

    @model_validator(mode="after")
    def matching_timescales(self) -> RationalRange:
        if self.start.timescale != self.duration.timescale:
            raise ValueError("range start and duration must use the same timescale")
        if self.duration.value <= 0:
            raise ValueError("range duration must be positive")
        return self

    @classmethod
    def from_seconds(cls, start: float, duration: float) -> RationalRange:
        return cls(
            start=RationalTime.from_seconds(start),
            duration=RationalTime.from_seconds(duration),
        )


class Provenance(AnalysisModel):
    analyzer: str
    analyzer_version: str
    provider: Literal["local", "supplied", "remote"] = "local"
    configuration_hash: str


class TrackPoint(AnalysisModel):
    source_range: RationalRange
    confidence: Annotated[float, Field(ge=0, le=1)]
    metrics: dict[str, float] = Field(default_factory=dict)
    labels: tuple[str, ...] = ()
    text: str | None = None
    evidence: tuple[str, ...] = ()


class AnalysisTrack(AnalysisModel):
    name: str
    kind: Literal["motion", "audio", "color", "shot", "transcript", "semantic"]
    provenance: Provenance
    points: tuple[TrackPoint, ...]


class AssetRecord(AnalysisModel):
    asset_id: str
    path: Path
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: Annotated[int, Field(gt=0)]
    modified_ns: Annotated[int, Field(ge=0)]
    metadata: MediaMetadata


class ContentIndex(AnalysisModel):
    index_version: Literal["1.0.0"] = "1.0.0"
    asset: AssetRecord
    tracks: tuple[AnalysisTrack, ...]
    semantic_events: tuple[TimelineEvent, ...] = ()
    cache_hits: tuple[str, ...] = ()
    analyzer_metadata: dict[str, Any] = Field(default_factory=dict)

    def track(self, name: str) -> AnalysisTrack:
        match = next((track for track in self.tracks if track.name == name), None)
        if match is None:
            raise KeyError(name)
        return match
