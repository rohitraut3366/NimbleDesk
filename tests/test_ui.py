import time
from pathlib import Path

from pytest import MonkeyPatch
from starlette.testclient import TestClient

from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.workflow import CreationWorkflow
from nimbledesk.ui.server import (
    CreateJobRequest,
    JobRecord,
    JobService,
    PersistedJob,
    app,
)


def test_console_serves_creation_form_and_rejects_missing_source(tmp_path: Path) -> None:
    client = TestClient(app)

    page = client.get("/")
    response = client.post(
        "/api/jobs",
        json={
            "source": str(tmp_path / "missing.mp4"),
            "output_directory": str(tmp_path / "output"),
            "brief": {"title": "Test creation"},
        },
    )

    assert page.status_code == 200
    assert "NimbleDesk Studio" in page.text
    assert response.status_code == 400
    assert "does not exist" in response.json()["error"]


def test_console_returns_unknown_job() -> None:
    response = TestClient(app).get("/api/jobs/unknown")

    assert response.status_code == 404
    assert response.json() == {"error": "unknown job"}


def test_job_service_cancels_and_persists_long_running_job(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    storage = tmp_path / "jobs"

    def wait_for_cancellation(self: CreationWorkflow, *args: object, **kwargs: object) -> None:
        token = kwargs["cancellation"]
        assert isinstance(token, CancellationToken)
        while not token.is_cancelled():
            time.sleep(0.01)
        token.check()

    monkeypatch.setattr(CreationWorkflow, "create", wait_for_cancellation)
    service = JobService(storage, maximum_workers=1)
    job = service.submit(
        CreateJobRequest(
            source=source,
            output_directory=tmp_path / "output",
            brief=CreativeBrief(title="Cancellation fixture"),
        )
    )
    _wait_for_status(job, "running")

    service.cancel(job.state.job_id)
    _wait_for_status(job, "cancelled")
    service.close()

    persisted = PersistedJob.model_validate_json(
        (storage / f"{job.state.job_id}.json").read_text(encoding="utf-8")
    )
    assert persisted.status == "cancelled"


def test_job_service_marks_running_job_interrupted_after_restart(tmp_path: Path) -> None:
    storage = tmp_path / "jobs"
    storage.mkdir()
    state = PersistedJob(
        job_id="job-1",
        request=CreateJobRequest(
            source=tmp_path / "source.mp4",
            output_directory=tmp_path / "output",
            brief=CreativeBrief(title="Recovery fixture"),
        ),
        status="running",
        stage="analyzing",
        progress=0.4,
        created_at=1,
        updated_at=2,
    )
    (storage / "job-1.json").write_text(state.model_dump_json(), encoding="utf-8")

    service = JobService(storage)
    recovered = service.get("job-1")
    service.close()

    assert recovered is not None
    assert recovered.state.status == "interrupted"
    assert "resume from cached analysis" in recovered.state.stage


def _wait_for_status(job: JobRecord, expected: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if job.state.status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected}")
