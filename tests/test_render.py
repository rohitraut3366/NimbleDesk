import subprocess
from pathlib import Path

from pytest import MonkeyPatch

import nimbledesk.creative.render as renderer
from nimbledesk.creative.models import (
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
