from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageColor, ImageDraw, ImageFont

GraphicKind = Literal["title", "lower_third", "caption"]


def write_logo_graphic(
    source_path: Path,
    position: str,
    width_fraction: float,
    width: int,
    height: int,
    output_path: Path,
) -> Path:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    with Image.open(source_path) as source:
        logo = source.convert("RGBA")
    target_width = max(1, round(width * width_fraction))
    target_height = max(1, round(logo.height * target_width / logo.width))
    logo = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)
    margin = max(12, round(width * 0.025))
    x = margin if position.endswith("left") else width - target_width - margin
    y = margin if position.startswith("top") else height - target_height - margin
    canvas.alpha_composite(logo, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return output_path


def write_text_graphic(
    text: str,
    kind: GraphicKind,
    width: int,
    height: int,
    output_path: Path,
    *,
    font_path: Path | None = None,
    primary_color: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "title":
        _draw_title(draw, text, width, height, font_path)
    elif kind == "lower_third":
        _draw_lower_third(draw, text, width, height, font_path, primary_color)
    else:
        _draw_caption(draw, text, width, height)
    image.save(output_path)
    return output_path


def _draw_title(
    draw: ImageDraw.ImageDraw, text: str, width: int, height: int, font_path: Path | None
) -> None:
    font = _font(max(28, round(height * 0.075)), font_path)
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


def _draw_lower_third(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    height: int,
    font_path: Path | None,
    primary_color: str | None,
) -> None:
    font = _font(max(22, round(height * 0.046)), font_path)
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
        fill=(*_color(primary_color, (68, 160, 255)), 255),
    )
    draw.text(
        (left + padding_x, top + padding_y - bounds[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )


def _draw_caption(draw: ImageDraw.ImageDraw, text: str, width: int, height: int) -> None:
    font = _font(max(20, min(72, round(height * 0.045))), None)
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


def _font(
    size: int, font_path: Path | None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            str(font_path) if font_path else "DejaVuSans-Bold.ttf", size=size
        )
    except OSError:
        return ImageFont.load_default(size=size)


def _color(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if value is None:
        return fallback
    try:
        parsed = ImageColor.getrgb(value)
    except ValueError:
        return fallback
    return parsed[:3]


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
