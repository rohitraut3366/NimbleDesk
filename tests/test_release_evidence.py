from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from nimbledesk.media.models import HighlightCandidate, TimelineEvent
from nimbledesk.testing.davinci_contract import DaVinciContractCase, DaVinciContractReport
from nimbledesk.testing.fixture_contract import FixtureCase, FixtureContractReport
from nimbledesk.testing.qualification import (
    EnduranceReport,
    HighlightAnnotation,
    benchmark_events,
    benchmark_ranking,
)
from nimbledesk.testing.release_evidence import (
    DAVINCI_TARGETS,
    FIXTURE_TARGETS,
    INSTALLER_CASES,
    INSTALLER_TARGETS,
    ISOLATION_CASES,
    ISOLATION_TARGETS,
    AdapterIsolationReport,
    InstallerLifecycleReport,
    PhysicalEvidenceCase,
    ReleaseEvidenceManifest,
    evaluate_release_evidence,
    write_example_manifest,
)


def test_release_evidence_accepts_complete_target_and_corpus_matrix(tmp_path: Path) -> None:
    fixture_paths: dict[str, Path] = {}
    endurance_paths: dict[str, Path] = {}
    isolation_paths: dict[str, Path] = {}
    installer_paths: dict[str, Path] = {}
    for target in FIXTURE_TARGETS:
        platform_name, desktop, session = _identity(target)
        fixture_paths[target] = _write(
            tmp_path / "fixtures" / f"{target}.json",
            FixtureContractReport(
                platform=platform_name,
                platform_release="qualified-release",
                desktop_environment=desktop,
                session_type=session,
                backend="native:qualified",
                started_at=1,
                completed_at=2,
                capabilities=("screen_capture", "pointer", "keyboard", "accessibility"),
                permissions={"screen_capture": "granted"},
                cases=tuple(
                    FixtureCase(name=name, passed=True, evidence={})
                    for name in (
                        "fixture_ready",
                        "fixture_observed",
                        "bounded_capture",
                        "pointer_round_trip",
                        "keyboard_and_semantic_submit",
                        "semantic_button",
                        "semantic_checkbox",
                    )
                ),
                passed=True,
            ),
        )
        endurance_paths[target] = _write(
            tmp_path / "endurance" / f"{target}.json",
            EnduranceReport(
                started_at=1,
                completed_at=28_801,
                requested_seconds=28_800,
                elapsed_seconds=28_800,
                iterations=5_760,
                captures=480,
                input_round_trips=96,
                failures=(),
                mean_observation_latency_ms=20,
                p95_observation_latency_ms=30,
                backend="native:qualified",
                capabilities=("screen_capture", "pointer", "keyboard"),
                passed=True,
            ),
        )
        isolation_paths[target] = _write(
            tmp_path / "isolation" / f"{target}.json",
            AdapterIsolationReport(
                target_id=target,
                platform=platform_name,
                platform_release="qualified-release",
                adapter_id="nimbledesk.malicious-fixture",
                malicious_fixture_sha256="a" * 64,
                cases=tuple(
                    PhysicalEvidenceCase(name=name, passed=True)
                    for name in ISOLATION_CASES
                ),
                passed=True,
            ),
        )
        installer_paths[target] = _write(
            tmp_path / "installers" / f"{target}.json",
            InstallerLifecycleReport(
                target_id=target,
                platform=platform_name,
                platform_release="qualified-release",
                release_version="1.0.0",
                artifact_name=f"nimbledesk-{target}",
                artifact_sha256=("b" * 63) + str(len(installer_paths)),
                signed_release_key_id="production-2026",
                signed_release_verified=True,
                native_signature_verified=(
                    True if platform_name in {"Darwin", "Windows"} else None
                ),
                cases=tuple(
                    PhysicalEvidenceCase(name=name, passed=True)
                    for name in INSTALLER_CASES
                ),
                passed=True,
            ),
        )
    davinci_paths = {
        target: _write(
            tmp_path / "davinci" / f"{target}.json",
            DaVinciContractReport(
                started_at=1,
                completed_at=2,
                platform=_identity(target)[0],
                platform_release="qualified-release",
                resolve_version="20.2.1",
                qualification_project="qualification",
                cases=tuple(
                    DaVinciContractCase(name=name, passed=True, evidence={})
                    for name in (
                        "initial_import_and_save",
                        "idempotent_render_and_verify",
                        "revision_creates_new_timeline",
                        "render_cancellation",
                    )
                ),
                passed=True,
            ),
        )
        for target in DAVINCI_TARGETS
    }
    event_paths: list[Path] = []
    ranking_paths: list[Path] = []
    for corpus_id in ("gameplay-01", "general-01"):
        event = TimelineEvent(time_seconds=30, event_type="payoff")
        event_paths.append(
            _write(
                tmp_path / "corpora" / f"{corpus_id}-events.json",
                benchmark_events(
                    (event,),
                    (event,),
                    3,
                    corpus_id=corpus_id,
                    source_duration_seconds=14_400,
                ),
            )
        )
        annotation = HighlightAnnotation(
            moment_id=f"{corpus_id}-moment",
            peak_seconds=30,
            event_type="payoff",
            relevance=5,
            context_start_seconds=25,
            context_end_seconds=35,
        )
        candidate = HighlightCandidate(
            rank=1,
            start_seconds=20,
            end_seconds=40,
            peak_seconds=30,
            score=1,
            reasons=("qualified",),
        )
        ranking_paths.append(
            _write(
                tmp_path / "corpora" / f"{corpus_id}-ranking.json",
                benchmark_ranking(
                    (annotation,),
                    (candidate,),
                    1,
                    3,
                    corpus_id=corpus_id,
                    source_duration_seconds=14_400,
                ),
            )
        )
    manifest = ReleaseEvidenceManifest(
        fixture_reports=fixture_paths,
        davinci_reports=davinci_paths,
        endurance_reports=endurance_paths,
        isolation_reports=isolation_paths,
        installer_reports=installer_paths,
        event_reports=tuple(event_paths),
        ranking_reports=tuple(ranking_paths),
        corpus_kinds={
            "gameplay-01": "gameplay",
            "general-01": "speech_or_general",
        },
    )

    report = evaluate_release_evidence(manifest, tmp_path)

    assert report.passed
    assert all(check.passed for check in report.checks)
    assert next(check for check in report.checks if check.name == "corpora:coverage").evidence[
        "total_hours"
    ] == 8


def test_release_evidence_rejects_missing_reports_and_corpus_mix(tmp_path: Path) -> None:
    report = evaluate_release_evidence(
        ReleaseEvidenceManifest(
            fixture_reports={},
            davinci_reports={},
            endurance_reports={},
            isolation_reports={},
            installer_reports={},
            event_reports=(),
            ranking_reports=(),
            corpus_kinds={},
        ),
        tmp_path,
    )

    assert not report.passed
    assert any(check.name == "fixture:macos-arm64" for check in report.checks)
    corpus_coverage = next(
        check for check in report.checks if check.name == "corpora:coverage"
    )
    assert corpus_coverage.passed is False


def test_release_example_contains_every_required_target(tmp_path: Path) -> None:
    path = tmp_path / "release-evidence.json"

    write_example_manifest(path)
    manifest = ReleaseEvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))

    assert set(manifest.fixture_reports) == set(FIXTURE_TARGETS)
    assert set(manifest.davinci_reports) == set(DAVINCI_TARGETS)
    assert set(manifest.endurance_reports) == set(FIXTURE_TARGETS)
    assert set(manifest.isolation_reports) == set(ISOLATION_TARGETS)
    assert set(manifest.installer_reports) == set(INSTALLER_TARGETS)


def _write(path: Path, report: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(), encoding="utf-8")
    return path


def _identity(target: str) -> tuple[str, str | None, str | None]:
    if target == "macos-arm64":
        return "Darwin", None, None
    if target == "windows-11-x64":
        return "Windows", None, None
    if "gnome-wayland" in target:
        return "Linux", "GNOME", "wayland"
    if "gnome-x11" in target:
        return "Linux", "GNOME", "x11"
    return "Linux", "KDE", "wayland"
