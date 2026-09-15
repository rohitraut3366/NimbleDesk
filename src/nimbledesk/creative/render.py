from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from nimbledesk.creative.graphics import GraphicKind, write_text_graphic
from nimbledesk.creative.models import CaptionCue, EditPlan
from nimbledesk.media.ffmpeg import MediaToolError, probe_media, require_media_tools
from nimbledesk.media.process import run_cancellable


def render_edit_plan(
    plan: EditPlan,
    output_path: Path,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    if not plan.segments:
        raise ValueError("cannot render an edit plan without segments")
    require_media_tools()
    metadata = probe_media(plan.source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-v", "error", "-i", str(plan.source_path)]
    next_input = 1
    filters: list[str] = []
    for index, segment in enumerate(plan.segments):
        source = segment.source_range
        rate = segment.speed.rate
        color_filter = _color_filter(
            segment.visual.color_look,
            segment.visual.exposure_adjustment_stops,
            segment.visual.saturation_multiplier,
        )
        video_steps = [
            f"[0:v]trim=start={source.start_seconds:.3f}:end={source.end_seconds:.3f}",
            f"setpts=(PTS-STARTPTS)/{rate:.6f}",
        ]
        if segment.speed.interpolation == "frame_blend":
            video_steps.append(
                f"minterpolate=fps={plan.delivery.frame_rate:.6f}:mi_mode=blend"
            )
        elif segment.speed.interpolation == "optical_flow":
            video_steps.append(
                f"minterpolate=fps={plan.delivery.frame_rate:.6f}:"
                "mi_mode=mci:mc_mode=aobmc:me_mode=bidir"
            )
        video_steps.extend(
            (
                f"scale={plan.delivery.width}:{plan.delivery.height}:"
                "force_original_aspect_ratio=increase",
                f"crop={plan.delivery.width}:{plan.delivery.height}:"
                f"x=(in_w-out_w)*{segment.visual.reframe_center_x:.6f}:"
                f"y=(in_h-out_h)*{segment.visual.reframe_center_y:.6f}",
                color_filter,
            )
        )
        if segment.visual.punch_in_scale > 1:
            scale = segment.visual.punch_in_scale
            ramp_frames = max(1, round(plan.delivery.frame_rate * 0.35))
            video_steps.append(
                f"zoompan=z='1+({scale:.6f}-1)*min(on/{ramp_frames},1)':"
                "x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:"
                f"s={plan.delivery.width}x{plan.delivery.height}:"
                f"fps={plan.delivery.frame_rate:.6f}"
            )
        if segment.visual.transition_in == "dip_to_black":
            video_steps.append("fade=t=in:st=0:d=0.25:color=black")
        graphic_specs: list[tuple[GraphicKind, str, float, float]] = []
        if segment.visual.title:
            graphic_specs.append(
                (
                    "title",
                    segment.visual.title,
                    0,
                    min(3.0, segment.timeline_duration_seconds),
                )
            )
        if segment.visual.lower_third:
            graphic_specs.append(
                (
                    "lower_third",
                    segment.visual.lower_third,
                    min(0.5, segment.timeline_duration_seconds * 0.1),
                    min(4.5, segment.timeline_duration_seconds),
                )
            )
        raw_video_label = f"vraw{index}" if graphic_specs else f"v{index}"
        video_steps.extend(
            (f"fps={plan.delivery.frame_rate:.6f}", f"format=yuv420p[{raw_video_label}]")
        )
        video_filter = ",".join(video_steps)
        filters.append(video_filter)
        previous_label = raw_video_label
        for graphic_index, (kind, text, start, end) in enumerate(graphic_specs):
            graphic_path = (
                output_path.parent
                / "graphics"
                / f"{segment.segment_id}-{kind.replace('_', '-')}.png"
            )
            write_text_graphic(
                text,
                kind,
                plan.delivery.width,
                plan.delivery.height,
                graphic_path,
            )
            command.extend(["-loop", "1", "-i", str(graphic_path)])
            graphic_label = f"graphic{index}_{graphic_index}"
            output_label = (
                f"v{index}"
                if graphic_index == len(graphic_specs) - 1
                else f"voverlay{index}_{graphic_index}"
            )
            filters.append(f"[{next_input}:v]format=rgba[{graphic_label}]")
            filters.append(
                f"[{previous_label}][{graphic_label}]overlay=0:0:"
                f"enable='between(t,{start:.3f},{end:.3f})'[{output_label}]"
            )
            previous_label = output_label
            next_input += 1
        if metadata.has_audio:
            audio_filter = (
                f"[0:a]atrim=start={source.start_seconds:.3f}:end={source.end_seconds:.3f},"
                f"asetpts=PTS-STARTPTS,{_atempo_filter(rate)}[a{index}]"
            )
        else:
            audio_filter = (
                "anullsrc=r=48000:cl=stereo,"
                f"atrim=duration={segment.timeline_duration_seconds:.3f}[a{index}]"
            )
        filters.append(audio_filter)
    _compile_timeline(plan, filters)

    video_output = "[vbase]"
    if plan.captions:
        subtitle_path = output_path.with_suffix(".srt")
        write_srt(plan.captions, subtitle_path)
        if _ffmpeg_supports_filter("subtitles"):
            filters.append(
                f"[vbase]{_subtitle_filter(subtitle_path, plan.delivery.height)}[vfinal]"
            )
            video_output = "[vfinal]"
        else:
            previous_label = "vbase"
            for index, caption_cue in enumerate(plan.captions):
                graphic_path = output_path.parent / "graphics" / f"caption-{index + 1:04d}.png"
                write_text_graphic(
                    caption_cue.text,
                    "caption",
                    plan.delivery.width,
                    plan.delivery.height,
                    graphic_path,
                )
                command.extend(["-loop", "1", "-i", str(graphic_path)])
                graphic_label = f"caption{index}"
                output_label = f"vcaption{index}"
                filters.append(f"[{next_input}:v]format=rgba[{graphic_label}]")
                filters.append(
                    f"[{previous_label}][{graphic_label}]overlay=0:0:enable='between(t,"
                    f"{caption_cue.timeline_range.start_seconds:.3f},"
                    f"{caption_cue.timeline_range.end_seconds:.3f})'[{output_label}]"
                )
                previous_label = output_label
                next_input += 1
            video_output = f"[{previous_label}]"

    audio_output = "[abase]"
    music_labels: list[str] = []
    for music_index, music in enumerate(plan.all_music_cues):
        command.extend(["-stream_loop", "-1", "-i", str(music.asset.path)])
        gain = 10 ** (music.gain_db / 20)
        delay = round(music.timeline_range.start_seconds * 1000)
        label = f"music{music_index}"
        filters.append(
            f"[{next_input}:a]atrim=start={music.source_range.start_seconds:.3f}:"
            f"duration={music.timeline_range.duration_seconds:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.25,afade=t=out:st="
            f"{max(0, music.timeline_range.duration_seconds - 0.35):.3f}:d=0.35,"
            f"volume={gain:.6f},adelay={delay}|{delay}[{label}]"
        )
        music_labels.append(f"[{label}]")
        next_input += 1
    if music_labels:
        filters.append(
            "".join(music_labels)
            + f"amix=inputs={len(music_labels)}:duration=longest:normalize=0[music]"
        )
        ducking_db = min(cue.duck_under_dialogue_db for cue in plan.all_music_cues)
        ducking_ratio = max(2, min(20, abs(ducking_db) / 2))
        filters.append("[abase]asplit=2[base_mix][dialogue_sidechain]")
        filters.append(
            "[music][dialogue_sidechain]sidechaincompress="
            f"threshold=0.025:ratio={ducking_ratio:.2f}:attack=20:release=500[ducked_music]"
        )
        filters.append(
            "[base_mix][ducked_music]amix=inputs=2:duration=first:"
            "dropout_transition=2[aout]"
        )
        audio_output = "[aout]"
    sound_labels: list[str] = []
    for index, cue in enumerate(plan.sound_cues):
        command.extend(["-i", str(cue.asset.path)])
        gain = 10 ** (cue.gain_db / 20)
        delay = round(cue.timeline_range.start_seconds * 1000)
        label = f"sound{index}"
        filters.append(
            f"[{next_input}:a]atrim=start={cue.source_range.start_seconds:.3f}:"
            f"duration={cue.timeline_range.duration_seconds:.3f},asetpts=PTS-STARTPTS,"
            f"volume={gain:.6f},adelay={delay}|{delay}[{label}]"
        )
        sound_labels.append(f"[{label}]")
        next_input += 1
    if sound_labels:
        filters.append(
            audio_output
            + "".join(sound_labels)
            + f"amix=inputs={len(sound_labels) + 1}:duration=first:normalize=0[afinal]"
        )
        audio_output = "[afinal]"
    filters.append(
        f"{audio_output}loudnorm=I={plan.delivery.audio_loudness_lufs:.1f}:"
        "LRA=11:TP=-1.5,alimiter=limit=0.95:attack=5:release=50[amaster]"
    )
    audio_output = "[amaster]"
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            video_output,
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
    completed = run_cancellable(command, cancelled=cancelled)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or "creative render failed")
    probe_media(output_path)
    return output_path


def write_srt(cues: tuple[CaptionCue, ...], output_path: Path) -> None:
    blocks = [
        f"{index}\n{_srt_time(cue.timeline_range.start_seconds)} --> "
        f"{_srt_time(cue.timeline_range.end_seconds)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ]
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _compile_timeline(plan: EditPlan, filters: list[str]) -> None:
    video_label = "v0"
    audio_label = "a0"
    compiled_duration = plan.segments[0].timeline_duration_seconds
    for index, segment in enumerate(plan.segments[1:], start=1):
        next_video = f"v{index}"
        next_audio = f"a{index}"
        output_video = f"vchain{index}"
        output_audio = f"achain{index}"
        if segment.visual.transition_in == "cross_dissolve":
            duration = segment.visual.transition_duration_seconds
            offset = compiled_duration - duration
            filters.append(
                f"[{video_label}][{next_video}]xfade=transition=fade:"
                f"duration={duration:.3f}:offset={offset:.3f}[{output_video}]"
            )
            filters.append(
                f"[{audio_label}][{next_audio}]acrossfade=d={duration:.3f}:"
                f"c1=tri:c2=tri[{output_audio}]"
            )
            compiled_duration += segment.timeline_duration_seconds - duration
        else:
            filters.append(
                f"[{video_label}][{audio_label}][{next_video}][{next_audio}]"
                f"concat=n=2:v=1:a=1[{output_video}][{output_audio}]"
            )
            compiled_duration += segment.timeline_duration_seconds
        video_label = output_video
        audio_label = output_audio
    filters.append(f"[{video_label}]null[vbase]")
    filters.append(f"[{audio_label}]anull[abase]")


def _subtitle_filter(subtitle_path: Path, frame_height: int) -> str:
    escaped_path = _escape_ffmpeg_filter_value(subtitle_path.resolve().as_posix())
    font_size = max(18, min(72, round(frame_height * 0.045)))
    outline = max(1, round(font_size * 0.08))
    style = (
        f"FontName=Arial,FontSize={font_size},PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H90000000,BorderStyle=1,"
        f"Outline={outline},Shadow=0,Alignment=2,MarginV={max(24, round(frame_height * 0.06))}"
    )
    return f"subtitles=filename='{escaped_path}':force_style='{style}'"


def _escape_ffmpeg_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in (":", "'", "[", "]", ",", ";"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _ffmpeg_supports_filter(name: str) -> bool:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    return any(name in line.split()[:2] for line in completed.stdout.splitlines())


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


def _atempo_filter(rate: float) -> str:
    factors: list[float] = []
    remaining = rate
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2:
        factors.append(2)
        remaining /= 2
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remaining = divmod(milliseconds, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    whole_seconds, milliseconds = divmod(remaining, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"
