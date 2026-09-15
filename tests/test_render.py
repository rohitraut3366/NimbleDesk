import shutil
import subprocess
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import nimbledesk.creative.render as renderer
from nimbledesk.creative.models import (
    CaptionCue,
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.media.models import MediaMetadata


def test_render_compiles_speed_interpolation_punch_in_and_dip(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    plan = EditPlan(
        source_path=source,
        brief=CreativeBrief(target_duration_seconds=20, captions=False, music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=source,
                source_range=TimeRange(start_seconds=0, end_seconds=4),
                timeline_start_seconds=0,
                speed=SpeedTreatment(
                    rate=0.25,
                    interpolation="optical_flow",
                    rationale="emphasize action",
                ),
                visual=VisualTreatment(
                    transition_in="dip_to_black",
                    punch_in_scale=1.2,
                    rationale="animated emphasis",
                ),
                score=1,
                evidence=(
                    Evidence(
                        analyzer="fixture",
                        analyzer_version="1",
                        confidence=1,
                        description="action peak",
                    ),
                ),
            ),
        ),
        delivery=DeliverySpec(width=640, height=360, frame_rate=30),
    )
    metadata = MediaMetadata(
        path=source,
        duration_seconds=4,
        width=640,
        height=360,
        frame_rate=30,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
    commands: list[list[str]] = []

    def capture(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(renderer, "require_media_tools", lambda: None)
    monkeypatch.setattr(renderer, "probe_media", lambda _path: metadata)
    monkeypatch.setattr(renderer, "run_cancellable", capture)

    renderer.render_edit_plan(plan, tmp_path / "final.mp4")

    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "mi_mode=mci" in filter_graph
    assert "zoompan=" in filter_graph
    assert "fade=t=in:st=0:d=0.25" in filter_graph
    assert "atempo=0.500000,atempo=0.500000" in filter_graph


def test_render_writes_captions_before_ffmpeg_and_maps_captioned_video(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    cue = CaptionCue(
        timeline_range=TimeRange(start_seconds=0, end_seconds=2),
        text="Readable caption",
        segment_id="segment-001",
        source_range=TimeRange(start_seconds=0, end_seconds=2),
    )
    plan = _single_segment_plan(source, captions=(cue,))
    metadata = MediaMetadata(
        path=source,
        duration_seconds=2,
        width=640,
        height=360,
        frame_rate=30,
        has_audio=False,
        video_codec="h264",
        audio_codec=None,
    )
    commands: list[list[str]] = []

    def capture(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert (tmp_path / "final.srt").read_text(encoding="utf-8").endswith(
            "Readable caption\n"
        )
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(renderer, "require_media_tools", lambda: None)
    monkeypatch.setattr(renderer, "probe_media", lambda _path: metadata)
    monkeypatch.setattr(renderer, "run_cancellable", capture)

    renderer.render_edit_plan(plan, tmp_path / "final.mp4")

    command = commands[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[vbase]subtitles=filename=" in filter_graph
    assert "force_style=" in filter_graph
    assert command[command.index("-map") + 1] == "[vfinal]"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_renders_burned_captions_when_libass_is_available(tmp_path: Path) -> None:
    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if not any("subtitles" in line.split()[:2] for line in filters.splitlines()):
        pytest.skip("ffmpeg was built without the subtitles/libass filter")

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=320x180:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    cue = CaptionCue(
        timeline_range=TimeRange(start_seconds=0.2, end_seconds=1.8),
        text="Caption visible in review",
        segment_id="segment-001",
        source_range=TimeRange(start_seconds=0.2, end_seconds=1.8),
    )
    plan = _single_segment_plan(source, captions=(cue,))
    output = tmp_path / "caption output" / "final.mp4"

    renderer.render_edit_plan(plan, output)

    assert output.stat().st_size > 0
    assert output.with_suffix(".srt").is_file()


def _single_segment_plan(
    source: Path, captions: tuple[CaptionCue, ...] = ()
) -> EditPlan:
    return EditPlan(
        source_path=source,
        brief=CreativeBrief(target_duration_seconds=5, captions=bool(captions), music=False),
        segments=(
            EditSegment(
                segment_id="segment-001",
                role="hook",
                source_path=source,
                source_range=TimeRange(start_seconds=0, end_seconds=2),
                timeline_start_seconds=0,
                speed=SpeedTreatment(rate=1, rationale="retain source timing"),
                visual=VisualTreatment(rationale="retain source framing"),
                score=1,
                evidence=(),
            ),
        ),
        captions=captions,
        delivery=DeliverySpec(width=640, height=360, frame_rate=24),
    )
