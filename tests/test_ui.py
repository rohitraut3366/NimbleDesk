from pathlib import Path

from starlette.testclient import TestClient

from nimbledesk.ui.server import app


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
