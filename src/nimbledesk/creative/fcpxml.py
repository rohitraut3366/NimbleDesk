from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from nimbledesk.creative.graphics import GraphicKind, write_logo_graphic, write_text_graphic
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
    dissolve_reference: str | None = None
    if any(segment.visual.transition_in == "cross_dissolve" for segment in plan.segments):
        dissolve_reference = "r-transition-dissolve"
        ElementTree.SubElement(
            resources,
            "effect",
            id=dissolve_reference,
            name="Cross Dissolve",
            uid=(
                ".../Transitions.localized/Dissolves.localized/"
                "Cross Dissolve.localized/Cross Dissolve.motr"
            ),
        )
    music_references: list[str] = []
    next_resource = 3
    for music_cue in plan.all_music_cues:
        music_reference = f"r{next_resource}"
        next_resource += 1
        music_references.append(music_reference)
        ElementTree.SubElement(
            resources,
            "asset",
            id=music_reference,
            name=music_cue.asset.title,
            src=music_cue.asset.path.resolve().as_uri(),
            start="0s",
            duration=_seconds(music_cue.asset.duration_seconds),
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
    logo_reference: str | None = None
    logo_path: Path | None = None
    if plan.brief.brand.logo_path:
        logo_reference = f"r{next_resource}"
        next_resource += 1
        logo_path = output_path.parent / "graphics" / "brand-logo.png"
        write_logo_graphic(
            plan.brief.brand.logo_path,
            plan.brief.brand.logo_position,
            plan.brief.brand.logo_width_fraction,
            plan.delivery.width,
            plan.delivery.height,
            logo_path,
        )
        ElementTree.SubElement(
            resources,
            "asset",
            id=logo_reference,
            name="Brand logo",
            src=logo_path.resolve().as_uri(),
            start="0s",
            duration=_seconds(plan.duration_seconds),
            hasVideo="1",
            format="r1",
        )
    graphic_references: dict[tuple[str, GraphicKind], tuple[str, Path, float, float]] = {}
    for segment in plan.segments:
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
        for kind, text, start, end in graphic_specs:
            path = output_path.parent / "graphics" / (
                f"{segment.segment_id}-{kind.replace('_', '-')}.png"
            )
            write_text_graphic(
                text,
                kind,
                plan.delivery.width,
                plan.delivery.height,
                path,
                font_path=segment.visual.font_path,
                primary_color=plan.brief.brand.primary_color,
            )
            reference = f"r{next_resource}"
            next_resource += 1
            graphic_references[(segment.segment_id, kind)] = (reference, path, start, end)
            ElementTree.SubElement(
                resources,
                "asset",
                id=reference,
                name=f"{segment.segment_id} {kind.replace('_', ' ')}",
                src=path.resolve().as_uri(),
                start="0s",
                duration=_seconds(end - start),
                hasVideo="1",
                format="r1",
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
        if segment.visual.transition_in == "cross_dissolve" and dissolve_reference:
            transition = ElementTree.SubElement(
                spine,
                "transition",
                name="Cross Dissolve",
                offset=_seconds(segment.timeline_start_seconds),
                duration=_seconds(segment.visual.transition_duration_seconds),
            )
            ElementTree.SubElement(transition, "filter-video", ref=dissolve_reference)
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
        for kind in ("title", "lower_third"):
            graphic = graphic_references.get((segment.segment_id, kind))
            if graphic is None:
                continue
            reference, path, start, end = graphic
            ElementTree.SubElement(
                clip,
                "asset-clip",
                name=path.stem,
                ref=reference,
                lane="1",
                offset=_seconds(segment.timeline_start_seconds + start),
                start="0s",
                duration=_seconds(end - start),
            )
        if logo_reference and logo_path:
            ElementTree.SubElement(
                clip,
                "asset-clip",
                name=logo_path.stem,
                ref=logo_reference,
                lane="2",
                offset=_seconds(segment.timeline_start_seconds),
                start="0s",
                duration=_seconds(segment.timeline_duration_seconds),
            )
    for index, caption in enumerate(plan.captions, start=1):
        caption_element = ElementTree.SubElement(
            spine,
            "caption",
            name=f"Caption {index}",
            lane="3",
            offset=_seconds(caption.timeline_range.start_seconds),
            start="0s",
            duration=_seconds(caption.timeline_range.duration_seconds),
            role="captions",
        )
        caption_text_element = ElementTree.SubElement(caption_element, "text")
        style_id = f"caption-style-{index}"
        styled_text = ElementTree.SubElement(caption_text_element, "text-style", ref=style_id)
        styled_text.text = caption.text
        style_definition = ElementTree.SubElement(
            caption_element, "text-style-def", id=style_id
        )
        ElementTree.SubElement(
            style_definition,
            "text-style",
            font="Arial",
            fontSize="48",
            fontColor="1 1 1 1",
            alignment="center",
        )
    for music_reference, music_cue in zip(
        music_references, plan.all_music_cues, strict=True
    ):
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
