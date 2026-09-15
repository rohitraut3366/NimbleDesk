import json
from pathlib import Path
from typing import Literal

import pytest

from nimbledesk.creative.models import (
    EditSegment,
    Evidence,
    SoundAsset,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.creative.sound import load_sound_catalog, plan_sound_cues


def _segment(
    source: Path,
    segment_id: str,
    role: Literal["hook", "setup", "development", "payoff", "outro"],
    start: float,
) -> EditSegment:
    return EditSegment(
        segment_id=segment_id,
        role=role,
        source_path=source,
        source_range=TimeRange(start_seconds=start, end_seconds=start + 4),
        timeline_start_seconds=start,
        speed=SpeedTreatment(rationale="fixture"),
        visual=VisualTreatment(rationale="fixture"),
        score=1,
        evidence=(
            Evidence(
                analyzer="fixture",
                analyzer_version="1",
                confidence=1,
                description="grenade kill payoff",
            ),
        ),
    )


def test_sound_planner_matches_evidence_and_limits_density(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    sound = tmp_path / "impact.wav"
    sound.write_bytes(b"fixture")
    asset = SoundAsset(
        path=sound,
        duration_seconds=1,
        title="Impact",
        tags=("impact",),
        license="user-owned",
    )

    cues = plan_sound_cues(
        (
            _segment(source, "one", "hook", 0),
            _segment(source, "two", "payoff", 4),
        ),
        (asset,),
    )

    assert len(cues) == 1
    assert cues[0].segment_id == "one"
    assert cues[0].asset.license == "user-owned"


def test_sound_catalog_requires_available_licensed_assets(tmp_path: Path) -> None:
    catalog = tmp_path / "sounds.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "path": str(tmp_path / "missing.wav"),
                    "duration_seconds": 1,
                    "title": "Missing",
                    "tags": ["impact"],
                    "license": "licensed",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="unavailable"):
        load_sound_catalog(catalog)
