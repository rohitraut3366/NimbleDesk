from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

GraphicKind = Literal["title", "lower_third", "caption"]


def write_text_graphic(
    text: str,
    kind: GraphicKind,
    width: int,
    height: int,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "title":
        _draw_title(draw, text, width, height)
    elif kind == "lower_third":
        _draw_lower_third(draw, text, width, height)
    else:
        _draw_caption(draw, text, width, height)
    image.save(output_path)
    return output_path


def _draw_title(draw: ImageDraw.ImageDraw, text: str, width: int, height: int) -> None:
    font = _font(max(28, round(height * 0.075)))
    wrapped = _wrap_for_pixels(draw, text, font, width * 0.78)
    bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=round(height * 0.015))
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    padding_x = round(width * 0.045)
    padding_y = round(height * 0.035)
    left = (width - text_width) / 2 - padding_x
    top = height * 0.42 - padding_y
    right = (width + text_width) / 2 + padding_x
    bottom = top + text_height + 2 * padding_y
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=round(height * 0.025),
        fill=(8, 12, 20, 210),
    )
    draw.multiline_text(
        ((width - text_width) / 2, top + padding_y - bounds[1]),
        wrapped,
        font=font,
        fill=(255, 255, 255, 255),
        spacing=round(height * 0.015),
        align="center",
    )


def _draw_lower_third(draw: ImageDraw.ImageDraw, text: str, width: int, height: int) -> None:
    font = _font(max(22, round(height * 0.046)))
    bounds = draw.textbbox((0, 0), text, font=font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    padding_x = round(width * 0.025)
    padding_y = round(height * 0.018)
    left = round(width * 0.055)
    bottom = round(height * 0.84)
    right = min(width - left, left + text_width + 2 * padding_x)
    top = bottom - text_height - 2 * padding_y
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=round(height * 0.012),
        fill=(8, 12, 20, 220),
    )
    draw.rectangle(
        (left, top, left + max(5, round(width * 0.006)), bottom),
        fill=(68, 160, 255, 255),
    )
    draw.text(
        (left + padding_x, top + padding_y - bounds[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )


def _draw_caption(draw: ImageDraw.ImageDraw, text: str, width: int, height: int) -> None:
    font = _font(max(20, min(72, round(height * 0.045))))
    bounds = draw.multiline_textbbox(
        (0, 0), text, font=font, spacing=round(height * 0.008), align="center"
    )
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    padding_x = round(width * 0.025)
    padding_y = round(height * 0.014)
    left = (width - text_width) / 2 - padding_x
    bottom = height * 0.91
    top = bottom - text_height - 2 * padding_y
    right = (width + text_width) / 2 + padding_x
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=round(height * 0.012),
        fill=(0, 0, 0, 190),
    )
    draw.multiline_text(
        ((width - text_width) / 2, top + padding_y - bounds[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(1, round(height * 0.002)),
        stroke_fill=(0, 0, 0, 255),
        spacing=round(height * 0.008),
        align="center",
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _wrap_for_pixels(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    maximum_width: float,
) -> str:
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= maximum_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return "\n".join(lines[:3])
