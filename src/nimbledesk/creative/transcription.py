from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from nimbledesk.creative.models import TimeRange, TranscriptSegment


class TranscriptionError(RuntimeError):
    pass


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
) -> tuple[TranscriptSegment, ...]:
    executable = shutil.which("whisper")
    if executable is None:
        raise TranscriptionError(
            "the whisper CLI is not installed; install openai-whisper or provide --transcript"
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
        completed = subprocess.run(command, capture_output=True, check=False, text=True)
        if completed.returncode != 0:
            raise TranscriptionError(completed.stderr.strip() or "Whisper transcription failed")
        result_path = output_directory / f"{source.stem}.json"
        payload: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    return tuple(_segment_from_whisper(segment) for segment in payload.get("segments", []))


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
