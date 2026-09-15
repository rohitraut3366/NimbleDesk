import json
import zipfile
from pathlib import Path

from pytest import MonkeyPatch

import nimbledesk.diagnostics as diagnostics
from nimbledesk.daemon.audit import AuditLog
from nimbledesk.protocol.models import ActionKind, ActionRequest, ActionResult, ActionStatus


def test_diagnostic_bundle_contains_aggregate_health_without_private_data(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    data = tmp_path / ".nimbledesk"
    runtime = data / "runtime"
    jobs = data / "jobs"
    runtime.mkdir(parents=True)
    jobs.mkdir()
    private_path = tmp_path / "private-video.mp4"
    secret = "secret-that-must-not-leak"
    (runtime / "connection.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 1234, "secret": secret}),
        encoding="utf-8",
    )
    (runtime / "connection.json").chmod(0o600)
    (jobs / "job.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "kind": "create",
                "source": str(private_path),
                "error": "private failure detail",
            }
        ),
        encoding="utf-8",
    )
    action = ActionRequest(
        session_id="session",
        kind=ActionKind.TYPE_TEXT,
        arguments={"text": "private text"},
    )
    result = ActionResult(
        action_id=action.action_id,
        status=ActionStatus.FAILED,
        message="failure",
        started_at=1,
        finished_at=2,
    )
    AuditLog(runtime / "audit.jsonl").record(action, result)
    monkeypatch.setattr(
        diagnostics,
        "_daemon_health",
        lambda _path: {"reachable": True, "backend": "simulator"},
    )
    monkeypatch.setattr(
        diagnostics,
        "_tool_status",
        lambda _tool: {"available": False},
    )

    bundle = diagnostics.build_diagnostic_bundle(tmp_path / "diagnostics.zip", data)
    with zipfile.ZipFile(bundle) as archive:
        report_text = archive.read("report.json").decode()
        report = json.loads(report_text)

    assert report["runtime"]["connection_private"]
    assert report["audit"] == {
        "entries": 1,
        "chain_valid": True,
        "statuses": {"failed": 1},
    }
    assert report["jobs"]["statuses"] == {"failed": 1}
    assert secret not in report_text
    assert str(private_path) not in report_text
    assert "private text" not in report_text
