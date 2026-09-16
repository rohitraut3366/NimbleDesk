from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil


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
        _verify_davinci_adapter(executable, root)
        _verify_studio_runtime(executable, root)

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


def _verify_studio_runtime(executable: Path, root: Path) -> None:
    runtime_directory = root / "studio-runtime"
    runtime_directory.mkdir()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = int(port_socket.getsockname()[1])
    process = subprocess.Popen(
        [str(executable), "start", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "NIMBLEDESK_BACKEND": "simulator",
            "NIMBLEDESK_RUNTIME_DIR": str(runtime_directory),
            "NIMBLEDESK_SAFETY_CONSOLE": "0",
        },
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        health: dict[str, object] | None = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                status, _headers, body = _studio_request(base_url + "/api/health")
                if status == 200:
                    health = json.loads(body)
                    break
            except (OSError, URLError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        if health is None:
            _raise_studio_failure(process, "did not become ready")
        if health.get("status") != "ok" or "simulator" not in str(health.get("backend")):
            raise RuntimeError("bundled Studio did not connect to its simulator daemon")

        status, headers, page = _studio_request(base_url + "/")
        if status != 200 or "NimbleDesk Studio" not in page:
            raise RuntimeError("bundled Studio did not serve its production page")
        if (
            headers.get("x-frame-options") != "DENY"
            or "frame-ancestors 'none'" not in headers.get("content-security-policy", "")
        ):
            raise RuntimeError("bundled Studio omitted required browser security headers")

        status, _headers, body = _studio_request(
            base_url + "/api/sessions",
            method="POST",
            payload={"reason": "Frozen bundle contract", "config": {"input_enabled": False}},
        )
        session = json.loads(body)
        session_id = session.get("session_id")
        if status != 201 or not isinstance(session_id, str):
            raise RuntimeError("bundled Studio could not create a desktop session")
        status, _headers, body = _studio_request(
            base_url + f"/api/sessions/{session_id}/paused", method="POST"
        )
        if status != 200 or json.loads(body).get("state") != "paused":
            raise RuntimeError("bundled Studio could not pause its desktop session")
        status, _headers, body = _studio_request(
            base_url + "/api/emergency-stop", method="POST"
        )
        stop_result = json.loads(body)
        if status != 200 or stop_result.get("stopped_sessions") != 1:
            raise RuntimeError("bundled Studio emergency stop did not stop its session")
        status, _headers, body = _studio_request(base_url + "/api/intelligence")
        capabilities = json.loads(body).get("capabilities", [])
        capability_names = {
            item.get("capability") for item in capabilities if isinstance(item, dict)
        }
        if status != 200 or capability_names != {
            "game_ocr",
            "transcription",
            "semantic_vision",
            "music",
            "sound",
        }:
            raise RuntimeError("bundled Studio omitted creative-intelligence readiness")
    finally:
        _stop_process_tree(process)
    if (runtime_directory / "connection.json").exists():
        raise RuntimeError("bundled Studio left stale daemon connection metadata")


def _verify_davinci_adapter(executable: Path, root: Path) -> None:
    output = root / "creation"
    plan_path = output / "edit_plan.json"
    timeline_path = output / "davinci_timeline.fcpxml"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    title = str(plan["brief"]["title"])
    caption_count = len(plan["captions"])
    fixture_state = root / "davinci-fixture-state.json"
    fixture_environment = {
        **os.environ,
        "RESOLVE_SCRIPT_API": str(Path(__file__).parent / "fixtures"),
        "NIMBLEDESK_DAVINCI_FIXTURE_STATE": str(fixture_state),
        "NIMBLEDESK_DAVINCI_FIXTURE_TITLE": title,
        "NIMBLEDESK_DAVINCI_FIXTURE_CAPTIONS": str(caption_count),
        "NIMBLEDESK_DAVINCI_FIXTURE_SOURCE": str(output / "final.mp4"),
    }
    first = _run_davinci_worker(
        executable,
        plan_path,
        timeline_path,
        root / "davinci-output",
        root / "davinci-first.json",
        root / "davinci-first.cancel",
        fixture_environment,
        render=True,
    )
    first_result = first.get("result")
    if (
        first.get("success") is not True
        or not isinstance(first_result, dict)
        or first_result.get("project_saved") is not True
        or first_result.get("timeline_reused") is not False
        or first_result.get("resolve_version") != "20.2.1-fixture"
        or not Path(str(first_result.get("render_path"))).is_file()
    ):
        raise RuntimeError("bundled DaVinci adapter failed its import, save, or render contract")

    reused = _run_davinci_worker(
        executable,
        plan_path,
        timeline_path,
        root / "davinci-output",
        root / "davinci-reused.json",
        root / "davinci-reused.cancel",
        fixture_environment,
        render=False,
    )
    reused_result = reused.get("result")
    if (
        reused.get("success") is not True
        or not isinstance(reused_result, dict)
        or reused_result.get("timeline_reused") is not True
    ):
        raise RuntimeError("bundled DaVinci adapter did not reuse an identical timeline")

    revision = json.loads(plan_path.read_text(encoding="utf-8"))
    revision_visual = revision["segments"][0]["visual"]
    revision_visual["saturation_multiplier"] = 1.05
    revision_visual["rationale"] = str(revision_visual["rationale"]) + "; fixture revision"
    revision_path = root / "davinci-revision-plan.json"
    revision_path.write_text(json.dumps(revision), encoding="utf-8")
    revised = _run_davinci_worker(
        executable,
        revision_path,
        timeline_path,
        root / "davinci-output",
        root / "davinci-revised.json",
        root / "davinci-revised.cancel",
        fixture_environment,
        render=False,
    )
    revised_result = revised.get("result")
    if (
        revised.get("success") is not True
        or not isinstance(revised_result, dict)
        or revised_result.get("timeline_reused") is not False
        or revised_result.get("plan_fingerprint") == first_result.get("plan_fingerprint")
    ):
        raise RuntimeError("bundled DaVinci adapter did not create a revised timeline")

    cancel_path = root / "davinci-cancel.cancel"
    cancel_path.touch()
    cancellation_environment = {
        **fixture_environment,
        "NIMBLEDESK_DAVINCI_FIXTURE_RENDERING": "1",
    }
    cancelled = _run_davinci_worker(
        executable,
        revision_path,
        timeline_path,
        root / "davinci-output",
        root / "davinci-cancelled.json",
        cancel_path,
        cancellation_environment,
        render=True,
        expected_return_code=1,
    )
    state = json.loads(fixture_state.read_text(encoding="utf-8"))
    if (
        cancelled.get("success") is not False
        or "cancelled" not in str(cancelled.get("error", ""))
        or state.get("stopped") is not True
    ):
        raise RuntimeError("bundled DaVinci adapter did not stop a cancelled render")


def _run_davinci_worker(
    executable: Path,
    plan_path: Path,
    timeline_path: Path,
    output_directory: Path,
    result_path: Path,
    cancel_path: Path,
    environment: dict[str, str],
    *,
    render: bool,
    expected_return_code: int = 0,
) -> dict[str, object]:
    completed = subprocess.run(
        [
            str(executable),
            "davinci-worker",
            str(plan_path),
            str(timeline_path),
            str(output_directory),
            "1" if render else "0",
            "30",
            str(result_path),
            str(cancel_path),
        ],
        capture_output=True,
        check=False,
        env=environment,
        timeout=60,
    )
    if completed.returncode != expected_return_code or not result_path.is_file():
        detail = completed.stderr.decode("utf-8", errors="replace")[:8_192]
        raise RuntimeError(f"bundled DaVinci worker failed: {detail}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("bundled DaVinci worker returned a non-object response")
    return payload


def _studio_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urlopen(request, timeout=5) as response:
        return (
            int(response.status),
            {key.casefold(): value for key, value in response.headers.items()},
            response.read().decode("utf-8"),
        )


def _raise_studio_failure(process: subprocess.Popen[bytes], reason: str) -> None:
    if process.poll() is None:
        _stop_process_tree(process)
    stdout, stderr = process.communicate(timeout=5)
    detail = (stderr or stdout).decode("utf-8", errors="replace")[:8_192]
    raise RuntimeError(f"bundled Studio {reason}: {detail}")


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for child in children:
        with suppress(psutil.Error):
            child.terminate()
    _gone, alive = psutil.wait_procs(children, timeout=5)
    for child in alive:
        with suppress(psutil.Error):
            child.kill()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify internal standalone worker routes")
    parser.add_argument("executable", type=Path)
    verify_bundle(parser.parse_args().executable)
