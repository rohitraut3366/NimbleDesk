import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image
from pytest import MonkeyPatch
from starlette.testclient import TestClient

import nimbledesk.ui.server as ui
from nimbledesk.creative.automatic import AutomaticCapability, AutomaticIntelligenceReport
from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.creative.models import (
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    PlanRevisionRequest,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.creative.style import StyleProfileStore
from nimbledesk.creative.variants import VariantComparison, VariantEvaluation, VariantMetric
from nimbledesk.creative.workflow import CreationResult, CreationWorkflow
from nimbledesk.media.models import MediaMetadata
from nimbledesk.media.photos import PhotoManifest
from nimbledesk.ui.server import (
    _HTML,
    CreateJobRequest,
    JobRecord,
    JobService,
    PersistedJob,
    PhotoJobRequest,
    ReviseJobRequest,
    app,
)


def test_studio_enables_automatic_intelligence_by_default(tmp_path: Path) -> None:
    request = CreateJobRequest(
        source=tmp_path / "source.mp4",
        output_directory=tmp_path / "output",
        brief=CreativeBrief(),
    )

    assert request.automatic_intelligence


def test_completed_job_exposes_automatic_decisions_and_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    output = tmp_path / "output"
    output.mkdir()
    generated = {
        name: output / name
        for name in ("content-index.json", "plan.json", "validation.json", "timeline.fcpxml")
    }
    for path in generated.values():
        path.write_text("{}", encoding="utf-8")
    automatic_path = output / "automatic_intelligence.json"
    automatic_path.write_text(
        AutomaticIntelligenceReport(
            enabled=True,
            capabilities=(
                AutomaticCapability(
                    capability="transcription",
                    status="enabled",
                    detail="local Whisper is available",
                ),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )
    plan = _fixture_plan(source)
    state = PersistedJob(
        job_id="automatic-job",
        request=CreateJobRequest(source=source, output_directory=output, brief=plan.brief),
        status="completed",
        stage="completed",
        progress=1,
        result=CreationResult(
            output_directory=output,
            content_index_path=generated["content-index.json"],
            plan_path=generated["plan.json"],
            validation_path=generated["validation.json"],
            timeline_path=generated["timeline.fcpxml"],
            render_path=None,
            transcript_path=None,
            events_path=None,
            vision_analysis_path=None,
            automatic_intelligence_path=automatic_path,
            plan=plan,
        ),
        created_at=1,
        updated_at=2,
    )

    response = JobRecord(state=state).response()

    assert response["automatic_capabilities"] == [
        {
            "capability": "transcription",
            "status": "enabled",
            "detail": "local Whisper is available",
        }
    ]
    assert any(
        artifact["name"] == "automatic-intelligence" for artifact in response["artifacts"]
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_studio_client_javascript_parses(tmp_path: Path) -> None:
    script = tmp_path / "studio.js"
    script.write_text(_HTML.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")

    completed = subprocess.run(
        ["node", "--check", str(script)], capture_output=True, check=False, text=True
    )

    assert completed.returncode == 0, completed.stderr


def test_console_serves_creation_form_and_rejects_missing_source(tmp_path: Path) -> None:
    client = TestClient(app, base_url="http://127.0.0.1")

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
    assert "Review and revise" in page.text
    assert "Actions awaiting your approval" in page.text
    assert "Semantic vision provider JSON" in page.text
    assert "Required event types" in page.text
    assert "Brand logo path" in page.text
    assert "Allow remote frame processing" in page.text
    assert "Required moments" in page.text
    assert response.status_code == 400
    assert "does not exist" in response.json()["error"]


def test_console_reports_creative_intelligence_readiness() -> None:
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get("/api/intelligence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert {item["capability"] for item in payload["capabilities"]} == {
        "game_ocr",
        "transcription",
        "semantic_vision",
        "music",
        "sound",
    }


def test_studio_rejects_dns_rebinding_and_cross_origin_mutations() -> None:
    local = TestClient(app, base_url="http://127.0.0.1")

    page = local.get("/")
    cross_origin = local.post(
        "/api/jobs",
        headers={"origin": "https://malicious.example", "sec-fetch-site": "cross-site"},
        json={},
    )
    rebound = TestClient(app, base_url="http://malicious.example").get("/")

    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert cross_origin.status_code == 403
    assert rebound.status_code == 400


def test_console_accepts_complete_creative_brief(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    service = JobService(tmp_path / "jobs")
    monkeypatch.setattr(service._executor, "submit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui, "JOB_SERVICE", service)

    response = TestClient(app, base_url="http://127.0.0.1").post(
        "/api/jobs",
        json={
            "source": str(source),
            "output_directory": str(tmp_path / "output"),
            "brief": {
                "title": "Complete brief",
                "references": ["Fast, clear opening"],
                "preferred_speakers": ["Rohit"],
                "excluded_content": ["private screens"],
                "mandatory_moments": [
                    {"label": "Clutch", "start_seconds": 10, "end_seconds": 15}
                ],
                "music_style": ["cinematic", "electronic"],
                "transition_style": "cinematic",
                "brand": {
                    "primary_color": "#71e5b4",
                    "protected_colors": ["#ff0000"],
                    "required": False,
                },
                "accessibility": {
                    "captions_required": True,
                    "caption_language": "en",
                    "speaker_labels": True,
                    "maximum_caption_characters_per_line": 38,
                    "maximum_caption_characters_per_second": 18,
                },
                "data_policy": {
                    "allow_remote_transcript": False,
                    "allow_remote_audio": False,
                    "allow_remote_frames": True,
                    "retain_analysis_cache": False,
                },
            },
        },
    )
    service.close()

    assert response.status_code == 202
    brief = response.json()["request"]["brief"]
    assert brief["mandatory_moments"][0]["label"] == "Clutch"
    assert brief["accessibility"]["maximum_caption_characters_per_line"] == 38
    assert brief["data_policy"]["allow_remote_frames"] is True


def test_console_reports_desktop_runtime_health(monkeypatch: MonkeyPatch) -> None:
    class FakeDaemonClient:
        async def call(self, method: str) -> dict[str, object]:
            assert method == "health"
            return {
                "status": "ok",
                "backend": "native:macos-ax+portable-pyautogui",
                "capabilities": ["screen_capture", "pointer"],
            }

    monkeypatch.setattr(ui, "daemon_client", FakeDaemonClient)

    response = TestClient(app, base_url="http://127.0.0.1").get("/api/health")

    assert response.status_code == 200
    assert response.json()["capabilities"] == ["screen_capture", "pointer"]


def test_console_returns_unknown_job() -> None:
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/jobs/unknown")

    assert response.status_code == 404
    assert response.json() == {"error": "unknown job"}


def test_console_persists_and_lists_style_profiles(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "STYLE_PROFILE_STORE", StyleProfileStore(tmp_path / "profiles"))
    client = TestClient(app, base_url="http://127.0.0.1")
    profile = {
        "profile_id": "gaming",
        "name": "Gaming channel",
        "defaults": {"pace": "fast", "aspect_ratio": "9:16", "color_look": "vivid"},
    }

    saved = client.put("/api/style-profiles/gaming", json=profile)
    listed = client.get("/api/style-profiles")

    assert saved.status_code == 200
    assert listed.json()["profiles"][0]["profile_id"] == "gaming"
    assert listed.json()["profiles"][0]["defaults"]["pace"] == "fast"


def test_console_serves_only_registered_generated_artifacts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output = tmp_path / "photo-output"
    output.mkdir()
    contact_sheet = output / "contact-sheet.jpg"
    contact_sheet.write_bytes(b"generated-image")
    service = JobService(tmp_path / "jobs")
    state = PersistedJob(
        job_id="photo-1",
        kind="photo",
        request=PhotoJobRequest(source=tmp_path, output_directory=output),
        status="completed",
        stage="completed",
        progress=1,
        result=PhotoManifest(analyzed=(), selected=(), contact_sheet=contact_sheet),
        created_at=1,
        updated_at=2,
    )
    service._jobs[state.job_id] = JobRecord(state=state)
    monkeypatch.setattr(ui, "JOB_SERVICE", service)
    client = TestClient(app, base_url="http://127.0.0.1")

    listed = client.get("/api/jobs/photo-1")
    artifact = client.get("/api/jobs/photo-1/artifacts/contact-sheet")
    unknown = client.get("/api/jobs/photo-1/artifacts/outside")
    service.close()

    assert listed.json()["artifacts"][0]["name"] == "contact-sheet"
    assert artifact.status_code == 200
    assert artifact.content == b"generated-image"
    assert unknown.status_code == 404


def test_variant_selection_is_persisted_and_creates_renderable_revision(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    output = tmp_path / "creation"
    variant_plan_path = output / "variants" / "context-first" / "edit_plan.json"
    variant_plan_path.parent.mkdir(parents=True)
    plan = _fixture_plan(source)
    variant_plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    comparison_path = output / "variants" / "variant_comparison.json"
    comparison_path.write_text(
        VariantComparison(
            recommended_variant_id="context-first",
            variants=(
                VariantEvaluation(
                    variant_id="context-first",
                    strategy="chronological",
                    plan_path=variant_plan_path,
                    overall_score=0.8,
                    metrics=(
                        VariantMetric(
                            name="narrative_completeness", score=1, evidence="all beats"
                        ),
                    ),
                    tradeoff="More context before the payoff",
                ),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )
    generated = {
        name: output / name
        for name in ("content_index.json", "edit_plan.json", "validation.json", "timeline.fcpxml")
    }
    for path in generated.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    result = CreationResult(
        output_directory=output,
        content_index_path=generated["content_index.json"],
        plan_path=generated["edit_plan.json"],
        validation_path=generated["validation.json"],
        timeline_path=generated["timeline.fcpxml"],
        render_path=None,
        transcript_path=None,
        events_path=None,
        vision_analysis_path=None,
        plan=plan,
        variant_comparison_path=comparison_path,
    )
    state = PersistedJob(
        job_id="create-1",
        request=CreateJobRequest(source=source, output_directory=output, brief=plan.brief),
        status="completed",
        stage="completed",
        progress=1,
        result=result,
        created_at=1,
        updated_at=2,
    )
    service = JobService(tmp_path / "jobs")
    service._jobs[state.job_id] = JobRecord(state=state)
    monkeypatch.setattr(service._executor, "submit", lambda *_args, **_kwargs: None)

    revision = service.select_variant(
        service._jobs[state.job_id],
        "context-first",
        ui.VariantSelectionRequest(ffmpeg_render=True),
    )
    persisted = PersistedJob.model_validate_json(
        (tmp_path / "jobs" / "create-1.json").read_text(encoding="utf-8")
    )
    service.close()

    assert persisted.selected_variant_id == "context-first"
    assert revision.state.kind == "revision"
    assert revision.state.request.plan == variant_plan_path
    assert revision.state.request.ffmpeg_render


def test_console_lists_and_approves_daemon_action(monkeypatch: MonkeyPatch) -> None:
    class FakeDaemonClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call(
            self, method: str, params: dict[str, object] | None = None
        ) -> dict[str, object]:
            self.calls.append((method, params or {}))
            if method == "approval_list":
                return {"approvals": [{"approval_id": "approval-1"}]}
            return {"approval_token": "secret-token"}

    daemon = FakeDaemonClient()
    monkeypatch.setattr(ui, "daemon_client", lambda: daemon)
    client = TestClient(app, base_url="http://127.0.0.1")

    listed = client.get("/api/approvals")
    approved = client.post("/api/approvals/approval-1/approve")

    assert listed.json() == {"approvals": [{"approval_id": "approval-1"}]}
    assert approved.json() == {"status": "approve"}
    assert daemon.calls == [
        ("approval_list", {}),
        ("approval_approve", {"approval_id": "approval-1"}),
    ]


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


def test_job_service_builds_and_persists_validated_revision(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    plan = EditPlan(
        source_path=source,
        brief=CreativeBrief(target_duration_seconds=10, music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=source,
                source_range=TimeRange(start_seconds=0, end_seconds=10),
                timeline_start_seconds=0,
                speed=SpeedTreatment(rationale="keep action understandable"),
                visual=VisualTreatment(rationale="retain source composition"),
                score=1,
                evidence=(
                    Evidence(
                        analyzer="fixture",
                        analyzer_version="1",
                        confidence=0.9,
                        description="high value moment",
                    ),
                ),
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    metadata = MediaMetadata(
        path=source,
        duration_seconds=10,
        width=1920,
        height=1080,
        frame_rate=30,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
    monkeypatch.setattr("nimbledesk.ui.server.probe_media", lambda _path: metadata)
    storage = tmp_path / "jobs"
    service = JobService(storage)

    job = service.submit_revision(
        "parent-1",
        ReviseJobRequest(
            plan=plan_path,
            output_directory=tmp_path / "revision",
            changes=PlanRevisionRequest(
                pace="fast",
                color_look="vivid",
                lock_segment_ids=("segment-001",),
            ),
        ),
    )
    _wait_for_status(job, "completed")
    service.close()

    assert job.state.kind == "revision"
    assert job.state.parent_job_id == "parent-1"
    assert job.state.result is not None
    assert job.state.result.plan.segments[0].locked
    assert (tmp_path / "revision" / "plan_diff.json").is_file()
    persisted = PersistedJob.model_validate_json(
        (storage / f"{job.state.job_id}.json").read_text(encoding="utf-8")
    )
    assert persisted.status == "completed"


def test_job_service_creates_and_persists_photo_story(tmp_path: Path) -> None:
    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (320, 240), (200, 80, 30)).save(source / "one.jpg")
    Image.new("RGB", (320, 240), (30, 120, 200)).save(source / "two.jpg")
    storage = tmp_path / "jobs"
    service = JobService(storage)

    job = service.submit_photo(
        PhotoJobRequest(
            source=source,
            output_directory=tmp_path / "photo-output",
            count=2,
        )
    )
    _wait_for_status(job, "completed")
    service.close()

    assert job.state.kind == "photo"
    assert job.state.result is not None
    assert job.state.result.contact_sheet.is_file()
    persisted = PersistedJob.model_validate_json(
        (storage / f"{job.state.job_id}.json").read_text(encoding="utf-8")
    )
    assert persisted.status == "completed"


def _wait_for_status(job: JobRecord, expected: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if job.state.status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected}")


def _fixture_plan(source: Path) -> EditPlan:
    return EditPlan(
        source_path=source,
        brief=CreativeBrief(target_duration_seconds=10, music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=source,
                source_range=TimeRange(start_seconds=0, end_seconds=10),
                timeline_start_seconds=0,
                speed=SpeedTreatment(rationale="keep action understandable"),
                visual=VisualTreatment(rationale="retain source composition"),
                score=1,
                evidence=(
                    Evidence(
                        analyzer="fixture",
                        analyzer_version="1",
                        confidence=0.9,
                        description="high value moment",
                    ),
                ),
            ),
        ),
        delivery=DeliverySpec(width=1920, height=1080, frame_rate=30),
    )
