from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

import nimbledesk.testing.davinci_contract as davinci_contract
from nimbledesk.creative.davinci import DaVinciResult
from nimbledesk.creative.models import (
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.media.process import ProcessCancelled


def _plan(path: Path) -> EditPlan:
    source_range = TimeRange(start_seconds=1, end_seconds=3)
    return EditPlan(
        source_path=path / "source.mp4",
        brief=CreativeBrief(title="Physical fixture", captions=False, music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=path / "source.mp4",
                source_range=source_range,
                timeline_start_seconds=0,
                speed=SpeedTreatment(rationale="preserve timing"),
                visual=VisualTreatment(rationale="preserve color"),
                score=1,
                evidence=(),
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )


def test_davinci_contract_runs_import_render_revision_and_cancellation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source_plan_path = tmp_path / "plan.json"
    source_plan_path.write_text(_plan(tmp_path).model_dump_json(), encoding="utf-8")
    render_path = tmp_path / "evidence" / "davinci-final.mp4"
    calls = 0

    def fake_execute(*_args: object, **kwargs: object) -> DaVinciResult:
        nonlocal calls
        calls += 1
        if calls == 4:
            assert callable(kwargs["cancelled"])
            raise ProcessCancelled("render stopped")
        if calls == 2:
            render_path.parent.mkdir(parents=True, exist_ok=True)
            render_path.write_bytes(b"render")
        fingerprint = "initial" if calls <= 2 else "revision"
        return DaVinciResult(
            project_name="Qualification",
            timeline_name=f"Timeline {fingerprint}",
            render_job_id="job-1" if calls == 2 else None,
            render_path=render_path if calls == 2 else None,
            plan_fingerprint=fingerprint,
            timeline_reused=calls == 2,
            project_saved=True,
            resolve_version="20.2.1",
        )

    monkeypatch.setattr(davinci_contract, "execute_davinci_isolated", fake_execute)
    monkeypatch.setattr(
        davinci_contract,
        "verify_render",
        lambda *_args, **_kwargs: SimpleNamespace(valid=True),
    )

    report = davinci_contract.run_davinci_contract(
        source_plan_path, tmp_path / "evidence", timeout_seconds=30
    )

    assert report.passed
    assert report.resolve_version == "20.2.1"
    assert calls == 4
    assert [case.name for case in report.cases] == [
        "initial_import_and_save",
        "idempotent_render_and_verify",
        "revision_creates_new_timeline",
        "render_cancellation",
    ]
    revision = EditPlan.model_validate_json(
        (tmp_path / "evidence" / "qualification-revision-plan.json").read_text()
    )
    assert revision.segments[0].visual.saturation_multiplier == 1.05
