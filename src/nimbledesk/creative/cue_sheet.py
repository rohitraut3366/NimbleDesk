from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import EditPlan, TimeRange


class CueSheetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cue_type: Literal["music", "sound_effect"]
    title: str
    artist: str | None
    composer: str | None
    source_path: Path
    source_range: TimeRange
    timeline_range: TimeRange
    license: str
    attribution: str | None
    allowed_platforms: tuple[str, ...]
    allowed_territories: tuple[str, ...]
    license_expires: str | None
    generated: bool
    generation_provider: str | None
    generation_model: str | None
    generation_prompt: str | None
    generation_seed: int | None
    purpose: str


class CueSheet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cue_sheet_version: str = "1.0.0"
    project_title: str
    platform: str
    entries: tuple[CueSheetEntry, ...]


def build_cue_sheet(plan: EditPlan) -> CueSheet:
    entries: list[CueSheetEntry] = []
    for cue in plan.all_music_cues:
        asset = cue.asset
        entries.append(
            CueSheetEntry(
                cue_type="music",
                title=asset.title,
                artist=asset.artist,
                composer=asset.composer,
                source_path=asset.path,
                source_range=cue.source_range,
                timeline_range=cue.timeline_range,
                license=asset.license,
                attribution=asset.attribution,
                allowed_platforms=asset.allowed_platforms,
                allowed_territories=asset.allowed_territories,
                license_expires=asset.license_expires.isoformat()
                if asset.license_expires
                else None,
                generated=asset.generated,
                generation_provider=asset.generation_provider,
                generation_model=asset.generation_model,
                generation_prompt=asset.generation_prompt,
                generation_seed=asset.generation_seed,
                purpose=cue.rationale,
            )
        )
    for sound_cue in plan.sound_cues:
        sound_asset = sound_cue.asset
        entries.append(
            CueSheetEntry(
                cue_type="sound_effect",
                title=sound_asset.title,
                artist=sound_asset.artist,
                composer=None,
                source_path=sound_asset.path,
                source_range=sound_cue.source_range,
                timeline_range=sound_cue.timeline_range,
                license=sound_asset.license,
                attribution=sound_asset.attribution,
                allowed_platforms=sound_asset.allowed_platforms,
                allowed_territories=sound_asset.allowed_territories,
                license_expires=sound_asset.license_expires.isoformat()
                if sound_asset.license_expires
                else None,
                generated=sound_asset.generated,
                generation_provider=sound_asset.generation_provider,
                generation_model=sound_asset.generation_model,
                generation_prompt=sound_asset.generation_prompt,
                generation_seed=sound_asset.generation_seed,
                purpose=sound_cue.purpose,
            )
        )
    return CueSheet(
        project_title=plan.brief.title,
        platform=plan.brief.platform,
        entries=tuple(entries),
    )


def write_cue_sheet(plan: EditPlan, output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    sheet = build_cue_sheet(plan)
    json_path = output_directory / "cue_sheet.json"
    csv_path = output_directory / "cue_sheet.csv"
    json_path.write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    columns = tuple(CueSheetEntry.model_fields)
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for entry in sheet.entries:
            row = entry.model_dump(mode="json")
            row["source_range"] = json.dumps(row["source_range"], separators=(",", ":"))
            row["timeline_range"] = json.dumps(row["timeline_range"], separators=(",", ":"))
            row["allowed_platforms"] = ", ".join(entry.allowed_platforms)
            row["allowed_territories"] = ", ".join(entry.allowed_territories)
            writer.writerow(row)
    return json_path, csv_path
