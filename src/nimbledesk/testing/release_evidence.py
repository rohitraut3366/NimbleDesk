from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.testing.davinci_contract import DaVinciContractReport
from nimbledesk.testing.fixture_contract import FixtureContractReport
from nimbledesk.testing.qualification import (
    EnduranceReport,
    EventBenchmarkReport,
    RankingBenchmarkReport,
)

FIXTURE_TARGETS = (
    "macos-arm64",
    "windows-11-x64",
    "ubuntu-24.04-gnome-wayland",
    "ubuntu-24.04-gnome-x11",
    "kde-plasma-6-wayland",
)
DAVINCI_TARGETS = ("macos-arm64", "windows-11-x64", "ubuntu-24.04-gnome-x11")


class ReleaseEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QualityThresholds(ReleaseEvidenceModel):
    event_precision: Annotated[float, Field(ge=0, le=1)] = 0.7
    event_recall: Annotated[float, Field(ge=0, le=1)] = 0.7
    event_f1: Annotated[float, Field(ge=0, le=1)] = 0.7
    ranking_map: Annotated[float, Field(ge=0, le=1)] = 0.65
    ranking_ndcg: Annotated[float, Field(ge=0, le=1)] = 0.7
    event_type_coverage: Annotated[float, Field(ge=0, le=1)] = 0.6
    context_retention: Annotated[float, Field(ge=0, le=1)] = 0.7
    minimum_total_corpus_hours: Annotated[float, Field(ge=1)] = 8


class ReleaseEvidenceManifest(ReleaseEvidenceModel):
    fixture_reports: dict[str, Path]
    davinci_reports: dict[str, Path]
    endurance_reports: dict[str, Path]
    event_reports: tuple[Path, ...]
    ranking_reports: tuple[Path, ...]
    corpus_kinds: dict[str, Literal["gameplay", "speech_or_general"]]
    thresholds: QualityThresholds = QualityThresholds()


class ReleaseEvidenceCheck(ReleaseEvidenceModel):
    name: str
    passed: bool
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ReleaseEvidenceReport(ReleaseEvidenceModel):
    report_version: str = "1.0.0"
    checks: tuple[ReleaseEvidenceCheck, ...]
    passed: bool


def evaluate_release_evidence(
    manifest: ReleaseEvidenceManifest, base_directory: Path
) -> ReleaseEvidenceReport:
    checks: list[ReleaseEvidenceCheck] = []
    for target in FIXTURE_TARGETS:
        fixture_report = _load_target_report(
            "fixture",
            target,
            manifest.fixture_reports,
            base_directory,
            FixtureContractReport,
            checks,
        )
        if fixture_report is not None:
            checks.append(_validate_fixture(target, fixture_report))
    for target in DAVINCI_TARGETS:
        davinci_report = _load_target_report(
            "davinci",
            target,
            manifest.davinci_reports,
            base_directory,
            DaVinciContractReport,
            checks,
        )
        if davinci_report is not None:
            checks.append(_validate_davinci(target, davinci_report))
    for target in FIXTURE_TARGETS:
        endurance_report = _load_target_report(
            "endurance", target, manifest.endurance_reports, base_directory, EnduranceReport, checks
        )
        if endurance_report is not None:
            checks.append(_validate_endurance(target, endurance_report))
    events = _load_report_sequence(
        "events", manifest.event_reports, base_directory, EventBenchmarkReport, checks
    )
    rankings = _load_report_sequence(
        "ranking", manifest.ranking_reports, base_directory, RankingBenchmarkReport, checks
    )
    checks.extend(_validate_corpora(events, rankings, manifest))
    return ReleaseEvidenceReport(
        checks=tuple(checks),
        passed=bool(checks) and all(check.passed for check in checks),
    )


def evaluate_release_manifest(path: Path) -> ReleaseEvidenceReport:
    manifest = ReleaseEvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    return evaluate_release_evidence(manifest, path.resolve().parent)


def _load_target_report[ReportModel: BaseModel](
    kind: str,
    target: str,
    references: dict[str, Path],
    base_directory: Path,
    model: type[ReportModel],
    checks: list[ReleaseEvidenceCheck],
) -> ReportModel | None:
    reference = references.get(target)
    if reference is None:
        checks.append(_failed(f"{kind}:{target}", "required report is missing"))
        return None
    try:
        return model.model_validate_json(_resolve(reference, base_directory).read_text("utf-8"))
    except Exception as error:
        checks.append(_failed(f"{kind}:{target}", f"invalid report: {error}"))
        return None


def _load_report_sequence[ReportModel: BaseModel](
    kind: str,
    references: tuple[Path, ...],
    base_directory: Path,
    model: type[ReportModel],
    checks: list[ReleaseEvidenceCheck],
) -> tuple[ReportModel, ...]:
    reports: list[ReportModel] = []
    for index, reference in enumerate(references, start=1):
        try:
            reports.append(
                model.model_validate_json(_resolve(reference, base_directory).read_text("utf-8"))
            )
        except Exception as error:
            checks.append(_failed(f"{kind}:{index}", f"invalid report: {error}"))
    return tuple(reports)


def _validate_fixture(target: str, report: FixtureContractReport) -> ReleaseEvidenceCheck:
    expected_platform, desktop, session = _fixture_identity(target)
    identity_matches = report.platform == expected_platform
    if desktop:
        identity_matches = (
            identity_matches and desktop in (report.desktop_environment or "").upper()
        )
    if session:
        identity_matches = identity_matches and report.session_type == session
    required_cases = {
        "fixture_ready",
        "fixture_observed",
        "bounded_capture",
        "pointer_round_trip",
        "keyboard_and_semantic_submit",
        "semantic_button",
        "semantic_checkbox",
    }
    passed_cases = {case.name for case in report.cases if case.passed}
    passed = report.passed and identity_matches and required_cases <= passed_cases
    return ReleaseEvidenceCheck(
        name=f"fixture:{target}",
        passed=passed,
        evidence={
            "platform": report.platform,
            "platform_release": report.platform_release,
            "desktop_environment": report.desktop_environment,
            "session_type": report.session_type,
            "backend": report.backend,
            "contract_passed": report.passed,
            "passed_cases": sorted(passed_cases),
        },
        error=None if passed else "fixture failed or report identity does not match target",
    )


def _validate_davinci(target: str, report: DaVinciContractReport) -> ReleaseEvidenceCheck:
    expected_platform, _desktop, _session = _fixture_identity(target)
    required_cases = {
        "initial_import_and_save",
        "idempotent_render_and_verify",
        "revision_creates_new_timeline",
        "render_cancellation",
    }
    passed_cases = {case.name for case in report.cases if case.passed}
    passed = (
        report.passed
        and report.platform == expected_platform
        and report.resolve_version is not None
        and report.resolve_version.startswith("20.")
        and required_cases <= passed_cases
    )
    return ReleaseEvidenceCheck(
        name=f"davinci:{target}",
        passed=passed,
        evidence={
            "platform": report.platform,
            "platform_release": report.platform_release,
            "resolve_version": report.resolve_version,
            "contract_passed": report.passed,
            "passed_cases": sorted(passed_cases),
        },
        error=None if passed else "DaVinci contract, target platform, or Resolve 20.x check failed",
    )


def _validate_endurance(target: str, report: EnduranceReport) -> ReleaseEvidenceCheck:
    passed = (
        report.passed
        and report.requested_seconds >= 8 * 3_600
        and report.elapsed_seconds >= report.requested_seconds * 0.99
        and report.iterations > 0
        and report.captures > 0
        and report.input_round_trips > 0
        and not report.failures
    )
    return ReleaseEvidenceCheck(
        name=f"endurance:{target}",
        passed=passed,
        evidence={
            "requested_seconds": report.requested_seconds,
            "elapsed_seconds": report.elapsed_seconds,
            "iterations": report.iterations,
            "captures": report.captures,
            "input_round_trips": report.input_round_trips,
            "backend": report.backend,
        },
        error=None if passed else "eight-hour mixed capture/input endurance requirements failed",
    )


def _validate_corpora(
    events: tuple[EventBenchmarkReport, ...],
    rankings: tuple[RankingBenchmarkReport, ...],
    manifest: ReleaseEvidenceManifest,
) -> list[ReleaseEvidenceCheck]:
    event_by_id = {
        report.corpus_id: report
        for report in events
        if report.corpus_id
    }
    ranking_by_id = {
        report.corpus_id: report
        for report in rankings
        if report.corpus_id
    }
    corpus_ids = set(manifest.corpus_kinds)
    paired = corpus_ids and corpus_ids == set(event_by_id) == set(ranking_by_id)
    kinds = set(manifest.corpus_kinds.values())
    duration_seconds = sum(
        event_by_id[corpus_id].source_duration_seconds or 0
        for corpus_id in corpus_ids
        if corpus_id in event_by_id
    )
    structure_passed = bool(
        paired
        and len(corpus_ids) >= 2
        and kinds == {"gameplay", "speech_or_general"}
        and duration_seconds >= manifest.thresholds.minimum_total_corpus_hours * 3_600
    )
    checks = [
        ReleaseEvidenceCheck(
            name="corpora:coverage",
            passed=structure_passed,
            evidence={
                "corpus_ids": sorted(corpus_ids),
                "kinds": sorted(kinds),
                "total_hours": round(duration_seconds / 3_600, 3),
                "event_report_ids": sorted(event_by_id),
                "ranking_report_ids": sorted(ranking_by_id),
            },
            error=(
                None
                if structure_passed
                else "corpora must be paired, include both required kinds, and total eight hours"
            ),
        )
    ]
    for corpus_id in sorted(corpus_ids & set(event_by_id) & set(ranking_by_id)):
        event = event_by_id[corpus_id]
        ranking = ranking_by_id[corpus_id]
        same_duration = event.source_duration_seconds == ranking.source_duration_seconds
        event_passed = (
            event.precision >= manifest.thresholds.event_precision
            and event.recall >= manifest.thresholds.event_recall
            and event.f1 >= manifest.thresholds.event_f1
        )
        ranking_passed = (
            ranking.mean_average_precision >= manifest.thresholds.ranking_map
            and ranking.normalized_discounted_cumulative_gain >= manifest.thresholds.ranking_ndcg
            and ranking.event_type_coverage >= manifest.thresholds.event_type_coverage
            and ranking.context_retention is not None
            and ranking.context_retention >= manifest.thresholds.context_retention
        )
        checks.append(
            ReleaseEvidenceCheck(
                name=f"corpus:{corpus_id}",
                passed=bool(same_duration and event_passed and ranking_passed),
                evidence={
                    "duration_seconds": event.source_duration_seconds,
                    "event_precision": event.precision,
                    "event_recall": event.recall,
                    "event_f1": event.f1,
                    "ranking_map": ranking.mean_average_precision,
                    "ranking_ndcg": ranking.normalized_discounted_cumulative_gain,
                    "event_type_coverage": ranking.event_type_coverage,
                    "context_retention": ranking.context_retention,
                },
                error=(
                    None
                    if same_duration and event_passed and ranking_passed
                    else "corpus duration or quality threshold failed"
                ),
            )
        )
    return checks


def _fixture_identity(target: str) -> tuple[str, str | None, str | None]:
    if target == "macos-arm64":
        return "Darwin", None, None
    if target == "windows-11-x64":
        return "Windows", None, None
    if "gnome-wayland" in target:
        return "Linux", "GNOME", "wayland"
    if "gnome-x11" in target:
        return "Linux", "GNOME", "x11"
    return "Linux", "KDE", "wayland"


def _resolve(path: Path, base_directory: Path) -> Path:
    return path if path.is_absolute() else base_directory / path


def _failed(name: str, error: str) -> ReleaseEvidenceCheck:
    return ReleaseEvidenceCheck(name=name, passed=False, error=error)


def write_release_evidence_report(report: ReleaseEvidenceReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def write_example_manifest(output: Path) -> None:
    payload = {
        "fixture_reports": {target: f"fixtures/{target}.json" for target in FIXTURE_TARGETS},
        "davinci_reports": {target: f"davinci/{target}.json" for target in DAVINCI_TARGETS},
        "endurance_reports": {
            target: f"endurance/{target}.json" for target in FIXTURE_TARGETS
        },
        "event_reports": ["corpora/gameplay-events.json", "corpora/general-events.json"],
        "ranking_reports": ["corpora/gameplay-ranking.json", "corpora/general-ranking.json"],
        "corpus_kinds": {
            "gameplay-01": "gameplay",
            "general-01": "speech_or_general",
        },
        "thresholds": QualityThresholds().model_dump(mode="json"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
