from __future__ import annotations

import argparse
import json
from pathlib import Path

from nimbledesk.creative.models import AspectRatio, AutonomyLevel, ContentKind, Pace
from nimbledesk.creative.style import load_profile, resolve_brief
from nimbledesk.creative.workflow import CreationWorkflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Understand source footage, create an edit plan, and render a finished video"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--brief", type=Path, help="Complete CreativeBrief JSON file")
    parser.add_argument("--style-profile", type=Path, help="Reusable StyleProfile JSON file")
    parser.add_argument("--title")
    parser.add_argument(
        "--content-kind", choices=[item.value for item in ContentKind]
    )
    parser.add_argument("--platform")
    parser.add_argument("--duration", type=float)
    parser.add_argument(
        "--aspect-ratio", choices=[item.value for item in AspectRatio]
    )
    parser.add_argument("--pace", choices=[item.value for item in Pace])
    parser.add_argument("--mood")
    parser.add_argument("--clip-count", type=int)
    parser.add_argument("--color-look")
    parser.add_argument("--autonomy", choices=[item.value for item in AutonomyLevel])
    parser.add_argument("--no-captions", action="store_true")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument("--events", type=Path, help="Supplied event timeline JSON")
    parser.add_argument(
        "--automatic",
        action="store_true",
        help="Discover available OCR, transcription, semantic vision, music, and sound inputs",
    )
    parser.add_argument(
        "--game-ocr", action="store_true", help="Detect game events with Tesseract OCR"
    )
    parser.add_argument("--game-pack", type=Path, help="Custom game OCR pack JSON")
    parser.add_argument(
        "--vision-provider",
        type=Path,
        help="Semantic vision provider configuration JSON",
    )
    parser.add_argument("--transcript", type=Path, help="Supplied transcript segment JSON")
    parser.add_argument(
        "--transcribe", action="store_true", help="Transcribe with the local Whisper CLI"
    )
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--language")
    parser.add_argument("--music-catalog", type=Path, help="Licensed local music catalog JSON")
    parser.add_argument("--sound-catalog", type=Path, help="Licensed local sound catalog JSON")
    parser.add_argument("--plan-only", action="store_true", help="Skip the final FFmpeg render")
    parser.add_argument(
        "--davinci",
        action="store_true",
        help="Import the generated timeline into a running DaVinci Resolve instance",
    )
    parser.add_argument(
        "--davinci-render",
        action="store_true",
        help="Import and render the timeline in DaVinci Resolve",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    profile = load_profile(arguments.style_profile)
    if arguments.brief:
        raw_brief = json.loads(arguments.brief.read_text(encoding="utf-8"))
        if not isinstance(raw_brief, dict):
            raise ValueError("creative brief must contain a JSON object")
    else:
        raw_brief = {
            "title": arguments.title,
            "content_kind": arguments.content_kind,
            "platform": arguments.platform,
            "target_duration_seconds": arguments.duration,
            "aspect_ratio": arguments.aspect_ratio,
            "pace": arguments.pace,
            "mood": arguments.mood,
            "clip_count": arguments.clip_count,
            "color_look": arguments.color_look,
            "autonomy": arguments.autonomy,
        }
        raw_brief = {key: value for key, value in raw_brief.items() if value is not None}
        if arguments.no_captions:
            raw_brief["captions"] = False
        if arguments.no_music:
            raw_brief["music"] = False
    if (arguments.davinci or arguments.davinci_render) and "autonomy" not in raw_brief:
        raw_brief["autonomy"] = AutonomyLevel.EXECUTE_EDITOR.value
    brief = resolve_brief(raw_brief, profile)
    result = CreationWorkflow().create(
        arguments.source,
        arguments.output_directory,
        brief,
        supplied_events=arguments.events,
        automatic_intelligence=arguments.automatic,
        automatic_game_ocr=arguments.game_ocr,
        game_pack=arguments.game_pack,
        vision_provider=arguments.vision_provider,
        supplied_transcript=arguments.transcript,
        automatic_transcription=arguments.transcribe,
        whisper_model=arguments.whisper_model,
        language=arguments.language,
        music_catalog=arguments.music_catalog,
        sound_catalog=arguments.sound_catalog,
        render=not arguments.plan_only,
        execute_davinci=arguments.davinci or arguments.davinci_render,
        render_in_davinci=arguments.davinci_render,
    )
    print(
        json.dumps(
            {
                "plan": str(result.plan_path),
                "validation": str(result.validation_path),
                "content_index": str(result.content_index_path),
                "davinci_timeline": str(result.timeline_path),
                "render": str(result.render_path) if result.render_path else None,
                "transcript": str(result.transcript_path) if result.transcript_path else None,
                "events": str(result.events_path) if result.events_path else None,
                "vision_analysis": (
                    str(result.vision_analysis_path) if result.vision_analysis_path else None
                ),
                "automatic_intelligence": str(result.automatic_intelligence_path),
                "cue_sheet": str(result.cue_sheet_path) if result.cue_sheet_path else None,
                "cue_sheet_csv": (
                    str(result.cue_sheet_csv_path) if result.cue_sheet_csv_path else None
                ),
                "variant_comparison": (
                    str(result.variant_comparison_path)
                    if result.variant_comparison_path
                    else None
                ),
                "verification": (
                    str(result.verification_path) if result.verification_path else None
                ),
                "davinci_verification": (
                    str(result.davinci_verification_path)
                    if result.davinci_verification_path
                    else None
                ),
                "duration_seconds": result.plan.duration_seconds,
                "review_items": [item.model_dump() for item in result.plan.review_items],
                "davinci": result.davinci.model_dump(mode="json") if result.davinci else None,
            },
            indent=2,
        )
    )
