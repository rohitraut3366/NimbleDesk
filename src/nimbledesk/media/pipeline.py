from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from nimbledesk.media.ffmpeg import MediaToolError, probe_media, require_media_tools
from nimbledesk.media.models import (
    AnalysisConfig,
    HighlightCandidate,
    HighlightManifest,
    RenderedClip,
    TimelineEvent,
)
from nimbledesk.media.process import check_cancelled, run_cancellable
from nimbledesk.media.ranking import build_signal_points, rank_highlights
from nimbledesk.media.signals import extract_audio_signal, extract_motion_signal


class HighlightPipeline:
    def analyze_and_render(
        self,
        source: Path,
        output_directory: Path,
        config: AnalysisConfig,
        events: tuple[TimelineEvent, ...] = (),
        cancelled: Callable[[], bool] | None = None,
    ) -> HighlightManifest:
        require_media_tools()
        metadata = probe_media(source)
        output_directory.mkdir(parents=True, exist_ok=True)
        motion = extract_motion_signal(
            metadata.path, config.sample_frames_per_second, cancelled=cancelled
        )
        if metadata.has_audio:
            audio = extract_audio_signal(
                metadata.path, config.audio_window_seconds, cancelled=cancelled
            )
        else:
            audio = np.asarray([], dtype=np.float64)
        points = build_signal_points(
            duration_seconds=metadata.duration_seconds,
            motion=motion,
            motion_frames_per_second=config.sample_frames_per_second,
            audio=audio,
            audio_window_seconds=config.audio_window_seconds,
            events=events,
        )
        candidates = rank_highlights(points, metadata.duration_seconds, config, events)
        clips = tuple(
            self._render_clip(
                metadata.path, output_directory, candidate, config, cancelled=cancelled
            )
            for candidate in candidates
        )
        manifest = HighlightManifest(
            source=metadata,
            config=config,
            candidates=candidates,
            clips=clips,
        )
        manifest_path = output_directory / "highlights.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest

    def _render_clip(
        self,
        source: Path,
        output_directory: Path,
        candidate: HighlightCandidate,
        config: AnalysisConfig,
        cancelled: Callable[[], bool] | None = None,
    ) -> RenderedClip:
        check_cancelled(cancelled)
        label = candidate.event_labels[0] if candidate.event_labels else "moment"
        filename = (
            f"highlight_{candidate.rank:02d}_{_slug(label)}_"
            f"{round(candidate.peak_seconds * 1000):010d}.mp4"
        )
        output_path = output_directory / filename
        duration = candidate.end_seconds - candidate.start_seconds
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{candidate.start_seconds:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
        ]
        if config.output_width:
            command.extend(["-vf", f"scale={config.output_width}:-2"])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                str(config.video_quality),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        completed = run_cancellable(command, cancelled=cancelled)
        if completed.returncode != 0:
            raise MediaToolError(completed.stderr.strip() or f"failed to render {filename}")
        rendered_metadata = probe_media(output_path)
        return RenderedClip(
            candidate=candidate,
            output_path=output_path,
            duration_seconds=rendered_metadata.duration_seconds,
            width=rendered_metadata.width,
            height=rendered_metadata.height,
        )


def load_events(path: Path | None) -> tuple[TimelineEvent, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("events file must contain a JSON list")
    return tuple(TimelineEvent.model_validate(event) for event in payload)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "moment"
