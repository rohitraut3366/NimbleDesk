from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.davinci import DaVinciResult, connect_to_resolve, execute_in_davinci
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.gaming import detect_game_events, load_game_pack, write_events
from nimbledesk.creative.models import CreativeBrief, EditPlan
from nimbledesk.creative.music import load_music_catalog
from nimbledesk.creative.planner import build_edit_plan, write_edit_plan
from nimbledesk.creative.render import render_edit_plan
from nimbledesk.creative.transcription import (
    load_transcript,
    transcribe_with_whisper,
    write_transcript,
)
from nimbledesk.media.models import AnalysisConfig, TimelineEvent
from nimbledesk.media.pipeline import HighlightPipeline, load_events


class CreationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_directory: Path
    plan_path: Path
    timeline_path: Path
    render_path: Path | None
    transcript_path: Path | None
    events_path: Path | None
    davinci: DaVinciResult | None = None
    plan: EditPlan


class CreationWorkflow:
    def create(
        self,
        source: Path,
        output_directory: Path,
        brief: CreativeBrief,
        *,
        supplied_events: Path | None = None,
        automatic_game_ocr: bool = False,
        game_pack: Path | None = None,
        supplied_transcript: Path | None = None,
        automatic_transcription: bool = False,
        whisper_model: str = "small",
        language: str | None = None,
        music_catalog: Path | None = None,
        render: bool = True,
        execute_davinci: bool = False,
        render_in_davinci: bool = False,
    ) -> CreationResult:
        output_directory.mkdir(parents=True, exist_ok=True)
        events = list(load_events(supplied_events))
        if automatic_game_ocr:
            events.extend(detect_game_events(source, load_game_pack(game_pack)))
        merged_events = _merge_events(tuple(events))
        events_path = output_directory / "detected_events.json" if merged_events else None
        if events_path:
            write_events(merged_events, events_path)

        transcripts = load_transcript(supplied_transcript)
        if automatic_transcription:
            transcripts = transcribe_with_whisper(source, whisper_model, language)
        transcript_path = output_directory / "transcript.json" if transcripts else None
        if transcript_path:
            write_transcript(transcripts, transcript_path)

        analysis_directory = output_directory / "analysis"
        manifest = HighlightPipeline().analyze_and_render(
            source=source,
            output_directory=analysis_directory,
            config=AnalysisConfig(
                highlight_count=brief.clip_count,
                lead_in_seconds=5,
                aftermath_seconds=10,
                minimum_peak_separation_seconds=12,
            ),
            events=merged_events,
        )
        plan = build_edit_plan(
            manifest,
            brief,
            transcripts=transcripts,
            music_assets=load_music_catalog(music_catalog),
        )
        plan_path = output_directory / "edit_plan.json"
        timeline_path = output_directory / "davinci_timeline.fcpxml"
        write_edit_plan(plan, plan_path)
        export_fcpxml(plan, timeline_path)
        render_path = output_directory / "final.mp4" if render else None
        if render_path:
            render_edit_plan(plan, render_path)
        davinci = None
        if execute_davinci:
            davinci = execute_in_davinci(
                connect_to_resolve(),
                plan,
                timeline_path,
                output_directory,
                render=render_in_davinci,
            )
        return CreationResult(
            output_directory=output_directory,
            plan_path=plan_path,
            timeline_path=timeline_path,
            render_path=render_path,
            transcript_path=transcript_path,
            events_path=events_path,
            davinci=davinci,
            plan=plan,
        )


def _merge_events(events: tuple[TimelineEvent, ...]) -> tuple[TimelineEvent, ...]:
    ordered = sorted(events, key=lambda event: event.time_seconds)
    merged: list[TimelineEvent] = []
    for event in ordered:
        duplicate = next(
            (
                existing
                for existing in merged
                if existing.event_type == event.event_type
                and abs(existing.time_seconds - event.time_seconds) < 2
            ),
            None,
        )
        if duplicate is None:
            merged.append(event)
    return tuple(merged)
