from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Annotated

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.media.models import TimelineEvent
from nimbledesk.media.process import CancellationCheck, run_cancellable

MAXIMUM_RESPONSE_BYTES = 1_000_000


class VisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisionProviderConfig(VisionModel):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,79}$")
    model: str
    command: tuple[str, ...] = Field(min_length=1)
    sample_interval_seconds: Annotated[float, Field(ge=0.5, le=60)] = 4
    maximum_frames: Annotated[int, Field(ge=4, le=240)] = 48
    timeout_seconds: Annotated[float, Field(ge=5, le=3600)] = 300
    minimum_confidence: Annotated[float, Field(ge=0, le=1)] = 0.65


class VisionSheet(VisionModel):
    path: Path
    timestamps_seconds: tuple[float, ...]


class VisionRequest(VisionModel):
    request_version: str = "1.0.0"
    source_name: str
    content_kind: str
    sheets: tuple[VisionSheet, ...]
    requested_events: tuple[str, ...]


class VisionEvent(VisionModel):
    time_seconds: Annotated[float, Field(ge=0)]
    event_type: str
    label: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence: str


class VisionProviderResponse(VisionModel):
    events: tuple[VisionEvent, ...]


class VisionAnalysis(VisionModel):
    analysis_version: str = "1.0.0"
    provider_id: str
    model: str
    configuration_hash: str
    sampled_frames: int
    contact_sheets: tuple[Path, ...]
    events: tuple[VisionEvent, ...]

    def timeline_events(self) -> tuple[TimelineEvent, ...]:
        return tuple(
            TimelineEvent(
                time_seconds=event.time_seconds,
                event_type=event.event_type,
                label=event.label,
                importance=event.confidence,
            )
            for event in self.events
        )


class VisionAnalysisError(RuntimeError):
    pass


def analyze_with_vision_provider(
    source: Path,
    output_directory: Path,
    config_path: Path,
    content_kind: str,
    cancelled: CancellationCheck | None = None,
) -> VisionAnalysis:
    config = VisionProviderConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    output_directory.mkdir(parents=True, exist_ok=True)
    frames_directory = output_directory / "frames"
    frames_directory.mkdir(parents=True, exist_ok=True)
    frames = _extract_frames(source, frames_directory, config, cancelled)
    sheets = _write_contact_sheets(frames, config.sample_interval_seconds, output_directory)
    request = VisionRequest(
        source_name=source.name,
        content_kind=content_kind,
        sheets=sheets,
        requested_events=(
            "kill",
            "multi_kill",
            "grenade_kill",
            "clutch",
            "narrow_survival",
            "victory",
            "reaction",
            "question_or_hook",
            "payoff",
        ),
    )
    request_path = output_directory / "request.json"
    response_path = output_directory / "response.json"
    request_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
    command = _provider_command(config.command, request_path, response_path)
    try:
        completed = run_cancellable(
            command,
            cancelled=cancelled,
            timeout_seconds=config.timeout_seconds,
        )
    except TimeoutError as error:
        raise VisionAnalysisError("semantic vision provider timed out") from error
    if completed.returncode != 0:
        raise VisionAnalysisError(
            completed.stderr[:8_192].strip() or "semantic vision provider failed"
        )
    if not response_path.is_file():
        raise VisionAnalysisError("semantic vision provider did not write its response")
    if response_path.stat().st_size > MAXIMUM_RESPONSE_BYTES:
        raise VisionAnalysisError("semantic vision response exceeded one megabyte")
    response = VisionProviderResponse.model_validate_json(
        response_path.read_text(encoding="utf-8")
    )
    accepted = tuple(
        event for event in response.events if event.confidence >= config.minimum_confidence
    )
    configuration_hash = hashlib.sha256(
        config.model_dump_json().encode("utf-8") + source.resolve().as_posix().encode("utf-8")
    ).hexdigest()
    analysis = VisionAnalysis(
        provider_id=config.provider_id,
        model=config.model,
        configuration_hash=configuration_hash,
        sampled_frames=len(frames),
        contact_sheets=tuple(sheet.path for sheet in sheets),
        events=accepted,
    )
    (output_directory / "analysis.json").write_text(
        analysis.model_dump_json(indent=2), encoding="utf-8"
    )
    return analysis


def _extract_frames(
    source: Path,
    directory: Path,
    config: VisionProviderConfig,
    cancelled: CancellationCheck | None,
) -> tuple[Path, ...]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VisionAnalysisError("semantic vision requires ffmpeg on PATH")
    pattern = directory / "frame_%05d.jpg"
    completed = run_cancellable(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            f"fps=1/{config.sample_interval_seconds},scale=640:-2",
            "-frames:v",
            str(config.maximum_frames),
            "-q:v",
            "4",
            str(pattern),
        ],
        cancelled=cancelled,
    )
    if completed.returncode != 0:
        raise VisionAnalysisError(completed.stderr.strip() or "vision sampling failed")
    frames = tuple(sorted(directory.glob("frame_*.jpg")))
    if not frames:
        raise VisionAnalysisError("semantic vision produced no sample frames")
    return frames


def _write_contact_sheets(
    frames: tuple[Path, ...], interval: float, output_directory: Path
) -> tuple[VisionSheet, ...]:
    sheets: list[VisionSheet] = []
    for sheet_index, start in enumerate(range(0, len(frames), 12), start=1):
        group = frames[start : start + 12]
        canvas = Image.new("RGB", (1280, 588), (10, 10, 10))
        draw = ImageDraw.Draw(canvas)
        timestamps: list[float] = []
        for local_index, frame_path in enumerate(group):
            timestamp = (start + local_index) * interval
            timestamps.append(timestamp)
            with Image.open(frame_path) as frame:
                thumbnail = frame.convert("RGB")
                thumbnail.thumbnail((320, 180))
                x = local_index % 4 * 320
                y = local_index // 4 * 196
                canvas.paste(thumbnail, (x, y))
                draw.rectangle((x, y + 160, x + 110, y + 180), fill=(0, 0, 0))
                draw.text((x + 5, y + 163), f"{timestamp:.1f}s", fill=(255, 255, 255))
        path = output_directory / f"contact-sheet-{sheet_index:03d}.jpg"
        canvas.save(path, quality=82, optimize=True)
        sheets.append(VisionSheet(path=path, timestamps_seconds=tuple(timestamps)))
    return tuple(sheets)


def _provider_command(
    command: tuple[str, ...], request_path: Path, response_path: Path
) -> list[str]:
    if "{request}" not in command or "{response}" not in command:
        raise VisionAnalysisError(
            "vision provider command must contain {request} and {response} arguments"
        )
    return [
        argument.replace("{request}", str(request_path)).replace(
            "{response}", str(response_path)
        )
        for argument in command
    ]
