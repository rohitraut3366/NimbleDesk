from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pytest import MonkeyPatch

from nimbledesk.media.ffmpeg import check_media_integrity, probe_media


def test_probe_media_preserves_editor_critical_metadata(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "portrait-hdr.mov"
    source.write_bytes(b"fixture")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "30/1",
                "time_base": "1/24000",
                "sample_aspect_ratio": "1:1",
                "pix_fmt": "yuv420p10le",
                "color_range": "tv",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "side_data_list": [{"rotation": -90}],
                "tags": {"timecode": "01:02:03:04"},
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 6,
                "channel_layout": "5.1",
                "sample_rate": "48000",
            },
        ],
        "format": {
            "duration": "12.5",
            "start_time": "0.25",
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "bit_rate": "25000000",
        },
    }
    monkeypatch.setattr("shutil.which", lambda _tool: "/usr/bin/tool")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )

    metadata = probe_media(source)

    assert (metadata.width, metadata.height) == (2160, 3840)
    assert metadata.rotation_degrees == 270
    assert metadata.variable_frame_rate
    assert metadata.hdr
    assert metadata.bit_depth == 10
    assert metadata.color_primaries == "bt2020"
    assert metadata.audio_channel_layout == "5.1"
    assert metadata.embedded_timecode == "01:02:03:04"
    assert metadata.bitrate == 25_000_000


def test_integrity_check_reports_decoder_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"fixture")
    metadata_payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 180,
                "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
            }
        ],
        "format": {"duration": "1", "format_name": "mov,mp4"},
    }
    monkeypatch.setattr("shutil.which", lambda _tool: "/usr/bin/tool")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(metadata_payload), stderr=""
        ),
    )
    metadata = probe_media(source)
    monkeypatch.setattr(
        "nimbledesk.media.ffmpeg.run_cancellable",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="invalid data found"
        ),
    )

    report = check_media_integrity(source, metadata, source_sha256="a" * 64)

    assert not report.valid
    assert report.error == "invalid data found"
    assert report.source_sha256 == "a" * 64
