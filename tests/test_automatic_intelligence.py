from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch

from nimbledesk.creative import automatic
from nimbledesk.creative.automatic import resolve_automatic_intelligence
from nimbledesk.creative.models import CreativeBrief
from nimbledesk.creative.vision import VisionProviderConfig


def test_automatic_mode_discovers_local_tools_provider_and_catalogs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    music_catalog = tmp_path / "music.json"
    sound_catalog = tmp_path / "sound.json"
    music_catalog.write_text("[]", encoding="utf-8")
    sound_catalog.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(automatic.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(automatic, "_module_available", lambda _module: True)

    selection = resolve_automatic_intelligence(
        CreativeBrief(content_kind="gameplay"),
        tmp_path / "output",
        enabled=True,
        game_ocr=False,
        transcribe=False,
        vision_provider=None,
        music_catalog=None,
        sound_catalog=None,
        environment={
            "NIMBLEDESK_VISION_ENDPOINT": "http://127.0.0.1:11434/v1/chat/completions",
            "NIMBLEDESK_VISION_MODEL": "local-vision",
            "NIMBLEDESK_MUSIC_CATALOG": str(music_catalog),
            "NIMBLEDESK_SOUND_CATALOG": str(sound_catalog),
            "NIMBLEDESK_VISION_API_KEY": "must-not-be-persisted",
        },
    )

    assert selection.game_ocr
    assert selection.transcribe
    assert selection.music_catalog == music_catalog
    assert selection.sound_catalog == sound_catalog
    assert selection.vision_provider is not None
    provider_text = selection.vision_provider.read_text(encoding="utf-8")
    provider = VisionProviderConfig.model_validate_json(provider_text)
    assert provider.execution_location == "local"
    assert provider.model == "local-vision"
    assert "must-not-be-persisted" not in provider_text
    assert {item.status for item in selection.report.capabilities} == {"enabled"}


def test_automatic_mode_does_not_send_frames_without_remote_permission(tmp_path: Path) -> None:
    selection = resolve_automatic_intelligence(
        CreativeBrief(),
        tmp_path / "output",
        enabled=True,
        game_ocr=False,
        transcribe=False,
        vision_provider=None,
        music_catalog=None,
        sound_catalog=None,
        environment={
            "NIMBLEDESK_VISION_ENDPOINT": "https://vision.example/v1/chat/completions",
            "NIMBLEDESK_VISION_MODEL": "remote-vision",
        },
    )

    assert selection.vision_provider is None
    vision = next(
        item for item in selection.report.capabilities if item.capability == "semantic_vision"
    )
    assert vision.status == "blocked_by_policy"
    assert not (tmp_path / "output" / "analysis" / "automatic").exists()


def test_explicit_inputs_override_automatic_discovery(tmp_path: Path) -> None:
    provider = tmp_path / "provider.json"
    music = tmp_path / "music.json"
    sound = tmp_path / "sound.json"

    selection = resolve_automatic_intelligence(
        CreativeBrief(content_kind="tutorial"),
        tmp_path / "output",
        enabled=True,
        game_ocr=True,
        transcribe=True,
        vision_provider=provider,
        music_catalog=music,
        sound_catalog=sound,
        environment={},
    )

    assert selection.game_ocr
    assert selection.transcribe
    assert selection.vision_provider == provider
    assert selection.music_catalog == music
    assert selection.sound_catalog == sound
    assert {item.status for item in selection.report.capabilities} == {"provided"}


def test_automatic_report_is_machine_readable(tmp_path: Path) -> None:
    selection = resolve_automatic_intelligence(
        CreativeBrief(content_kind="tutorial"),
        tmp_path / "output",
        enabled=False,
        game_ocr=False,
        transcribe=False,
        vision_provider=None,
        music_catalog=None,
        sound_catalog=None,
        environment={},
    )
    path = tmp_path / "automatic.json"
    automatic.write_automatic_intelligence_report(selection.report, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["enabled"] is False
    assert len(payload["capabilities"]) == 5
    assert {item["status"] for item in payload["capabilities"]} == {"not_applicable"}
