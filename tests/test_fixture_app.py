import json
from pathlib import Path

import pytest

from nimbledesk.testing.fixture_app import FixtureRecorder, FixtureState


def test_fixture_recorder_persists_atomic_state_and_event_log(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    recorder = FixtureRecorder(state_path, events_path)

    recorder.update("ready", ready=True)
    recorder.update("text_submitted", submitted_text="contract text")
    recorder.update("password_submitted", password_length=12)

    state = FixtureState.model_validate_json(state_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert state.ready
    assert state.submitted_text == "contract text"
    assert state.password_length == 12
    assert [event["event"] for event in events] == [
        "ready",
        "text_submitted",
        "password_submitted",
    ]
    assert not state_path.with_suffix(".json.tmp").exists()


def test_fixture_recorder_rejects_unknown_state(tmp_path: Path) -> None:
    recorder = FixtureRecorder(tmp_path / "state.json", tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="unknown fixture state field"):
        recorder.update("invalid", unknown=True)
