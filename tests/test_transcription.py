from pathlib import Path
from types import SimpleNamespace

import pytest

from nimbledesk.creative.transcription import (
    TranscriptionError,
    _transcribe_with_faster_whisper,
)
from nimbledesk.media.process import ProcessCancelled


class FakeWhisperModel:
    def __init__(self, model: str, device: str, compute_type: str) -> None:
        assert model == "small"
        assert device == "auto"
        assert compute_type == "int8"

    def transcribe(self, source: str, **options: object) -> tuple[list[object], object]:
        assert source.endswith("fixture.mp4")
        assert options == {
            "language": "en",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
        }
        return (
            [
                SimpleNamespace(
                    start=1.25,
                    end=3.5,
                    text=" Local transcription ",
                    avg_logprob=-0.1,
                )
            ],
            object(),
        )


def test_faster_whisper_produces_time_aligned_transcript() -> None:
    segments = _transcribe_with_faster_whisper(
        FakeWhisperModel,
        Path("fixture.mp4"),
        "small",
        "en",
        None,
    )

    assert len(segments) == 1
    assert segments[0].text == "Local transcription"
    assert segments[0].source_range.start_seconds == 1.25
    assert segments[0].source_range.end_seconds == 3.5
    assert segments[0].confidence == pytest.approx(0.9048, abs=0.0001)


def test_faster_whisper_honors_cancellation_between_segments() -> None:
    with pytest.raises(ProcessCancelled):
        _transcribe_with_faster_whisper(
            FakeWhisperModel,
            Path("fixture.mp4"),
            "small",
            "en",
            lambda: True,
        )


def test_faster_whisper_wraps_provider_failures() -> None:
    class BrokenModel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("model unavailable")

    with pytest.raises(TranscriptionError, match="model unavailable"):
        _transcribe_with_faster_whisper(
            BrokenModel,
            Path("fixture.mp4"),
            "small",
            None,
            None,
        )
