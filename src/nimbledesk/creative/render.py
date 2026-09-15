from __future__ import annotations

import subprocess
from pathlib import Path

from nimbledesk.creative.models import CaptionCue, EditPlan
from nimbledesk.media.ffmpeg import MediaToolError, probe_media, require_media_tools


def render_edit_plan(plan: EditPlan, output_path: Path) -> Path:
    if not plan.segments:
        raise ValueError("cannot render an edit plan without segments")
    require_media_tools()
    metadata = probe_media(plan.source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, segment in enumerate(plan.segments):
        source = segment.source_range
        rate = segment.speed.rate
        color_filter = _color_filter(
            segment.visual.color_look,
            segment.visual.exposure_adjustment_stops,
            segment.visual.saturation_multiplier,
        )
        video_filter = (
            f"[0:v]trim=start={source.start_seconds:.3f}:end={source.end_seconds:.3f},"
            f"setpts=(PTS-STARTPTS)/{rate:.6f},"
            f"scale={plan.delivery.width}:{plan.delivery.height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={plan.delivery.width}:{plan.delivery.height},"
            f"{color_filter},format=yuv420p[v{index}]"
        )
        filters.append(video_filter)
        if metadata.has_audio:
            audio_filter = (
                f"[0:a]atrim=start={source.start_seconds:.3f}:end={source.end_seconds:.3f},"
                f"asetpts=PTS-STARTPTS,atempo={rate:.6f}[a{index}]"
            )
        else:
            audio_filter = (
                "anullsrc=r=48000:cl=stereo,"
                f"atrim=duration={segment.timeline_duration_seconds:.3f}[a{index}]"
            )
        filters.append(audio_filter)
        concat_inputs.append(f"[v{index}][a{index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(plan.segments)}:v=1:a=1[vbase][abase]"
    )

    command = ["ffmpeg", "-y", "-v", "error", "-i", str(plan.source_path)]
    audio_output = "[abase]"
    if plan.music_cue:
        music = plan.music_cue
        command.extend(["-stream_loop", "-1", "-i", str(music.asset.path)])
        gain = 10 ** (music.gain_db / 20)
        filters.append(
            f"[1:a]atrim=start={music.source_range.start_seconds:.3f}:"
            f"duration={plan.duration_seconds:.3f},asetpts=PTS-STARTPTS,"
            f"volume={gain:.6f}[music]"
        )
        filters.append("[abase][music]amix=inputs=2:duration=first:dropout_transition=2[aout]")
        audio_output = "[aout]"
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vbase]",
            "-map",
            audio_output,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-t",
            f"{plan.duration_seconds:.3f}",
            str(output_path),
        ]
    )
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or "creative render failed")
    probe_media(output_path)
    if plan.captions:
        write_srt(plan.captions, output_path.with_suffix(".srt"))
    return output_path


def write_srt(cues: tuple[CaptionCue, ...], output_path: Path) -> None:
    blocks = [
        f"{index}\n{_srt_time(cue.timeline_range.start_seconds)} --> "
        f"{_srt_time(cue.timeline_range.end_seconds)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ]
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _color_filter(look: str, exposure_stops: float, saturation_multiplier: float) -> str:
    normalized = look.casefold()
    brightness = max(-0.12, min(0.12, exposure_stops * 0.08))
    if normalized in {"vivid", "high_contrast"}:
        contrast, saturation = 1.10, 1.12
    elif normalized in {"cinematic", "moody"}:
        contrast, saturation = 1.08, 0.92
    elif normalized in {"flat", "neutral"}:
        contrast, saturation = 1.0, 1.0
    else:
        contrast, saturation = 1.04, 1.04
    saturation *= saturation_multiplier
    return (
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:"
        f"saturation={saturation:.4f}"
    )


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remaining = divmod(milliseconds, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    whole_seconds, milliseconds = divmod(remaining, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"
