from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from nimbledesk.creative.models import EditPlan
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.media.models import MediaMetadata
from nimbledesk.media.process import run_cancellable


class VerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Literal["warning", "blocking"]
    code: str
    message: str


class RenderVerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verification_version: str = "1.0.0"
    valid: bool
    media: MediaMetadata
    checks: dict[str, float | int | bool | str]
    issues: tuple[VerificationIssue, ...]


class RenderVerificationError(RuntimeError):
    def __init__(self, report: RenderVerificationReport) -> None:
        self.report = report
        super().__init__(
            "; ".join(issue.message for issue in report.issues if issue.severity == "blocking")
            or "render verification failed"
        )


def verify_render(
    plan: EditPlan,
    render_path: Path,
    report_path: Path,
    cancelled: Callable[[], bool] | None = None,
) -> RenderVerificationReport:
    media = probe_media(render_path)
    issues: list[VerificationIssue] = []
    checks: dict[str, float | int | bool | str] = {}
    duration_tolerance = max(0.12, 2 / plan.delivery.frame_rate)
    duration_error = abs(media.duration_seconds - plan.duration_seconds)
    checks["duration_error_seconds"] = round(duration_error, 4)
    checks["resolution_matches"] = (
        media.width == plan.delivery.width and media.height == plan.delivery.height
    )
    checks["frame_rate_error"] = round(abs(media.frame_rate - plan.delivery.frame_rate), 4)
    checks["has_audio"] = media.has_audio
    if duration_error > duration_tolerance:
        issues.append(_blocking("duration_mismatch", "Rendered duration does not match the plan"))
    if not checks["resolution_matches"]:
        issues.append(
            _blocking("resolution_mismatch", "Rendered resolution does not match delivery")
        )
    if abs(media.frame_rate - plan.delivery.frame_rate) > 0.1:
        issues.append(
            _blocking("frame_rate_mismatch", "Rendered frame rate does not match delivery")
        )
    if not media.has_audio:
        issues.append(_blocking("missing_audio", "Rendered output has no audio stream"))

    video_scan = run_cancellable(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-i",
            str(render_path),
            "-an",
            "-vf",
            "blackdetect=d=0.3:pix_th=0.10,freezedetect=n=-50dB:d=1",
            "-f",
            "null",
            "-",
        ],
        cancelled=cancelled,
    )
    checks["video_scan_return_code"] = video_scan.returncode
    if video_scan.returncode != 0:
        issues.append(
            _blocking(
                "video_decode_failed",
                "FFmpeg could not decode the complete rendered video stream",
            )
        )
    scan_text = video_scan.stderr
    black_durations = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", scan_text)]
    freeze_durations = [
        float(value) for value in re.findall(r"freeze_duration: ([0-9.]+)", scan_text)
    ]
    black_seconds = sum(black_durations)
    freeze_seconds = sum(freeze_durations)
    checks["black_seconds"] = round(black_seconds, 3)
    checks["freeze_seconds"] = round(freeze_seconds, 3)
    if black_seconds > max(0.5, plan.duration_seconds * 0.2):
        issues.append(
            _blocking("excessive_black", "Rendered output contains excessive black video")
        )
    elif black_seconds > 0.3:
        issues.append(_warning("black_frames", "Rendered output contains a detectable black range"))
    if freeze_seconds > max(1.5, plan.duration_seconds * 0.25):
        issues.append(_warning("frozen_video", "Rendered output contains a long frozen range"))

    if media.has_audio:
        audio_scan = run_cancellable(
            [
                "ffmpeg",
                "-hide_banner",
                "-v",
                "info",
                "-i",
                str(render_path),
                "-vn",
                "-af",
                "silencedetect=n=-50dB:d=2,ebur128=peak=true,volumedetect",
                "-f",
                "null",
                "-",
            ],
            cancelled=cancelled,
        )
        checks["audio_scan_return_code"] = audio_scan.returncode
        if audio_scan.returncode != 0:
            issues.append(
                _blocking(
                    "audio_decode_failed",
                    "FFmpeg could not decode the complete rendered audio stream",
                )
            )
        silence_durations = [
            float(value)
            for value in re.findall(r"silence_duration: ([0-9.]+)", audio_scan.stderr)
        ]
        maximum_volume = _last_float(r"max_volume: (-?[0-9.]+) dB", audio_scan.stderr)
        integrated_loudness = _last_float(r"I:\s+(-?[0-9.]+) LUFS", audio_scan.stderr)
        true_peak = _last_float(r"Peak:\s+(-?[0-9.]+) dBFS", audio_scan.stderr)
        checks["silence_seconds"] = round(sum(silence_durations), 3)
        checks["maximum_volume_db"] = maximum_volume if maximum_volume is not None else "unknown"
        checks["integrated_loudness_lufs"] = (
            integrated_loudness if integrated_loudness is not None else "unknown"
        )
        checks["true_peak_dbfs"] = true_peak if true_peak is not None else "unknown"
        if maximum_volume is not None and maximum_volume > -0.1:
            issues.append(_blocking("audio_peak", "Rendered audio reaches an unsafe peak"))
        if true_peak is not None and true_peak > -0.1:
            issues.append(_blocking("audio_true_peak", "Rendered audio true peak is unsafe"))
        if (
            integrated_loudness is not None
            and abs(integrated_loudness - plan.delivery.audio_loudness_lufs) > 2.5
        ):
            issues.append(
                _warning(
                    "loudness_target",
                    "Rendered integrated loudness differs from the delivery target",
                )
            )
        if sum(silence_durations) > plan.duration_seconds * 0.8:
            issues.append(_warning("mostly_silent", "Rendered output is mostly silent"))

    unreadable = [
        cue
        for cue in plan.captions
        if any(len(line) > 42 for line in cue.text.splitlines())
        or len(cue.text.replace("\n", " ")) / cue.timeline_range.duration_seconds > 22
    ]
    checks["caption_cues"] = len(plan.captions)
    checks["caption_readability_passed"] = not unreadable
    if unreadable:
        issues.append(_blocking("caption_readability", "Caption reading limits are exceeded"))
    caption_artifact = render_path.with_suffix(".srt")
    checks["caption_artifact_present"] = not plan.captions or caption_artifact.is_file()
    if plan.captions and not caption_artifact.is_file():
        issues.append(
            _blocking(
                "captions_not_executed",
                "The plan contains captions but no executed caption artifact was found",
            )
        )

    graphics = render_path.parent / "graphics"
    unsafe_graphics = _unsafe_graphics(graphics, plan.delivery.width, plan.delivery.height)
    checks["graphics_checked"] = len(tuple(graphics.glob("*.png"))) if graphics.is_dir() else 0
    checks["safe_area_passed"] = not unsafe_graphics
    if unsafe_graphics:
        issues.append(
            _blocking(
                "graphic_safe_area",
                "Graphic content crosses the five-percent delivery safe area: "
                + ", ".join(path.name for path in unsafe_graphics),
            )
        )
    _write_diagnostic_artifacts(
        render_path,
        media.duration_seconds,
        media.has_audio,
        checks,
        issues,
        cancelled,
    )
    report = RenderVerificationReport(
        valid=not any(issue.severity == "blocking" for issue in issues),
        media=media,
        checks=checks,
        issues=tuple(issues),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _unsafe_graphics(directory: Path, width: int, height: int) -> tuple[Path, ...]:
    if not directory.is_dir():
        return ()
    safe_left = width * 0.05
    safe_top = height * 0.05
    safe_right = width * 0.95
    safe_bottom = height * 0.95
    unsafe: list[Path] = []
    for path in directory.glob("*.png"):
        with Image.open(path) as image:
            alpha = image.convert("RGBA").getchannel("A")
            bounds = alpha.getbbox()
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        if left < safe_left or top < safe_top or right > safe_right or bottom > safe_bottom:
            unsafe.append(path)
    return tuple(unsafe)


def _last_float(pattern: str, text: str) -> float | None:
    matches = re.findall(pattern, text)
    return float(matches[-1]) if matches else None


def _write_diagnostic_artifacts(
    render_path: Path,
    duration_seconds: float,
    has_audio: bool,
    checks: dict[str, float | int | bool | str],
    issues: list[VerificationIssue],
    cancelled: Callable[[], bool] | None,
) -> None:
    contact_sheet = render_path.parent / "verification-contact-sheet.png"
    contact_scan = run_cancellable(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(render_path),
            "-vf",
            f"fps=12/{duration_seconds:.6f},scale=320:-2,tile=4x3",
            "-frames:v",
            "1",
            str(contact_sheet),
        ],
        cancelled=cancelled,
    )
    checks["contact_sheet_path"] = str(contact_sheet) if contact_sheet.is_file() else "unavailable"
    if contact_scan.returncode != 0:
        issues.append(
            _warning(
                "contact_sheet_failed", "Could not create verification contact sheet"
            )
        )
    if not has_audio:
        return
    waveform = render_path.parent / "verification-waveform.png"
    waveform_scan = run_cancellable(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(render_path),
            "-filter_complex",
            "showwavespic=s=1200x240:colors=0x71e5b4",
            "-frames:v",
            "1",
            str(waveform),
        ],
        cancelled=cancelled,
    )
    checks["waveform_path"] = str(waveform) if waveform.is_file() else "unavailable"
    if waveform_scan.returncode != 0:
        issues.append(_warning("waveform_failed", "Could not create verification waveform"))


def _blocking(code: str, message: str) -> VerificationIssue:
    return VerificationIssue(severity="blocking", code=code, message=message)


def _warning(code: str, message: str) -> VerificationIssue:
    return VerificationIssue(severity="warning", code=code, message=message)
