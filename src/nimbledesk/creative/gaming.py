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
    patterns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    importance: dict[str, float] = Field(default_factory=dict)
    cooldown_seconds: dict[str, float] = Field(default_factory=dict)
    multi_kill_window_seconds: float = Field(default=8, ge=1, le=30)
    clutch_window_seconds: float = Field(default=20, ge=1, le=60)


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
    patterns={
        "kill": (
            r"\b(?:killed|eliminated|knocked|downed|fragged)\s+[a-z0-9_]",
            r"[a-z0-9_]\s+(?:was\s+)?(?:killed|eliminated|knocked|downed)",
        ),
        "grenade_kill": (r"\b(?:grenade|frag|semtex|molotov)\b.*\b(?:kill|eliminat)",),
        "narrow_survival": (
            r"\b(?:[1-9]|1[0-5])\s*(?:hp|health)\b",
            r"\b(?:critical|low)\s+(?:hp|health)\b",
        ),
        "victory": (r"\b(?:victory|winner|champion|round\s+won|you\s+win)\b",),
    },
    importance={
        "multi_kill": 1,
        "grenade_kill": 1,
        "clutch": 1,
        "narrow_survival": 0.9,
        "victory": 0.9,
        "kill": 0.75,
    },
    cooldown_seconds={"kill": 1.5, "narrow_survival": 8},
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
    samples: list[tuple[float, str]] = []
    for index, frame in enumerate(frames):
        completed = run_cancellable(
            [tesseract, str(frame), "stdout", "--psm", "11"],
            cancelled=cancelled,
        )
        if completed.returncode != 0:
            continue
        normalized = re.sub(r"\s+", " ", completed.stdout.casefold())
        timestamp = index * pack.sample_interval_seconds
        samples.append((timestamp, normalized))
    return detect_events_from_ocr_samples(tuple(samples), pack)


def detect_events_from_ocr_samples(
    samples: tuple[tuple[float, str], ...],
    pack: GamePack,
) -> tuple[TimelineEvent, ...]:
    """Turn normalized OCR samples into direct and temporally inferred game events."""
    events: list[TimelineEvent] = []
    last_seen: dict[str, float] = {}
    event_types = set(pack.phrases) | set(pack.patterns)
    for timestamp, sample in samples:
        normalized = re.sub(r"\s+", " ", sample.casefold())
        for event_type in sorted(event_types):
            matched = _match_event(
                normalized,
                pack.phrases.get(event_type, ()),
                pack.patterns.get(event_type, ()),
            )
            cooldown = pack.cooldown_seconds.get(event_type, 5)
            if matched is None or timestamp - last_seen.get(event_type, -60) < cooldown:
                continue
            events.append(
                TimelineEvent(
                    time_seconds=timestamp,
                    event_type=event_type,
                    label=matched.title(),
                    importance=pack.importance.get(event_type, 0.8),
                    provenance=(f"game-pack:{pack.name}:ocr",),
                    evidence=(f'OCR matched "{matched}"',),
                )
            )
            last_seen[event_type] = timestamp
    return _infer_compound_events(tuple(events), pack)


def _match_event(
    text: str,
    phrases: tuple[str, ...],
    patterns: tuple[str, ...],
) -> str | None:
    phrase = next((candidate for candidate in phrases if candidate.casefold() in text), None)
    if phrase:
        return phrase
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _infer_compound_events(
    direct_events: tuple[TimelineEvent, ...], pack: GamePack
) -> tuple[TimelineEvent, ...]:
    events = list(direct_events)
    kills = [event for event in direct_events if event.event_type == "kill"]
    for index, kill in enumerate(kills):
        recent_kills = [
            candidate
            for candidate in kills[: index + 1]
            if kill.time_seconds - candidate.time_seconds <= pack.multi_kill_window_seconds
        ]
        if len(recent_kills) < 2 or _near_event(events, "multi_kill", kill.time_seconds, 2):
            continue
        events.append(
            TimelineEvent(
                time_seconds=kill.time_seconds,
                event_type="multi_kill",
                label=f"Inferred {len(recent_kills)}-kill streak",
                importance=min(1, 0.82 + 0.06 * len(recent_kills)),
                provenance=(f"game-pack:{pack.name}:temporal-fusion",),
                evidence=(
                    f"{len(recent_kills)} kill events occurred within "
                    f"{pack.multi_kill_window_seconds:g} seconds",
                ),
            )
        )
    danger_events = [
        event for event in direct_events if event.event_type == "narrow_survival"
    ]
    payoffs = [
        event
        for event in direct_events
        if event.event_type in {"kill", "multi_kill", "victory"}
    ]
    for danger in danger_events:
        payoff = next(
            (
                event
                for event in payoffs
                if 0 <= event.time_seconds - danger.time_seconds <= pack.clutch_window_seconds
            ),
            None,
        )
        if payoff is None or _near_event(events, "clutch", payoff.time_seconds, 5):
            continue
        events.append(
            TimelineEvent(
                time_seconds=payoff.time_seconds,
                event_type="clutch",
                label="Inferred clutch after critical health",
                importance=1,
                provenance=(f"game-pack:{pack.name}:temporal-fusion",),
                evidence=(
                    "critical-health evidence was followed by a kill, multi-kill, or victory "
                    f"within {pack.clutch_window_seconds:g} seconds",
                ),
            )
        )
    return tuple(sorted(events, key=lambda event: (event.time_seconds, event.event_type)))


def _near_event(
    events: list[TimelineEvent], event_type: str, timestamp: float, tolerance: float
) -> bool:
    return any(
        event.event_type == event_type and abs(event.time_seconds - timestamp) <= tolerance
        for event in events
    )
