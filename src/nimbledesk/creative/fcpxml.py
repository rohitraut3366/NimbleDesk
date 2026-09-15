from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from nimbledesk.creative.models import EditPlan


def export_fcpxml(plan: EditPlan, output_path: Path) -> Path:
    if not plan.segments:
        raise ValueError("cannot export an edit plan without segments")
    root = ElementTree.Element("fcpxml", version="1.10")
    resources = ElementTree.SubElement(root, "resources")
    frame_duration = _seconds(1 / plan.delivery.frame_rate)
    ElementTree.SubElement(
        resources,
        "format",
        id="r1",
        name=f"NimbleDesk {plan.delivery.width}x{plan.delivery.height}",
        frameDuration=frame_duration,
        width=str(plan.delivery.width),
        height=str(plan.delivery.height),
    )
    ElementTree.SubElement(
        resources,
        "asset",
        id="r2",
        name=plan.source_path.name,
        src=plan.source_path.resolve().as_uri(),
        start="0s",
        duration=_seconds(max(segment.source_range.end_seconds for segment in plan.segments)),
        hasVideo="1",
        hasAudio="1",
        format="r1",
    )
    music_reference: str | None = None
    next_resource = 3
    if plan.music_cue:
        music_reference = f"r{next_resource}"
        next_resource += 1
        ElementTree.SubElement(
            resources,
            "asset",
            id=music_reference,
            name=plan.music_cue.asset.title,
            src=plan.music_cue.asset.path.resolve().as_uri(),
            start="0s",
            duration=_seconds(plan.music_cue.asset.duration_seconds),
            hasAudio="1",
        )
    sound_references: list[str] = []
    for cue in plan.sound_cues:
        reference = f"r{next_resource}"
        next_resource += 1
        sound_references.append(reference)
        ElementTree.SubElement(
            resources,
            "asset",
            id=reference,
            name=cue.asset.title,
            src=cue.asset.path.resolve().as_uri(),
            start="0s",
            duration=_seconds(cue.asset.duration_seconds),
            hasAudio="1",
        )
    library = ElementTree.SubElement(root, "library")
    event = ElementTree.SubElement(library, "event", name="NimbleDesk")
    project = ElementTree.SubElement(event, "project", name=plan.brief.title)
    sequence = ElementTree.SubElement(
        project,
        "sequence",
        duration=_seconds(plan.duration_seconds),
        format="r1",
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ElementTree.SubElement(sequence, "spine")
    for segment in plan.segments:
        clip = ElementTree.SubElement(
            spine,
            "asset-clip",
            name=segment.segment_id,
            ref="r2",
            offset=_seconds(segment.timeline_start_seconds),
            start=_seconds(segment.source_range.start_seconds),
            duration=_seconds(segment.timeline_duration_seconds),
        )
        if segment.speed.rate != 1:
            time_map = ElementTree.SubElement(clip, "timeMap")
            ElementTree.SubElement(time_map, "timept", time="0s", value="0s", interp="linear")
            ElementTree.SubElement(
                time_map,
                "timept",
                time=_seconds(segment.timeline_duration_seconds),
                value=_seconds(segment.source_range.duration_seconds),
                interp="linear",
            )
        if (
            segment.visual.punch_in_scale != 1
            or segment.visual.reframe_center_x != 0.5
            or segment.visual.reframe_center_y != 0.5
        ):
            scale = segment.visual.punch_in_scale
            horizontal = (0.5 - segment.visual.reframe_center_x) * 100
            vertical = (segment.visual.reframe_center_y - 0.5) * 100
            ElementTree.SubElement(
                clip,
                "adjust-transform",
                scale=f"{scale} {scale}",
                position=f"{horizontal:.3f} {vertical:.3f}",
            )
    if music_reference and plan.music_cue:
        music_cue = plan.music_cue
        music_clip = ElementTree.SubElement(
            spine,
            "asset-clip",
            name=music_cue.asset.title,
            ref=music_reference,
            lane="-1",
            offset=_seconds(music_cue.timeline_range.start_seconds),
            start=_seconds(music_cue.source_range.start_seconds),
            duration=_seconds(music_cue.timeline_range.duration_seconds),
            audioRole="music",
        )
        ElementTree.SubElement(
            music_clip, "adjust-volume", amount=f"{music_cue.gain_db}dB"
        )
    for reference, sound_cue in zip(sound_references, plan.sound_cues, strict=True):
        sound_clip = ElementTree.SubElement(
            spine,
            "asset-clip",
            name=sound_cue.asset.title,
            ref=reference,
            lane="-2",
            offset=_seconds(sound_cue.timeline_range.start_seconds),
            start=_seconds(sound_cue.source_range.start_seconds),
            duration=_seconds(sound_cue.timeline_range.duration_seconds),
            audioRole="effects",
        )
        ElementTree.SubElement(
            sound_clip, "adjust-volume", amount=f"{sound_cue.gain_db}dB"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.indent(root, space="  ")
    ElementTree.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _seconds(value: float) -> str:
    return f"{round(value, 6)}s"
