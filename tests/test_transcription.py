import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nimbledesk.creative.transcription import (
    TranscriptionError,
    TranscriptionProviderConfig,
    _transcribe_with_faster_whisper,
    transcribe_with_provider,
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


def test_model_agnostic_provider_produces_provenanced_transcript(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"media")
    worker = tmp_path / "provider.py"
    worker.write_text(
        "import json, sys\n"
        "request=json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "assert request['source_name']=='fixture.mp4' and request['language']=='en'\n"
        "json.dump({'segments':[{'source_range':{'start_seconds':0.5,'end_seconds':2.0},"
        "'text':'Provider transcript','confidence':0.96}], 'detected_language':'en',"
        "'usage':{'audio_seconds':2.0,'provider_input_tokens':40,'provider_output_tokens':7}},"
        "open(sys.argv[2], 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    config = tmp_path / "provider.json"
    config.write_text(
        TranscriptionProviderConfig(
            provider_id="fixture-transcriber",
            model="fixture-model",
            command=(sys.executable, str(worker), "{request}", "{response}"),
            code_paths=(worker,),
        ).model_dump_json(),
        encoding="utf-8",
    )

    segments, analysis_path = transcribe_with_provider(
        source, tmp_path / "analysis", config, "en"
    )

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert segments[0].text == "Provider transcript"
    assert analysis["provider_id"] == "fixture-transcriber"
    assert analysis["segment_count"] == 1
    assert analysis["usage"]["provider_input_tokens"] == 40


def test_provider_requires_request_and_response_placeholders(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"media")
    config = tmp_path / "provider.json"
    config.write_text(
        TranscriptionProviderConfig(
            provider_id="broken-provider", model="fixture", command=("provider",)
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(TranscriptionError, match="must contain"):
        transcribe_with_provider(source, tmp_path / "analysis", config)
