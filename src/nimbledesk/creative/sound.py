from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from nimbledesk.creative.models import EditSegment, SoundAsset, SoundCue, TimeRange


def load_sound_catalog(path: Path | None) -> tuple[SoundAsset, ...]:
    if path is None:
        return ()
    assets = TypeAdapter(tuple[SoundAsset, ...]).validate_python(
        json.loads(path.expanduser().read_text(encoding="utf-8"))
    )
    missing = [str(asset.path) for asset in assets if not asset.path.expanduser().is_file()]
    if missing:
        raise FileNotFoundError("sound assets are unavailable: " + ", ".join(missing))
    if any(not asset.license.strip() for asset in assets):
        raise ValueError("every sound asset requires license provenance")
    return assets


def plan_sound_cues(
    segments: tuple[EditSegment, ...], assets: tuple[SoundAsset, ...]
) -> tuple[SoundCue, ...]:
    if not assets:
        return ()
    cues: list[SoundCue] = []
    used_assets: set[Path] = set()
    last_cue_time = -10.0
    for segment in segments:
        if segment.timeline_start_seconds - last_cue_time < 2:
            continue
        desired_tags = _desired_tags(segment)
        match = next(
            (
                asset
                for asset in assets
                if asset.path not in used_assets
                and desired_tags & {tag.casefold() for tag in asset.tags}
            ),
            None,
        )
        if match is None:
            continue
        duration = min(match.duration_seconds, 2, segment.timeline_duration_seconds)
        cue_start = segment.timeline_start_seconds
        if segment.role == "payoff":
            cue_start += min(1, max(0, segment.timeline_duration_seconds - duration))
        cues.append(
            SoundCue(
                asset=match,
                source_range=TimeRange(start_seconds=0, end_seconds=duration),
                timeline_range=TimeRange(
                    start_seconds=round(cue_start, 3),
                    end_seconds=round(cue_start + duration, 3),
                ),
                segment_id=segment.segment_id,
                purpose="accent " + segment.role,
                rationale=(
                    f"licensed {match.title} matches {', '.join(sorted(desired_tags))} "
                    "evidence; one cue per segment with a two-second density limit"
                ),
            )
        )
        used_assets.add(match.path)
        last_cue_time = cue_start
    return tuple(cues)


def _desired_tags(segment: EditSegment) -> set[str]:
    evidence = " ".join(item.description for item in segment.evidence).casefold()
    tags: set[str] = {segment.role}
    if any(term in evidence for term in ("grenade", "explosion")):
        tags.update({"explosion", "impact"})
    if any(term in evidence for term in ("kill", "clutch", "payoff")):
        tags.update({"impact", "hit"})
    if segment.role == "hook":
        tags.update({"whoosh", "riser"})
    if segment.role == "outro":
        tags.update({"outro", "resolve"})
    return tags
