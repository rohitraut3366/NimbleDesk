from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import ContentKind, CreativeBrief
from nimbledesk.creative.transcription import TranscriptionProviderConfig
from nimbledesk.creative.vision import VisionProviderConfig


class AutomaticCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: Literal["game_ocr", "transcription", "semantic_vision", "music", "sound"]
    status: Literal[
        "enabled", "provided", "unavailable", "not_applicable", "blocked_by_policy", "failed"
    ]
    detail: str


class AutomaticIntelligenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    capabilities: tuple[AutomaticCapability, ...]


class AutomaticIntelligenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    game_ocr: bool
    transcribe: bool
    vision_provider: Path | None
    music_catalog: Path | None
    sound_catalog: Path | None
    transcription_provider: Path | None = None
    report: AutomaticIntelligenceReport


def resolve_automatic_intelligence(
    brief: CreativeBrief,
    output_directory: Path,
    *,
    enabled: bool,
    game_ocr: bool,
    transcribe: bool,
    vision_provider: Path | None,
    music_catalog: Path | None,
    sound_catalog: Path | None,
    transcription_provider: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> AutomaticIntelligenceSelection:
    values = os.environ if environment is None else environment
    capabilities: list[AutomaticCapability] = []

    resolved_game_ocr = game_ocr
    if game_ocr:
        capabilities.append(_capability("game_ocr", "provided", "enabled explicitly"))
    elif not enabled:
        capabilities.append(_capability("game_ocr", "not_applicable", "automatic mode is off"))
    elif brief.content_kind not in {ContentKind.AUTO, ContentKind.GAMEPLAY}:
        capabilities.append(
            _capability("game_ocr", "not_applicable", "content kind is not gameplay or auto")
        )
    elif shutil.which("ffmpeg") and shutil.which("tesseract"):
        resolved_game_ocr = True
        capabilities.append(
            _capability("game_ocr", "enabled", "FFmpeg and Tesseract are available")
        )
    else:
        capabilities.append(
            _capability("game_ocr", "unavailable", "requires FFmpeg and Tesseract on PATH")
        )

    resolved_transcription = transcribe
    resolved_transcription_provider = transcription_provider
    configured_transcription_provider = values.get(
        "NIMBLEDESK_TRANSCRIPTION_PROVIDER", ""
    ).strip()
    if transcription_provider is not None:
        resolved_transcription = False
        capabilities.append(
            _capability(
                "transcription", "provided", "using the supplied provider configuration"
            )
        )
    elif transcribe:
        capabilities.append(_capability("transcription", "provided", "enabled explicitly"))
    elif not enabled:
        capabilities.append(
            _capability("transcription", "not_applicable", "automatic mode is off")
        )
    elif configured_transcription_provider:
        candidate = Path(configured_transcription_provider).expanduser().resolve()
        try:
            config = TranscriptionProviderConfig.model_validate_json(
                candidate.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            capabilities.append(
                _capability(
                    "transcription",
                    "unavailable",
                    f"configured provider is invalid: {error}",
                )
            )
        else:
            if (
                config.execution_location == "remote"
                and not brief.data_policy.allow_remote_audio
            ):
                capabilities.append(
                    _capability(
                        "transcription",
                        "blocked_by_policy",
                        "the configured provider is remote and the brief does not allow "
                        "remote audio",
                    )
                )
            else:
                resolved_transcription_provider = candidate
                capabilities.append(
                    _capability(
                        "transcription",
                        "enabled",
                        f"using the configured {config.execution_location} provider",
                    )
                )
    elif _module_available("faster_whisper") or shutil.which("whisper"):
        resolved_transcription = True
        capabilities.append(
            _capability("transcription", "enabled", "a local Whisper runtime is available")
        )
    else:
        capabilities.append(
            _capability(
                "transcription",
                "unavailable",
                "install the speech extra or a local Whisper CLI",
            )
        )

    resolved_vision = vision_provider
    if vision_provider is not None:
        capabilities.append(
            _capability("semantic_vision", "provided", "using the supplied provider configuration")
        )
    elif not enabled:
        capabilities.append(
            _capability("semantic_vision", "not_applicable", "automatic mode is off")
        )
    else:
        endpoint = values.get("NIMBLEDESK_VISION_ENDPOINT", "").strip()
        model = values.get("NIMBLEDESK_VISION_MODEL", "").strip()
        if not endpoint or not model:
            capabilities.append(
                _capability(
                    "semantic_vision",
                    "unavailable",
                    "NIMBLEDESK_VISION_ENDPOINT and NIMBLEDESK_VISION_MODEL are required",
                )
            )
        else:
            location = _vision_location(endpoint)
            if location == "remote" and not brief.data_policy.allow_remote_frames:
                capabilities.append(
                    _capability(
                        "semantic_vision",
                        "blocked_by_policy",
                        "the configured endpoint is remote and the brief does not allow "
                        "remote frames",
                    )
                )
            else:
                resolved_vision = _write_vision_provider(
                    output_directory, model=model, execution_location=location
                )
                capabilities.append(
                    _capability(
                        "semantic_vision",
                        "enabled",
                        f"using the configured {location} OpenAI-compatible endpoint",
                    )
                )

    resolved_music = _resolve_catalog(
        enabled,
        music_catalog,
        values.get("NIMBLEDESK_MUSIC_CATALOG"),
        Path.home() / ".nimbledesk" / "catalogs" / "music.json",
        "music",
        capabilities,
    )
    resolved_sound = _resolve_catalog(
        enabled,
        sound_catalog,
        values.get("NIMBLEDESK_SOUND_CATALOG"),
        Path.home() / ".nimbledesk" / "catalogs" / "sound.json",
        "sound",
        capabilities,
    )
    return AutomaticIntelligenceSelection(
        game_ocr=resolved_game_ocr,
        transcribe=resolved_transcription,
        vision_provider=resolved_vision,
        music_catalog=resolved_music,
        sound_catalog=resolved_sound,
        transcription_provider=resolved_transcription_provider,
        report=AutomaticIntelligenceReport(enabled=enabled, capabilities=tuple(capabilities)),
    )


def write_automatic_intelligence_report(
    report: AutomaticIntelligenceReport, path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def record_automatic_failure(
    report: AutomaticIntelligenceReport,
    capability: Literal["game_ocr", "transcription", "semantic_vision", "music", "sound"],
    error: Exception,
) -> AutomaticIntelligenceReport:
    detail = str(error).strip() or type(error).__name__
    updated = tuple(
        item.model_copy(update={"status": "failed", "detail": detail})
        if item.capability == capability
        else item
        for item in report.capabilities
    )
    return report.model_copy(update={"capabilities": updated})


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _vision_location(endpoint: str) -> Literal["local", "remote"]:
    if endpoint.startswith(("http://127.0.0.1", "http://localhost")):
        return "local"
    return "remote"


def _write_vision_provider(
    output_directory: Path,
    *,
    model: str,
    execution_location: Literal["local", "remote"],
) -> Path:
    path = output_directory / "analysis" / "automatic" / "vision-provider.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        command: tuple[str, ...] = (
            sys.executable,
            "vision-http",
            "{request}",
            "{response}",
        )
    else:
        command = (
            sys.executable,
            "-m",
            "nimbledesk.creative.vision_http",
            "{request}",
            "{response}",
        )
    config = VisionProviderConfig(
        provider_id="nimbledesk-auto-vision",
        model=model,
        command=command,
        execution_location=execution_location,
        network_access=True,
        environment_variables=(
            "NIMBLEDESK_VISION_ENDPOINT",
            "NIMBLEDESK_VISION_MODEL",
            "NIMBLEDESK_VISION_API_KEY",
        ),
    )
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _resolve_catalog(
    enabled: bool,
    supplied: Path | None,
    configured: str | None,
    default: Path,
    capability: Literal["music", "sound"],
    capabilities: list[AutomaticCapability],
) -> Path | None:
    if supplied is not None:
        capabilities.append(
            _capability(capability, "provided", "using the supplied licensed catalog")
        )
        return supplied
    if not enabled:
        capabilities.append(_capability(capability, "not_applicable", "automatic mode is off"))
        return None
    candidates = ([Path(configured).expanduser()] if configured else []) + [default]
    selected = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if selected is None:
        capabilities.append(
            _capability(capability, "unavailable", "no configured licensed catalog was found")
        )
        return None
    capabilities.append(
        _capability(capability, "enabled", f"discovered licensed catalog at {selected}")
    )
    return selected


def _capability(
    capability: Literal["game_ocr", "transcription", "semantic_vision", "music", "sound"],
    status: Literal[
        "enabled", "provided", "unavailable", "not_applicable", "blocked_by_policy", "failed"
    ],
    detail: str,
) -> AutomaticCapability:
    return AutomaticCapability(capability=capability, status=status, detail=detail)
