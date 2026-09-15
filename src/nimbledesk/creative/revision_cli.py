from __future__ import annotations

import argparse
import json
from pathlib import Path

from nimbledesk.creative.davinci import execute_davinci_isolated
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.models import EditPlan, Pace, PlanRevisionRequest
from nimbledesk.creative.render import render_edit_plan
from nimbledesk.creative.revision import revise_edit_plan, write_revision
from nimbledesk.creative.validation import (
    PlanValidationError,
    validate_edit_plan,
    write_validation_report,
)
from nimbledesk.media.ffmpeg import probe_media


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Revise an edit plan while preserving locked decisions"
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--pace", choices=[item.value for item in Pace])
    parser.add_argument("--color-look")
    parser.add_argument("--lock", action="append", default=[])
    parser.add_argument("--unlock", action="append", default=[])
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--davinci", action="store_true")
    parser.add_argument("--davinci-render", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    plan = EditPlan.model_validate_json(arguments.plan.read_text(encoding="utf-8"))
    request = PlanRevisionRequest(
        target_duration_seconds=arguments.duration,
        pace=arguments.pace,
        color_look=arguments.color_look,
        lock_segment_ids=tuple(arguments.lock),
        unlock_segment_ids=tuple(arguments.unlock),
    )
    revision = revise_edit_plan(plan, request)
    output = arguments.output_directory
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "edit_plan.json"
    diff_path = output / "plan_diff.json"
    timeline_path = output / "davinci_timeline.fcpxml"
    validation_path = output / "validation.json"
    write_revision(revision, plan_path, diff_path)
    validation = validate_edit_plan(revision.plan, probe_media(revision.plan.source_path))
    write_validation_report(validation, validation_path)
    if not validation.valid:
        raise PlanValidationError(validation)
    export_fcpxml(revision.plan, timeline_path)
    render_path = output / "final.mp4" if arguments.render else None
    if render_path:
        render_edit_plan(revision.plan, render_path)
    davinci = None
    if arguments.davinci or arguments.davinci_render:
        davinci = execute_davinci_isolated(
            plan_path,
            timeline_path,
            output,
            render=arguments.davinci_render,
        )
    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "diff": str(diff_path),
                "validation": str(validation_path),
                "timeline": str(timeline_path),
                "render": str(render_path) if render_path else None,
                "davinci": davinci.model_dump(mode="json") if davinci else None,
                "changes": len(revision.changes),
            },
            indent=2,
        )
    )
