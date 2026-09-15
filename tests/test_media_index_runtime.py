from pathlib import Path

import pytest

from nimbledesk.analysis.models import (
    AnalysisTrack,
    AssetRecord,
    ContentIndex,
    Provenance,
    RationalRange,
    TrackPoint,
)
from nimbledesk.media.models import MediaMetadata, TimelineEvent
from nimbledesk.protocol.models import SessionConfig, SessionState
from tests.test_runtime import make_runtime


def _write_index(path: Path) -> None:
    configuration_hash = "c" * 64
    index = ContentIndex(
        asset=AssetRecord(
            asset_id="asset-fixture",
            path=path.parent / "source.mp4",
            sha256="a" * 64,
            size_bytes=100,
            modified_ns=1,
            metadata=MediaMetadata(
                path=path.parent / "source.mp4",
                duration_seconds=120,
                width=1920,
                height=1080,
                frame_rate=30,
                has_audio=True,
                video_codec="h264",
                audio_codec="aac",
            ),
        ),
        tracks=(
            AnalysisTrack(
                name="semantic",
                kind="semantic",
                provenance=Provenance(
                    analyzer="fixture",
                    analyzer_version="1.0.0",
                    configuration_hash=configuration_hash,
                ),
                points=(
                    TrackPoint(
                        source_range=RationalRange.from_seconds(40, 5),
                        confidence=0.95,
                        labels=("clutch",),
                        text="one versus four comeback",
                        evidence=("four opponents eliminated",),
                    ),
                ),
            ),
        ),
        semantic_events=(
            TimelineEvent(
                time_seconds=43,
                event_type="clutch",
                label="One versus four clutch",
                importance=0.96,
                evidence=("round won after critical health",),
            ),
        ),
    )
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")


def test_session_scoped_media_search_and_detail_use_stable_bounded_ids(tmp_path: Path) -> None:
    index_path = tmp_path / "content_index.json"
    _write_index(index_path)
    runtime, _backend = make_runtime()
    session = runtime.start_session(
        "inspect media index", SessionConfig(granted_paths=(str(tmp_path),))
    )

    opened = runtime.open_content_index(session.session_id, index_path)
    search = runtime.search_content_index(
        session.session_id, str(opened["index_id"]), "clutch", 10, 2_000
    )
    result_id = str(search["results"][0]["result_id"])  # type: ignore[index]
    detail = runtime.content_index_detail(
        session.session_id, str(opened["index_id"]), result_id, 2_000
    )

    assert opened["asset_id"] == "asset-fixture"
    assert search["usage"]["estimated_text_tokens"] <= 2_000  # type: ignore[index]
    assert detail["detail"]["labels"] == ["clutch"]  # type: ignore[index]
    runtime.set_session_state(session.session_id, SessionState.STOPPED)
    with pytest.raises(RuntimeError, match="session is not active"):
        runtime.content_index_detail(
            session.session_id, str(opened["index_id"]), result_id, 2_000
        )


def test_media_index_must_be_inside_explicit_session_grants(tmp_path: Path) -> None:
    index_path = tmp_path / "content_index.json"
    _write_index(index_path)
    runtime, _backend = make_runtime()
    session = runtime.start_session("inspect media index", SessionConfig())

    with pytest.raises(ValueError, match="outside session grants"):
        runtime.open_content_index(session.session_id, index_path)
