from __future__ import annotations

import hashlib
import importlib
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.creative.models import TimeRange, TranscriptSegment
from nimbledesk.media.process import CancellationCheck, ProcessCancelled, run_cancellable


class TranscriptionError(RuntimeError):
    pass


MAXIMUM_PROVIDER_RESPONSE_BYTES = 1_000_000


class TranscriptionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TranscriptionProviderConfig(TranscriptionModel):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,79}$")
    model: str
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Annotated[float, Field(ge=5, le=28_800)] = 3_600
    execution_location: Literal["local", "remote"] = "local"


class TranscriptionProviderRequest(TranscriptionModel):
    request_version: str = "1.0.0"
    source_path: Path
    source_name: str
    language: str | None = None


class TranscriptionProviderUsage(TranscriptionModel):
    audio_seconds: Annotated[float, Field(ge=0)] | None = None
    provider_input_tokens: Annotated[int, Field(ge=0)] | None = None
    provider_output_tokens: Annotated[int, Field(ge=0)] | None = None


class TranscriptionProviderResponse(TranscriptionModel):
    segments: tuple[TranscriptSegment, ...]
    detected_language: str | None = None
    usage: TranscriptionProviderUsage | None = None


class TranscriptionAnalysis(TranscriptionModel):
    analysis_version: str = "1.0.0"
    provider_id: str
    model: str
    configuration_hash: str
    detected_language: str | None = None
    segment_count: int
    usage: TranscriptionProviderUsage | None = None


def load_transcript(path: Path | None) -> tuple[TranscriptSegment, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("transcript must contain a JSON list")
    return tuple(TranscriptSegment.model_validate(item) for item in payload)


def transcribe_with_whisper(
    source: Path,
    model: str = "small",
    language: str | None = None,
    cancelled: CancellationCheck | None = None,
) -> tuple[TranscriptSegment, ...]:
    try:
        faster_whisper = importlib.import_module("faster_whisper")
    except ImportError:
        faster_whisper = None
    if faster_whisper is not None:
        return _transcribe_with_faster_whisper(
            faster_whisper.WhisperModel,
            source,
            model,
            language,
            cancelled,
        )
    executable = shutil.which("whisper")
    if executable is None:
        raise TranscriptionError(
            "automatic transcription requires the speech extra or whisper CLI; install "
            "NimbleDesk with --extra speech, install openai-whisper, or provide --transcript"
        )
    with tempfile.TemporaryDirectory(prefix="nimbledesk-transcript-") as temporary:
        output_directory = Path(temporary)
        command = [
            executable,
            str(source),
            "--model",
            model,
            "--output_format",
            "json",
            "--output_dir",
            str(output_directory),
            "--verbose",
            "False",
        ]
        if language:
            command.extend(["--language", language])
        completed = run_cancellable(command, cancelled=cancelled)
        if completed.returncode != 0:
            raise TranscriptionError(completed.stderr.strip() or "Whisper transcription failed")
        result_path = output_directory / f"{source.stem}.json"
        payload: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    return tuple(_segment_from_whisper(segment) for segment in payload.get("segments", []))


def transcribe_with_provider(
    source: Path,
    output_directory: Path,
    config_path: Path,
    language: str | None = None,
    cancelled: CancellationCheck | None = None,
) -> tuple[tuple[TranscriptSegment, ...], Path]:
    config = TranscriptionProviderConfig.model_validate_json(
        config_path.read_text(encoding="utf-8")
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    request = TranscriptionProviderRequest(
        source_path=source.resolve(), source_name=source.name, language=language
    )
    request_path = output_directory / "request.json"
    response_path = output_directory / "response.json"
    request_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
    command = _provider_command(config.command, request_path, response_path)
    try:
        completed = run_cancellable(
            command, cancelled=cancelled, timeout_seconds=config.timeout_seconds
        )
    except TimeoutError as error:
        raise TranscriptionError("transcription provider timed out") from error
    if completed.returncode != 0:
        raise TranscriptionError(
            completed.stderr[:8_192].strip() or "transcription provider failed"
        )
    if not response_path.is_file():
        raise TranscriptionError("transcription provider did not write its response")
    if response_path.stat().st_size > MAXIMUM_PROVIDER_RESPONSE_BYTES:
        raise TranscriptionError("transcription provider response exceeded one megabyte")
    response = TranscriptionProviderResponse.model_validate_json(
        response_path.read_text(encoding="utf-8")
    )
    analysis = TranscriptionAnalysis(
        provider_id=config.provider_id,
        model=config.model,
        configuration_hash=hashlib.sha256(
            config.model_dump_json().encode("utf-8")
            + source.resolve().as_posix().encode("utf-8")
        ).hexdigest(),
        detected_language=response.detected_language,
        segment_count=len(response.segments),
        usage=response.usage,
    )
    analysis_path = output_directory / "analysis.json"
    analysis_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return response.segments, analysis_path


def _transcribe_with_faster_whisper(
    model_class: Any,
    source: Path,
    model: str,
    language: str | None,
    cancelled: CancellationCheck | None,
) -> tuple[TranscriptSegment, ...]:
    check_cancelled = cancelled or (lambda: False)
    try:
        whisper_model = model_class(model, device="auto", compute_type="int8")
        generated_segments, _information = whisper_model.transcribe(
            str(source),
            language=language,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
        )
        segments: list[TranscriptSegment] = []
        for generated in generated_segments:
            if check_cancelled():
                raise ProcessCancelled("creation was cancelled")
            segments.append(
                _segment_from_whisper(
                    {
                        "start": generated.start,
                        "end": generated.end,
                        "text": generated.text,
                        "avg_logprob": getattr(generated, "avg_logprob", 0),
                    }
                )
            )
        return tuple(segments)
    except ProcessCancelled:
        raise
    except Exception as error:
        raise TranscriptionError(f"faster-whisper transcription failed: {error}") from error


def write_transcript(segments: tuple[TranscriptSegment, ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([segment.model_dump(mode="json") for segment in segments], indent=2),
        encoding="utf-8",
    )


def _segment_from_whisper(payload: dict[str, Any]) -> TranscriptSegment:
    average_log_probability = float(payload.get("avg_logprob", 0))
    confidence = max(0.0, min(1.0, math.exp(average_log_probability)))
    return TranscriptSegment(
        source_range=TimeRange(
            start_seconds=max(0, float(payload["start"])),
            end_seconds=float(payload["end"]),
        ),
        text=str(payload.get("text", "")).strip(),
        confidence=round(confidence, 4),
    )


def _provider_command(
    command: tuple[str, ...], request_path: Path, response_path: Path
) -> list[str]:
    if "{request}" not in command or "{response}" not in command:
        raise TranscriptionError(
            "transcription provider command must contain {request} and {response} arguments"
        )
    return [
        argument.replace("{request}", str(request_path)).replace(
            "{response}", str(response_path)
        )
        for argument in command
    ]
