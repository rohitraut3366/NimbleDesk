from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.media.models import TimelineEvent
from nimbledesk.media.process import CancellationCheck, run_cancellable


class GamePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    sample_interval_seconds: float = Field(default=2, ge=0.25, le=30)
    crop: str | None = None
    phrases: dict[str, tuple[str, ...]]
    importance: dict[str, float] = Field(default_factory=dict)


DEFAULT_GAME_PACK = GamePack(
    name="generic-shooter",
    phrases={
        "multi_kill": ("double kill", "triple kill", "quad kill", "multi kill"),
        "grenade_kill": ("grenade kill", "frag kill"),
        "clutch": ("clutch", "last player standing"),
        "narrow_survival": ("low health", "critical health"),
        "victory": ("victory", "winner", "round won"),
        "kill": ("eliminated", "enemy killed", "you killed"),
    },
    importance={
        "multi_kill": 1,
        "grenade_kill": 1,
        "clutch": 1,
        "narrow_survival": 0.9,
        "victory": 0.9,
        "kill": 0.75,
    },
)


class GameAnalysisError(RuntimeError):
    pass


def load_game_pack(path: Path | None) -> GamePack:
    if path is None:
        return DEFAULT_GAME_PACK
    return GamePack.model_validate_json(path.read_text(encoding="utf-8"))


def detect_game_events(
    source: Path,
    pack: GamePack,
    cancelled: CancellationCheck | None = None,
) -> tuple[TimelineEvent, ...]:
    ffmpeg = shutil.which("ffmpeg")
    tesseract = shutil.which("tesseract")
    if ffmpeg is None or tesseract is None:
        raise GameAnalysisError("automatic game OCR requires ffmpeg and tesseract on PATH")
    with tempfile.TemporaryDirectory(prefix="nimbledesk-game-ocr-") as temporary:
        directory = Path(temporary)
        frame_pattern = directory / "frame_%08d.jpg"
        filters = [f"fps=1/{pack.sample_interval_seconds}"]
        if pack.crop:
            filters.append(f"crop={pack.crop}")
        command = [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            ",".join(filters),
            "-q:v",
            "4",
            str(frame_pattern),
        ]
        completed = run_cancellable(command, cancelled=cancelled)
        if completed.returncode != 0:
            raise GameAnalysisError(completed.stderr.strip() or "game frame extraction failed")
        return _ocr_frames(
            tuple(sorted(directory.glob("frame_*.jpg"))), pack, tesseract, cancelled
        )


def write_events(events: tuple[TimelineEvent, ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([event.model_dump(mode="json") for event in events], indent=2),
        encoding="utf-8",
    )


def _ocr_frames(
    frames: tuple[Path, ...],
    pack: GamePack,
    tesseract: str,
    cancelled: CancellationCheck | None,
) -> tuple[TimelineEvent, ...]:
    events: list[TimelineEvent] = []
    last_seen: dict[str, float] = {}
    for index, frame in enumerate(frames):
        completed = run_cancellable(
            [tesseract, str(frame), "stdout", "--psm", "11"],
            cancelled=cancelled,
        )
        if completed.returncode != 0:
            continue
        normalized = re.sub(r"\s+", " ", completed.stdout.casefold())
        timestamp = index * pack.sample_interval_seconds
        for event_type, phrases in pack.phrases.items():
            matched = next((phrase for phrase in phrases if phrase.casefold() in normalized), None)
            if matched is None or timestamp - last_seen.get(event_type, -60) < 5:
                continue
            events.append(
                TimelineEvent(
                    time_seconds=timestamp,
                    event_type=event_type,
                    label=matched.title(),
                    importance=pack.importance.get(event_type, 0.8),
                )
            )
            last_seen[event_type] = timestamp
    return tuple(events)
