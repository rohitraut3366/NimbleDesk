from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nimbledesk.creative.music import load_music_catalog
from nimbledesk.creative.music_index import index_music_directory, write_music_catalog


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_music_index_measures_audio_and_writes_licensed_catalog(tmp_path: Path) -> None:
    music_directory = tmp_path / "music"
    music_directory.mkdir()
    track = music_directory / "action-bed.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            str(track),
        ],
        check=True,
    )

    assets = index_music_directory(
        music_directory,
        license_terms="user-owned worldwide social usage",
        declared_moods=("exciting",),
    )
    catalog = tmp_path / "music.json"
    write_music_catalog(assets, catalog)

    assert len(assets) == 1
    assert assets[0].duration_seconds == pytest.approx(2, abs=0.1)
    assert assets[0].energy > 0
    assert assets[0].mood[0] == "exciting"
    assert assets[0].license == "user-owned worldwide social usage"
    assert load_music_catalog(catalog)[0].path == track.resolve()


def test_music_catalog_resolves_paths_relative_to_catalog(tmp_path: Path) -> None:
    track = tmp_path / "track.wav"
    track.write_bytes(b"fixture")
    catalog = tmp_path / "music.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "path": "track.wav",
                    "duration_seconds": 10,
                    "title": "Track",
                    "energy": 0.5,
                    "instrumental": True,
                    "license": "test license",
                }
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_music_catalog(catalog)

    assert loaded[0].path == track.resolve()
