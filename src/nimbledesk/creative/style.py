from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import (
    AccessibilityRequirements,
    AspectRatio,
    AutonomyLevel,
    BrandRules,
    CreativeBrief,
    DataPolicy,
    Pace,
)


class StyleDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audience: str | None = None
    platform: str | None = None
    aspect_ratio: AspectRatio | None = None
    pace: Pace | None = None
    mood: str | None = None
    captions: bool | None = None
    music: bool | None = None
    color_look: str | None = None
    title_style: str | None = None
    transition_style: str | None = None
    music_style: tuple[str, ...] | None = None
    brand: BrandRules | None = None
    accessibility: AccessibilityRequirements | None = None
    autonomy: AutonomyLevel | None = None
    data_policy: DataPolicy | None = None


class StyleFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    created_at: float
    selected_variant_id: str
    notes: str | None = None


class StyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_version: str = "1.0.0"
    profile_id: str
    name: str
    defaults: StyleDefaults = StyleDefaults()
    feedback: tuple[StyleFeedback, ...] = ()


class StyleProfileStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path.home() / ".nimbledesk" / "style-profiles"

    def list(self) -> tuple[StyleProfile, ...]:
        if not self.directory.is_dir():
            return ()
        profiles: list[StyleProfile] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                profiles.append(StyleProfile.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return tuple(profiles)

    def load(self, profile_id: str) -> StyleProfile:
        path = self._path(profile_id)
        if not path.is_file():
            raise FileNotFoundError(f"unknown style profile: {profile_id}")
        return StyleProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, profile: StyleProfile) -> Path:
        path = self._path(profile.profile_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def record_feedback(
        self, profile_id: str, selected_variant_id: str, notes: str | None = None
    ) -> StyleProfile:
        profile = self.load(profile_id)
        feedback = (*profile.feedback, StyleFeedback(
            created_at=time.time(), selected_variant_id=selected_variant_id, notes=notes
        ))
        updated = profile.model_copy(update={"feedback": feedback[-100:]})
        self.save(updated)
        return updated

    def _path(self, profile_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile_id):
            raise ValueError(
                "profile ID must use lowercase letters, numbers, hyphens, or underscores"
            )
        return self.directory / f"{profile_id}.json"


def resolve_brief(
    brief: CreativeBrief | dict[str, Any], profile: StyleProfile | None = None
) -> CreativeBrief:
    if isinstance(brief, CreativeBrief):
        return brief
    values = dict(brief)
    if profile is not None:
        defaults = profile.defaults.model_dump(mode="python", exclude_none=True)
        values = {**defaults, **values}
    return CreativeBrief.model_validate(values)


def load_profile(path: Path | None) -> StyleProfile | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    return StyleProfile.model_validate(payload)
