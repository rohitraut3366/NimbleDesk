from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nimbledesk.analysis.index import ContentIndexer
from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.cue_sheet import write_cue_sheet
from nimbledesk.creative.davinci import DaVinciResult, execute_davinci_isolated
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.gaming import detect_game_events, load_game_pack, write_events
from nimbledesk.creative.models import (
    AutonomyLevel,
    ContentKind,
    CreativeBrief,
    EditPlan,
    TranscriptSegment,
)
from nimbledesk.creative.music import load_music_catalog
from nimbledesk.creative.planner import build_edit_plan, write_edit_plan
from nimbledesk.creative.render import render_edit_plan
from nimbledesk.creative.sound import load_sound_catalog
from nimbledesk.creative.transcription import (
    load_transcript,
    transcribe_with_whisper,
    write_transcript,
)
from nimbledesk.creative.validation import (
    PlanValidationError,
    validate_edit_plan,
    write_validation_report,
)
from nimbledesk.creative.variants import generate_variant_comparison
from nimbledesk.creative.verify import RenderVerificationError, verify_render
from nimbledesk.creative.vision import VisionProviderConfig, analyze_with_vision_provider
from nimbledesk.media.models import AnalysisConfig, TimelineEvent
from nimbledesk.media.pipeline import HighlightPipeline, load_events


class CreationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_directory: Path
    content_index_path: Path
    plan_path: Path
    validation_path: Path
    timeline_path: Path
    render_path: Path | None
    transcript_path: Path | None
    events_path: Path | None
    vision_analysis_path: Path | None
    davinci: DaVinciResult | None = None
    plan: EditPlan
    cue_sheet_path: Path | None = None
    cue_sheet_csv_path: Path | None = None
    variant_comparison_path: Path | None = None
    verification_path: Path | None = None
    davinci_verification_path: Path | None = None


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
        vision_provider: Path | None = None,
        supplied_transcript: Path | None = None,
        automatic_transcription: bool = False,
        whisper_model: str = "small",
        language: str | None = None,
        music_catalog: Path | None = None,
        sound_catalog: Path | None = None,
        render: bool = True,
        execute_davinci: bool = False,
        render_in_davinci: bool = False,
        progress: Callable[[str, float], None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CreationResult:
        report = progress or (lambda _stage, _progress: None)
        token = cancellation or CancellationToken()
        if render and brief.autonomy is AutonomyLevel.PLAN_ONLY:
            raise ValueError("creative brief autonomy allows planning only")
        if render and brief.autonomy is AutonomyLevel.REVIEW_BEFORE_RENDER:
            raise ValueError("creative brief requires plan approval before rendering")
        if execute_davinci and brief.autonomy is not AutonomyLevel.EXECUTE_EDITOR:
            raise ValueError("creative brief autonomy does not allow editor execution")
        token.check()
        output_directory.mkdir(parents=True, exist_ok=True)
        report("detecting events", 0.05)
        events = list(load_events(supplied_events))
        if automatic_game_ocr:
            events.extend(
                detect_game_events(
                    source, load_game_pack(game_pack), cancelled=token.is_cancelled
                )
            )
        vision_analysis_path = None
        if vision_provider:
            vision_config = VisionProviderConfig.model_validate_json(
                vision_provider.read_text(encoding="utf-8")
            )
            if (
                vision_config.execution_location == "remote"
                and not brief.data_policy.allow_remote_frames
            ):
                raise ValueError(
                    "creative brief data policy does not allow frames to leave the laptop"
                )
            report("analyzing semantic vision", 0.1)
            vision_directory = output_directory / "analysis" / "vision"
            vision = analyze_with_vision_provider(
                source,
                vision_directory,
                vision_provider,
                brief.content_kind.value,
                cancelled=token.is_cancelled,
            )
            events.extend(vision.timeline_events())
            vision_analysis_path = vision_directory / "analysis.json"
        merged_events = _merge_events(tuple(events))

        report("transcribing dialogue", 0.15)
        transcripts = load_transcript(supplied_transcript)
        if automatic_transcription:
            transcripts = transcribe_with_whisper(
                source, whisper_model, language, cancelled=token.is_cancelled
            )
        transcript_path = output_directory / "transcript.json" if transcripts else None
        if transcript_path:
            write_transcript(transcripts, transcript_path)

        index_directory = output_directory / "analysis" / "index"
        content_index = ContentIndexer().build(
            source,
            index_directory,
            transcripts=transcripts,
            events=merged_events,
            progress=lambda stage, value: report(stage, 0.2 + value * 0.3),
            cancelled=token.is_cancelled,
        )
        content_index_path = index_directory / "content_index.json"
        merged_events = _apply_event_constraints(content_index.semantic_events, brief)
        brief = _bind_event_moments(
            brief, merged_events, content_index.asset.metadata.duration_seconds
        )
        events_path = output_directory / "detected_events.json" if merged_events else None
        if events_path:
            write_events(merged_events, events_path)
        brief = _resolve_content_kind(brief, merged_events, transcripts)
        report("ranking and refining highlights", 0.52)
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
            cancelled=token.is_cancelled,
        )
        report("building creative edit plan", 0.65)
        music_assets = load_music_catalog(music_catalog)
        sound_assets = load_sound_catalog(sound_catalog)
        plan = build_edit_plan(
            manifest,
            brief,
            transcripts=transcripts,
            music_assets=music_assets,
            sound_assets=sound_assets,
            content_index=content_index,
        )
        plan_path = output_directory / "edit_plan.json"
        validation_path = output_directory / "validation.json"
        timeline_path = output_directory / "davinci_timeline.fcpxml"
        write_edit_plan(plan, plan_path)
        validation = validate_edit_plan(plan, content_index.asset.metadata)
        write_validation_report(validation, validation_path)
        if not validation.valid:
            raise PlanValidationError(validation)
        export_fcpxml(plan, timeline_path)
        cue_sheet_path, cue_sheet_csv_path = write_cue_sheet(plan, output_directory)
        report("comparing watchability variants", 0.72)
        variants_directory = output_directory / "variants"
        generate_variant_comparison(
            manifest,
            brief,
            variants_directory,
            transcripts,
            music_assets,
            sound_assets,
            content_index,
        )
        variant_comparison_path = variants_directory / "variant_comparison.json"
        token.check()
        report("rendering review video", 0.75)
        render_path = output_directory / "final.mp4" if render else None
        verification_path = None
        if render_path:
            render_edit_plan(plan, render_path, cancelled=token.is_cancelled)
            verification_path = output_directory / "render_verification.json"
            verification = verify_render(
                plan,
                render_path,
                verification_path,
                cancelled=token.is_cancelled,
            )
            if not verification.valid:
                raise RenderVerificationError(verification)
        davinci = None
        davinci_verification_path = None
        if execute_davinci:
            report("executing in DaVinci Resolve", 0.9)
            davinci = execute_davinci_isolated(
                plan_path,
                timeline_path,
                output_directory,
                render=render_in_davinci,
                cancelled=token.is_cancelled,
            )
            if davinci.render_path:
                davinci_verification_path = output_directory / "davinci_render_verification.json"
                davinci_verification = verify_render(
                    plan,
                    davinci.render_path,
                    davinci_verification_path,
                    cancelled=token.is_cancelled,
                )
                if not davinci_verification.valid:
                    raise RenderVerificationError(davinci_verification)
        report("completed", 1)
        return CreationResult(
            output_directory=output_directory,
            content_index_path=content_index_path,
            plan_path=plan_path,
            validation_path=validation_path,
            timeline_path=timeline_path,
            render_path=render_path,
            transcript_path=transcript_path,
            events_path=events_path,
            vision_analysis_path=vision_analysis_path,
            davinci=davinci,
            plan=plan,
            cue_sheet_path=cue_sheet_path,
            cue_sheet_csv_path=cue_sheet_csv_path,
            variant_comparison_path=variant_comparison_path,
            verification_path=verification_path,
            davinci_verification_path=davinci_verification_path,
        )


def _merge_events(events: tuple[TimelineEvent, ...]) -> tuple[TimelineEvent, ...]:
    ordered = sorted(events, key=lambda event: event.time_seconds)
    merged: list[TimelineEvent] = []
    for event in ordered:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(merged)
                if existing.event_type == event.event_type
                and abs(existing.time_seconds - event.time_seconds) < 2
            ),
            None,
        )
        if duplicate_index is None:
            merged.append(event)
            continue
        duplicate = merged[duplicate_index]
        total_weight = duplicate.importance + event.importance
        fused_time = (
            duplicate.time_seconds * duplicate.importance
            + event.time_seconds * event.importance
        ) / max(total_weight, 0.0001)
        fused_confidence = 1 - (1 - duplicate.importance) * (1 - event.importance)
        labels = [label for label in (duplicate.label, event.label) if label]
        merged[duplicate_index] = TimelineEvent(
            time_seconds=round(fused_time, 3),
            event_type=event.event_type,
            label=max(labels, key=len) if labels else None,
            importance=round(fused_confidence, 4),
            provenance=tuple(dict.fromkeys((*duplicate.provenance, *event.provenance))),
            evidence=tuple(dict.fromkeys((*duplicate.evidence, *event.evidence))),
        )
    return tuple(merged)


def _apply_event_constraints(
    events: tuple[TimelineEvent, ...], brief: CreativeBrief
) -> tuple[TimelineEvent, ...]:
    event_types = {event.event_type for event in events}
    missing = set(brief.mandatory_event_types) - event_types
    if missing:
        raise ValueError("mandatory event types were not detected: " + ", ".join(sorted(missing)))
    excluded = set(brief.excluded_event_types)
    return tuple(event for event in events if event.event_type not in excluded)


def _bind_event_moments(
    brief: CreativeBrief,
    events: tuple[TimelineEvent, ...],
    source_duration_seconds: float,
) -> CreativeBrief:
    from nimbledesk.creative.models import BriefMoment

    moments = list(brief.mandatory_moments)
    already_bound = {moment.event_type for moment in moments if moment.event_type}
    for event_type in brief.mandatory_event_types:
        if event_type in already_bound:
            continue
        matching = [event for event in events if event.event_type == event_type]
        if not matching:
            continue
        event = max(matching, key=lambda item: item.importance)
        moments.append(
            BriefMoment(
                label=event.label or event.event_type,
                event_type=event.event_type,
                start_seconds=max(0, event.time_seconds - 1),
                end_seconds=min(source_duration_seconds, event.time_seconds + 1),
            )
        )
    return brief.model_copy(update={"mandatory_moments": tuple(moments)})


def _resolve_content_kind(
    brief: CreativeBrief,
    events: tuple[TimelineEvent, ...],
    transcripts: tuple[TranscriptSegment, ...],
) -> CreativeBrief:
    if brief.content_kind is not ContentKind.AUTO:
        return brief
    gameplay_event_types = {
        "kill",
        "multi_kill",
        "grenade_kill",
        "clutch",
        "narrow_survival",
        "victory",
        "death",
        "round_win",
    }
    if any(event.event_type in gameplay_event_types for event in events):
        detected = ContentKind.GAMEPLAY
    elif transcripts:
        detected = ContentKind.TALKING_HEAD
    else:
        detected = ContentKind.VLOG
    return brief.model_copy(update={"content_kind": detected})
