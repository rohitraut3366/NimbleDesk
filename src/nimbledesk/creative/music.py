from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nimbledesk.creative.models import CreativeBrief, MusicAsset


def load_music_catalog(path: Path | None) -> tuple[MusicAsset, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("music catalog must contain a JSON list")
    assets = tuple(_resolve_asset(MusicAsset.model_validate(item), path.parent) for item in payload)
    missing = [str(asset.path) for asset in assets if not asset.path.is_file()]
    if missing:
        raise FileNotFoundError("music files do not exist: " + ", ".join(missing))
    return assets


def _resolve_asset(asset: MusicAsset, catalog_directory: Path) -> MusicAsset:
    asset_path = asset.path if asset.path.is_absolute() else catalog_directory / asset.path
    return asset.model_copy(update={"path": asset_path.expanduser().resolve()})


def recommend_music(
    brief: CreativeBrief,
    assets: tuple[MusicAsset, ...],
    required_duration_seconds: float,
) -> MusicAsset | None:
    if not brief.music or not assets:
        return None
    eligible = tuple(
        asset
        for asset in assets
        if asset.license.strip()
        and (not asset.allowed_platforms or brief.platform.casefold() in {
            platform.casefold() for platform in asset.allowed_platforms
        })
        and (asset.license_expires is None or asset.license_expires >= date.today())
    )
    if not eligible:
        return None
    desired_energy = {"calm": 0.3, "balanced": 0.6, "fast": 0.85}[brief.pace.value]
    mood_terms = {term.casefold() for term in brief.mood.replace(",", " ").split()}

    def score(asset: MusicAsset) -> float:
        asset_moods = {mood.casefold() for mood in asset.mood}
        mood_score = len(mood_terms & asset_moods) / max(1, len(mood_terms))
        energy_score = 1 - abs(asset.energy - desired_energy)
        duration_score = min(1.0, asset.duration_seconds / max(1, required_duration_seconds))
        instrumental_score = 1.0 if asset.instrumental else 0.5
        return (
            0.4 * mood_score
            + 0.3 * energy_score
            + 0.2 * duration_score
            + 0.1 * instrumental_score
        )

    return max(eligible, key=score)
