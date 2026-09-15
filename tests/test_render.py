import shutil
import subprocess
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import nimbledesk.creative.render as renderer
from nimbledesk.creative.models import (
    BrandRules,
    CaptionCue,
    CreativeBrief,
    DeliverySpec,
    EditPlan,
    EditSegment,
    Evidence,
    ReframeKeyframe,
    SpeedTreatment,
    TimeRange,
    VisualTreatment,
)
from nimbledesk.media.ffmpeg import probe_media
from nimbledesk.media.models import MediaMetadata


def test_render_compiles_speed_interpolation_punch_in_and_dip(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"fixture")
    plan = EditPlan(
        source_path=source,
        brief=CreativeBrief(
            target_duration_seconds=20,
            captions=False,
            music=False,
            brand=BrandRules(logo_path=logo, primary_color="#ff5500"),
        ),
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
                    title="Ranked play",
                    lower_third="Rohit",
                    logo_path=logo,
                    logo_position="bottom_right",
                    reframe_mode="tracked_motion",
                    reframe_keyframes=(
                        ReframeKeyframe(
                            timeline_offset_seconds=0,
                            center_x=0.2,
                            center_y=0.4,
                            confidence=0.8,
                        ),
                        ReframeKeyframe(
                            timeline_offset_seconds=4,
                            center_x=0.8,
                            center_y=0.6,
                            confidence=0.8,
                        ),
                    ),
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
    monkeypatch.setattr(renderer, "_ffmpeg_supports_filter", lambda _name: True)

    renderer.render_edit_plan(plan, tmp_path / "final.mp4")

    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "mi_mode=mci" in filter_graph
    assert "zoompan=" in filter_graph
    assert "if(lt(t,4.000000)" in filter_graph
    assert "0.200000+(0.800000-0.200000)" in filter_graph
    assert "fade=t=in:st=0:d=0.25" in filter_graph
    assert "atempo=0.500000,atempo=0.500000" in filter_graph
    assert "loudnorm=I=-14.0:LRA=11:TP=-1.5" in filter_graph
    assert "alimiter=limit=0.95" in filter_graph
    assert filter_graph.count("overlay=0:0") == 2
    assert "scale=77:-1" in filter_graph
    assert "overlay=main_w-overlay_w-16:main_h-overlay_h-16" in filter_graph
    assert (tmp_path / "graphics" / "segment-001-title.png").is_file()
    assert (tmp_path / "graphics" / "segment-001-lower-third.png").is_file()


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
    monkeypatch.setattr(renderer, "_ffmpeg_supports_filter", lambda _name: True)

    renderer.render_edit_plan(plan, tmp_path / "final.mp4")

    command = commands[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[vbase]subtitles=filename=" in filter_graph
    assert "force_style=" in filter_graph
    assert command[command.index("-map") + 1] == "[vfinal]"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_renders_burned_captions_with_portable_fallback(tmp_path: Path) -> None:
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
    if not renderer._ffmpeg_supports_filter("subtitles"):
        assert (output.parent / "graphics" / "caption-0001.png").is_file()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_renders_dynamic_vertical_reframe(tmp_path: Path) -> None:
    source = tmp_path / "wide-source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    plan = _single_segment_plan(source).model_copy(
        update={"delivery": DeliverySpec(width=320, height=568, frame_rate=24)}
    )
    visual = plan.segments[0].visual.model_copy(
        update={
            "reframe_mode": "tracked_motion",
            "reframe_keyframes": (
                ReframeKeyframe(
                    timeline_offset_seconds=0,
                    center_x=0.1,
                    center_y=0.5,
                    confidence=0.8,
                ),
                ReframeKeyframe(
                    timeline_offset_seconds=2,
                    center_x=0.9,
                    center_y=0.5,
                    confidence=0.8,
                ),
            ),
        }
    )
    plan = plan.model_copy(
        update={"segments": (plan.segments[0].model_copy(update={"visual": visual}),)}
    )
    output = tmp_path / "vertical.mp4"

    renderer.render_edit_plan(plan, output)

    metadata = probe_media(output)
    assert (metadata.width, metadata.height) == (320, 568)


def test_timeline_compiler_builds_video_and_audio_cross_dissolve(tmp_path: Path) -> None:
    plan = _two_segment_cross_dissolve_plan(tmp_path / "source.mp4")
    filters: list[str] = []

    renderer._compile_timeline(plan, filters)

    graph = ";".join(filters)
    assert "xfade=transition=fade:duration=0.350:offset=1.650" in graph
    assert "acrossfade=d=0.350:c1=tri:c2=tri" in graph


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_renders_cross_dissolve_with_expected_duration(tmp_path: Path) -> None:
    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if not any("xfade" in line.split()[:2] for line in filters.splitlines()):
        pytest.skip("ffmpeg was built without the xfade filter")
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
            "testsrc2=size=640x360:rate=24:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    output = tmp_path / "cross-dissolve.mp4"

    renderer.render_edit_plan(_two_segment_cross_dissolve_plan(source), output)

    assert probe_media(output).duration_seconds == pytest.approx(3.65, abs=0.1)


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


def _two_segment_cross_dissolve_plan(source: Path) -> EditPlan:
    first = _single_segment_plan(source).segments[0]
    second = first.model_copy(
        update={
            "segment_id": "segment-002",
            "role": "payoff",
            "source_range": TimeRange(start_seconds=2, end_seconds=4),
            "timeline_start_seconds": 1.65,
            "visual": first.visual.model_copy(
                update={"transition_in": "cross_dissolve"}
            ),
        }
    )
    return _single_segment_plan(source).model_copy(update={"segments": (first, second)})
