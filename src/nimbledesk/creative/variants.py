from __future__ import annotations

import math
from pathlib import Path
from statistics import pstdev
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.analysis.models import ContentIndex
from nimbledesk.creative.models import (
    CreativeBrief,
    EditPlan,
    MusicAsset,
    Pace,
    SoundAsset,
    TranscriptSegment,
)
from nimbledesk.creative.planner import build_edit_plan, write_edit_plan
from nimbledesk.creative.validation import validate_edit_plan
from nimbledesk.media.models import HighlightManifest


class VariantMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    score: float = Field(ge=0, le=1)
    evidence: str


class VariantEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variant_id: str
    strategy: str
    plan_path: Path
    overall_score: float = Field(ge=0, le=1)
    metrics: tuple[VariantMetric, ...]
    tradeoff: str


class VariantComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_version: str = "1.0.0"
    disclaimer: str = (
        "Scores compare inspectable editing properties; they do not predict or guarantee "
        "audience performance."
    )
    recommended_variant_id: str
    variants: tuple[VariantEvaluation, ...]


def generate_variant_comparison(
    manifest: HighlightManifest,
    brief: CreativeBrief,
    output_directory: Path,
    transcripts: tuple[TranscriptSegment, ...] = (),
    music_assets: tuple[MusicAsset, ...] = (),
    sound_assets: tuple[SoundAsset, ...] = (),
    content_index: ContentIndex | None = None,
) -> VariantComparison:
    specifications: tuple[tuple[str, str, CreativeBrief, StoryStrategy], ...] = (
        ("strongest-hook", "Strongest moment opens the edit", brief, "strongest_hook"),
        (
            "energetic-short",
            "Faster and shorter, with less setup context",
            brief.model_copy(
                update={
                    "pace": Pace.FAST,
                    "target_duration_seconds": max(5, brief.target_duration_seconds * 0.75),
                    "clip_count": max(1, math.ceil(brief.clip_count * 0.7)),
                }
            ),
            "strongest_hook",
        ),
        (
            "context-first",
            "Chronological setup improves clarity but delays the strongest payoff",
            brief.model_copy(update={"pace": Pace.BALANCED}),
            "chronological",
        ),
    )
    evaluations: list[VariantEvaluation] = []
    for variant_id, tradeoff, variant_brief, strategy in specifications:
        plan = build_edit_plan(
            manifest,
            variant_brief,
            transcripts,
            music_assets,
            sound_assets,
            content_index,
            story_strategy=strategy,
        )
        validation = validate_edit_plan(plan, manifest.source)
        if not validation.valid:
            continue
        plan_path = output_directory / variant_id / "edit_plan.json"
        write_edit_plan(plan, plan_path)
        evaluations.append(_evaluate(variant_id, strategy, plan_path, plan, tradeoff))
    if not evaluations:
        raise ValueError("no valid watchability variants could be generated")
    recommended = max(evaluations, key=lambda item: item.overall_score)
    comparison = VariantComparison(
        recommended_variant_id=recommended.variant_id,
        variants=tuple(evaluations),
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "variant_comparison.json").write_text(
        comparison.model_dump_json(indent=2), encoding="utf-8"
    )
    return comparison


StoryStrategy = Literal["strongest_hook", "chronological"]


def _evaluate(
    variant_id: str,
    strategy: StoryStrategy,
    plan_path: Path,
    plan: EditPlan,
    tradeoff: str,
) -> VariantEvaluation:
    duration = max(0.001, plan.duration_seconds)
    first_payoff = next(
        (segment.timeline_start_seconds for segment in plan.segments if segment.role == "payoff"),
        duration,
    )
    line_lengths = [
        len(line) for caption in plan.captions for line in caption.text.splitlines()
    ]
    caption_durations = [caption.timeline_range.duration_seconds for caption in plan.captions]
    caption_characters = [len(caption.text.replace("\n", " ")) for caption in plan.captions]
    readable_captions = all(length <= 42 for length in line_lengths) and all(
        characters / max(0.1, seconds) <= 22
        for characters, seconds in zip(caption_characters, caption_durations, strict=True)
    )
    evidence_labels = {
        evidence.description.casefold()
        for segment in plan.segments
        for evidence in segment.evidence
    }
    roles = {segment.role for segment in plan.segments}
    speeds = [segment.speed.rate for segment in plan.segments]
    metrics = (
        VariantMetric(
            name="hook_clarity",
            score=min(1, plan.segments[0].score) if plan.segments else 0,
            evidence="opening segment selection confidence",
        ),
        VariantMetric(
            name="time_to_first_payoff",
            score=max(0, 1 - first_payoff / duration),
            evidence=f"first payoff begins at {first_payoff:.2f}s of {duration:.2f}s",
        ),
        VariantMetric(
            name="information_density",
            score=min(1, len(plan.segments) / max(1, duration / 8)),
            evidence=f"{len(plan.segments)} selected moments across {duration:.2f}s",
        ),
        VariantMetric(
            name="pacing_variation",
            score=min(1, 0.45 + pstdev(speeds) * 2) if len(speeds) > 1 else 0.35,
            evidence=f"playback rates: {', '.join(f'{speed:.2f}x' for speed in speeds)}",
        ),
        VariantMetric(
            name="narrative_completeness",
            score=min(1, len(roles & {"hook", "setup", "payoff", "outro"}) / 4),
            evidence="roles present: " + ", ".join(sorted(roles)),
        ),
        VariantMetric(
            name="novelty",
            score=min(1, len(evidence_labels) / max(1, len(plan.segments))),
            evidence=f"{len(evidence_labels)} distinct evidence descriptions",
        ),
        VariantMetric(
            name="caption_readability",
            score=1 if not plan.captions or readable_captions else 0.35,
            evidence="42 characters per line and at most 22 characters per second",
        ),
        VariantMetric(
            name="audio_intelligibility",
            score=1
            if not plan.all_music_cues
            or all(cue.duck_under_dialogue_db <= -6 for cue in plan.all_music_cues)
            else 0.5,
            evidence="music ducking and delivery loudness plan",
        ),
        VariantMetric(
            name="platform_fit",
            score=1,
            evidence=(
                f"{plan.delivery.width}x{plan.delivery.height} for {plan.brief.platform} "
                f"{plan.brief.aspect_ratio.value}"
            ),
        ),
    )
    weights = {
        "hook_clarity": 0.16,
        "time_to_first_payoff": 0.14,
        "information_density": 0.1,
        "pacing_variation": 0.08,
        "narrative_completeness": 0.16,
        "novelty": 0.1,
        "caption_readability": 0.1,
        "audio_intelligibility": 0.08,
        "platform_fit": 0.08,
    }
    overall = sum(metric.score * weights[metric.name] for metric in metrics)
    return VariantEvaluation(
        variant_id=variant_id,
        strategy=strategy,
        plan_path=plan_path,
        overall_score=round(overall, 4),
        metrics=metrics,
        tradeoff=tradeoff,
    )
