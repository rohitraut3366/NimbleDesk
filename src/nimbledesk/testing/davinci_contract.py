from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.davinci import DaVinciResult, execute_davinci_isolated
from nimbledesk.creative.fcpxml import export_fcpxml
from nimbledesk.creative.models import EditPlan
from nimbledesk.creative.planner import write_edit_plan
from nimbledesk.creative.verify import verify_render
from nimbledesk.media.process import ProcessCancelled


class DaVinciContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DaVinciContractCase(DaVinciContractModel):
    name: str
    passed: bool
    evidence: dict[str, Any]
    error: str | None = None


class DaVinciContractReport(DaVinciContractModel):
    report_version: str = "1.0.0"
    started_at: float
    completed_at: float
    platform: str
    platform_release: str
    resolve_version: str | None
    qualification_project: str
    cases: tuple[DaVinciContractCase, ...]
    passed: bool


def run_davinci_contract(
    source_plan_path: Path,
    output_directory: Path,
    timeout_seconds: float = 3_600,
) -> DaVinciContractReport:
    if timeout_seconds <= 0:
        raise ValueError("DaVinci contract timeout must be positive")
    started_at = time.time()
    source_plan = EditPlan.model_validate_json(source_plan_path.read_text(encoding="utf-8"))
    run_id = uuid4().hex[:8]
    project_name = f"{source_plan.brief.title} [NimbleDesk Qualification {run_id}]"
    plan = source_plan.model_copy(
        update={"brief": source_plan.brief.model_copy(update={"title": project_name})}
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    plan_path = output_directory / "qualification-plan.json"
    timeline_path = output_directory / "qualification-timeline.fcpxml"
    write_edit_plan(plan, plan_path)
    export_fcpxml(plan, timeline_path)
    cases: list[DaVinciContractCase] = []
    resolve_version: str | None = None
    try:
        imported = execute_davinci_isolated(
            plan_path,
            timeline_path,
            output_directory,
            render=False,
            timeout_seconds=timeout_seconds,
        )
        _require_saved(imported)
        resolve_version = imported.resolve_version
        if imported.timeline_reused:
            raise RuntimeError("new qualification plan unexpectedly reused an existing timeline")
        cases.append(_result_case("initial_import_and_save", imported))

        rendered = execute_davinci_isolated(
            plan_path,
            timeline_path,
            output_directory,
            render=True,
            timeout_seconds=timeout_seconds,
        )
        _require_saved(rendered)
        if not rendered.timeline_reused:
            raise RuntimeError("identical plan did not reuse its existing DaVinci timeline")
        if rendered.render_path is None:
            raise RuntimeError("DaVinci did not return a render path")
        verification_path = output_directory / "davinci-contract-verification.json"
        verification = verify_render(plan, rendered.render_path, verification_path)
        if not verification.valid:
            raise RuntimeError("DaVinci render failed output verification")
        cases.append(
            DaVinciContractCase(
                name="idempotent_render_and_verify",
                passed=True,
                evidence={
                    **_result_evidence(rendered),
                    "verification_path": str(verification_path),
                },
            )
        )

        revision = _color_revision(plan)
        revision_plan_path = output_directory / "qualification-revision-plan.json"
        revision_timeline_path = output_directory / "qualification-revision-timeline.fcpxml"
        write_edit_plan(revision, revision_plan_path)
        export_fcpxml(revision, revision_timeline_path)
        revised = execute_davinci_isolated(
            revision_plan_path,
            revision_timeline_path,
            output_directory,
            render=False,
            timeout_seconds=timeout_seconds,
        )
        _require_saved(revised)
        if revised.timeline_reused or revised.plan_fingerprint == imported.plan_fingerprint:
            raise RuntimeError("changed plan did not create a new versioned DaVinci timeline")
        cases.append(_result_case("revision_creates_new_timeline", revised))

        try:
            execute_davinci_isolated(
                revision_plan_path,
                revision_timeline_path,
                output_directory,
                render=True,
                timeout_seconds=timeout_seconds,
                cancelled=lambda: True,
            )
        except ProcessCancelled as error:
            cases.append(
                DaVinciContractCase(
                    name="render_cancellation",
                    passed=True,
                    evidence={"message": str(error)},
                )
            )
        else:
            raise RuntimeError("DaVinci render completed without observing immediate cancellation")
    except Exception as error:
        cases.append(
            DaVinciContractCase(
                name="contract_failure",
                passed=False,
                evidence={},
                error=f"{type(error).__name__}: {error}",
            )
        )
    return DaVinciContractReport(
        started_at=started_at,
        completed_at=time.time(),
        platform=platform.system(),
        platform_release=platform.platform(),
        resolve_version=resolve_version,
        qualification_project=project_name,
        cases=tuple(cases),
        passed=len(cases) == 4 and all(case.passed for case in cases),
    )


def _color_revision(plan: EditPlan) -> EditPlan:
    if not plan.segments:
        raise ValueError("DaVinci qualification requires at least one edit segment")
    segment = plan.segments[0]
    saturation = segment.visual.saturation_multiplier
    revised_saturation = saturation - 0.05 if saturation >= 1.5 else saturation + 0.05
    revised_visual = segment.visual.model_copy(
        update={
            "saturation_multiplier": round(revised_saturation, 3),
            "rationale": segment.visual.rationale + "; physical qualification revision",
        }
    )
    revised_segment = segment.model_copy(update={"visual": revised_visual})
    return plan.model_copy(update={"segments": (revised_segment, *plan.segments[1:])})


def _require_saved(result: DaVinciResult) -> None:
    if not result.project_saved:
        raise RuntimeError("DaVinci did not confirm that the project was saved")


def _result_case(name: str, result: DaVinciResult) -> DaVinciContractCase:
    return DaVinciContractCase(name=name, passed=True, evidence=_result_evidence(result))


def _result_evidence(result: DaVinciResult) -> dict[str, Any]:
    return {
        "project_name": result.project_name,
        "timeline_name": result.timeline_name,
        "plan_fingerprint": result.plan_fingerprint,
        "timeline_reused": result.timeline_reused,
        "project_saved": result.project_saved,
        "resolve_version": result.resolve_version,
        "render_job_id": result.render_job_id,
        "render_path": str(result.render_path) if result.render_path else None,
    }


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the physical DaVinci Resolve contract")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=3_600)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    parsed = parse_args(arguments)
    report = run_davinci_contract(parsed.plan, parsed.output, parsed.timeout_seconds)
    report_path = parsed.output / "davinci-contract.json"
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "passed": report.passed}))
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
