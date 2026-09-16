from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def verify_bundle(executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="nimbledesk-bundle-contract-") as temporary:
        root = Path(temporary)
        request = root / "adapter-request.json"
        request.write_text(
            json.dumps(
                {
                    "command": "inspect",
                    "arguments": {"project": "bundle-contract"},
                }
            ),
            encoding="utf-8",
        )
        adapter_package = root / "adapter-package"
        adapter_package.mkdir()
        (adapter_package / "bundle_external.py").write_text(
            "def handle(command, arguments):\n"
            "    return {'command': command, 'value': arguments['project']}\n",
            encoding="utf-8",
        )
        adapter = subprocess.run(
            [
                str(executable),
                "adapter-worker",
                "bundle_external:handle",
            ],
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "NIMBLEDESK_ADAPTER_REQUEST": str(request),
                "NIMBLEDESK_ADAPTER_PACKAGE": str(adapter_package),
            },
            timeout=60,
        )
        if adapter.returncode != 0:
            raise RuntimeError(
                "bundled adapter worker failed: "
                + adapter.stderr.decode("utf-8", errors="replace")[:8_192]
            )
        payload = json.loads(adapter.stdout)
        if payload.get("success") is not True or payload.get("result") != {
            "command": "inspect",
            "value": "bundle-contract",
        }:
            raise RuntimeError("bundled adapter worker returned an invalid contract result")

        davinci_result = root / "davinci-result.json"
        davinci = subprocess.run(
            [
                str(executable),
                "davinci-worker",
                str(root / "missing-plan.json"),
                str(root / "missing-timeline.fcpxml"),
                str(root / "output"),
                "0",
                "1",
                str(davinci_result),
                str(root / "cancel"),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if davinci.returncode != 1 or not davinci_result.is_file():
            raise RuntimeError("bundled DaVinci worker did not return its structured failure")
        davinci_payload = json.loads(davinci_result.read_text(encoding="utf-8"))
        if (
            davinci_payload.get("success") is not False
            or "missing-plan.json" not in str(davinci_payload.get("error", ""))
        ):
            raise RuntimeError("bundled DaVinci worker returned an invalid failure contract")

        _verify_media_creation(executable, root)

        isolation_report = root / "adapter-isolation-contract.json"
        isolation_contract = subprocess.run(
            [
                str(executable),
                "adapter-isolation-contract",
                "--target-id",
                f"bundle-{platform.system().lower()}-{platform.machine().lower()}",
                "--output",
                str(isolation_report),
            ],
            capture_output=True,
            check=False,
            timeout=180,
        )
        if isolation_contract.returncode != 0 or not isolation_report.is_file():
            raise RuntimeError(
                "bundled adapter isolation contract failed: "
                + isolation_contract.stderr.decode("utf-8", errors="replace")[:8_192]
            )
        if json.loads(isolation_report.read_text(encoding="utf-8")).get("passed") is not True:
            raise RuntimeError("bundled cross-platform adapter isolation did not pass")

        if platform.system() == "Windows":
            windows_report = root / "windows-adapter-contract.json"
            windows_contract = subprocess.run(
                [
                    str(executable),
                    "windows-adapter-contract",
                    "--output",
                    str(windows_report),
                ],
                capture_output=True,
                check=False,
                timeout=120,
            )
            if windows_contract.returncode != 0 or not windows_report.is_file():
                raise RuntimeError(
                    "bundled Windows adapter contract failed: "
                    + windows_contract.stderr.decode("utf-8", errors="replace")[:8_192]
                )
            if json.loads(windows_report.read_text(encoding="utf-8")).get("passed") is not True:
                raise RuntimeError("bundled Windows adapter isolation did not pass")


def _verify_media_creation(executable: Path, root: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required to verify the bundled creative workflow")
    source = root / "source.mp4"
    music = root / "music.wav"
    sound = root / "whoosh.wav"
    fixture_commands = (
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            str(music),
        ],
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=0.5",
            str(sound),
        ],
    )
    for command in fixture_commands:
        subprocess.run(command, capture_output=True, check=True, timeout=60)

    transcript = root / "transcript.json"
    transcript.write_text(
        json.dumps(
            [
                {
                    "source_range": {"start_seconds": 0, "end_seconds": 2},
                    "text": "We survived the impossible final fight.",
                    "confidence": 0.98,
                    "speaker": "Player",
                },
                {
                    "source_range": {"start_seconds": 2, "end_seconds": 4},
                    "text": "That grenade ended the round. What a win!",
                    "confidence": 0.97,
                    "speaker": "Player",
                },
            ]
        ),
        encoding="utf-8",
    )
    events = root / "events.json"
    events.write_text(
        json.dumps(
            [
                {
                    "time_seconds": 1,
                    "event_type": "kill",
                    "label": "Confirmed elimination",
                    "importance": 0.85,
                },
                {
                    "time_seconds": 3,
                    "event_type": "grenade_kill",
                    "label": "Grenade round winner",
                    "importance": 1.0,
                },
            ]
        ),
        encoding="utf-8",
    )
    music_catalog = root / "music.json"
    music_catalog.write_text(
        json.dumps(
            [
                {
                    "path": str(music),
                    "duration_seconds": 4,
                    "title": "Fixture action bed",
                    "mood": ["exciting", "engaging"],
                    "bpm": 120,
                    "energy": 0.8,
                    "instrumental": True,
                    "license": "owned test fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    sound_catalog = root / "sounds.json"
    sound_catalog.write_text(
        json.dumps(
            [
                {
                    "path": str(sound),
                    "duration_seconds": 0.5,
                    "title": "Fixture whoosh",
                    "tags": ["whoosh", "hook", "kill", "grenade"],
                    "license": "owned test fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = root / "creation"
    creation = subprocess.run(
        [
            str(executable),
            "create",
            str(source),
            str(output),
            "--title",
            "Frozen bundle creation",
            "--content-kind",
            "gameplay",
            "--duration",
            "5",
            "--aspect-ratio",
            "9:16",
            "--pace",
            "fast",
            "--mood",
            "exciting",
            "--clip-count",
            "1",
            "--events",
            str(events),
            "--transcript",
            str(transcript),
            "--music-catalog",
            str(music_catalog),
            "--sound-catalog",
            str(sound_catalog),
        ],
        capture_output=True,
        check=False,
        timeout=180,
    )
    if creation.returncode != 0:
        raise RuntimeError(
            "bundled creative workflow failed: "
            + creation.stderr.decode("utf-8", errors="replace")[:8_192]
        )
    required_files = (
        "final.mp4",
        "final.srt",
        "edit_plan.json",
        "validation.json",
        "render_verification.json",
        "davinci_timeline.fcpxml",
        "cue_sheet.json",
        "cue_sheet.csv",
        "detected_events.json",
        "transcript.json",
    )
    missing = [name for name in required_files if not (output / name).is_file()]
    if missing:
        raise RuntimeError("bundled creative workflow omitted: " + ", ".join(missing))
    verification = json.loads((output / "render_verification.json").read_text(encoding="utf-8"))
    plan = json.loads((output / "edit_plan.json").read_text(encoding="utf-8"))
    if verification.get("valid") is not True:
        raise RuntimeError("bundled creative render did not pass verification")
    if not plan.get("captions") or not plan.get("music_cue") or not plan.get("sound_cues"):
        raise RuntimeError("bundled creative plan omitted captions, music, or sound design")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify internal standalone worker routes")
    parser.add_argument("executable", type=Path)
    verify_bundle(parser.parse_args().executable)
