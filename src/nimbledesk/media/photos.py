from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageOps
from pydantic import BaseModel, ConfigDict

from nimbledesk.media.ffmpeg import MediaToolError, probe_media, require_media_tools
from nimbledesk.media.process import CancellationCheck, check_cancelled, run_cancellable

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


class PhotoAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    width: int
    height: int
    sharpness: float
    exposure: float
    contrast: float
    resolution: float
    quality_score: float
    difference_hash: int
    duplicate_of: Path | None = None


class RenderedPhoto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    output_path: Path
    quality_score: float


class PhotoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analyzed: tuple[PhotoAnalysis, ...]
    selected: tuple[RenderedPhoto, ...]
    contact_sheet: Path
    slideshow: Path | None = None


class PhotoPipeline:
    def create(
        self,
        source: Path,
        output_directory: Path,
        count: int = 20,
        create_slideshow: bool = False,
        slideshow_width: int = 1920,
        progress: Callable[[str, float], None] | None = None,
        cancelled: CancellationCheck | None = None,
    ) -> PhotoManifest:
        report = progress or (lambda _stage, _value: None)
        check_cancelled(cancelled)
        if not 1 <= count <= 1_000:
            raise ValueError("count must be between 1 and 1000")
        paths = discover_images(source)
        if not paths:
            raise ValueError(f"no supported images found in {source}")
        analyses_list: list[PhotoAnalysis] = []
        for index, path in enumerate(paths):
            check_cancelled(cancelled)
            report("analyzing photos", 0.05 + 0.35 * (index / len(paths)))
            analyses_list.append(analyze_photo(path))
        analyses = _mark_duplicates(tuple(analyses_list))
        report("selecting diverse photos", 0.45)
        selected_analyses = select_photos(analyses, count)
        selected_directory = output_directory / "selected"
        selected_directory.mkdir(parents=True, exist_ok=True)
        rendered_list: list[RenderedPhoto] = []
        for index, analysis in enumerate(selected_analyses, start=1):
            check_cancelled(cancelled)
            report("correcting selected photos", 0.5 + 0.25 * index / len(selected_analyses))
            rendered_list.append(_render_photo(analysis, selected_directory, index))
        rendered = tuple(rendered_list)
        contact_sheet = output_directory / "contact_sheet.jpg"
        render_contact_sheet(rendered, contact_sheet)
        slideshow = None
        if create_slideshow:
            report("rendering photo slideshow", 0.85)
            slideshow = output_directory / "slideshow.mp4"
            render_slideshow(rendered, slideshow, slideshow_width, cancelled=cancelled)
        manifest = PhotoManifest(
            analyzed=analyses,
            selected=rendered,
            contact_sheet=contact_sheet,
            slideshow=slideshow,
        )
        (output_directory / "photos.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        report("completed", 1)
        return manifest


def discover_images(source: Path) -> tuple[Path, ...]:
    resolved = source.expanduser().resolve()
    candidates: tuple[Path, ...]
    if resolved.is_file():
        candidates = (resolved,)
    elif resolved.is_dir():
        candidates = tuple(path for path in resolved.rglob("*") if path.is_file())
    else:
        raise FileNotFoundError(f"photo source does not exist: {resolved}")
    return tuple(
        sorted(
            path for path in candidates if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        )
    )


def analyze_photo(path: Path) -> PhotoAnalysis:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    sample = image.copy()
    sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
    grayscale = np.asarray(sample.convert("L"), dtype=np.float64) / 255
    horizontal_detail = float(np.mean(np.abs(np.diff(grayscale, axis=1))))
    vertical_detail = float(np.mean(np.abs(np.diff(grayscale, axis=0))))
    sharpness = min(1.0, (horizontal_detail + vertical_detail) / 0.2)
    mean_luminance = float(np.mean(grayscale))
    exposure = max(0.0, 1 - abs(mean_luminance - 0.5) * 2)
    contrast = min(1.0, float(np.std(grayscale)) * 4)
    resolution = min(1.0, math.sqrt(width * height) / 2_000)
    score = 0.4 * sharpness + 0.25 * exposure + 0.2 * contrast + 0.15 * resolution
    return PhotoAnalysis(
        source_path=path.resolve(),
        width=width,
        height=height,
        sharpness=round(sharpness, 4),
        exposure=round(exposure, 4),
        contrast=round(contrast, 4),
        resolution=round(resolution, 4),
        quality_score=round(score, 4),
        difference_hash=_difference_hash(sample),
    )


def select_photos(analyses: tuple[PhotoAnalysis, ...], count: int) -> tuple[PhotoAnalysis, ...]:
    eligible = sorted(
        (analysis for analysis in analyses if analysis.duplicate_of is None),
        key=lambda analysis: analysis.quality_score,
        reverse=True,
    )
    selected: list[PhotoAnalysis] = []
    for analysis in eligible:
        if selected and max(_hash_distance(analysis, item) for item in selected) < 10:
            continue
        selected.append(analysis)
        if len(selected) >= count:
            break
    return tuple(selected)


def render_contact_sheet(rendered: tuple[RenderedPhoto, ...], output_path: Path) -> None:
    if not rendered:
        raise ValueError("cannot create a contact sheet without selected photos")
    cell_width = 320
    cell_height = 220
    columns = min(4, len(rendered))
    rows = math.ceil(len(rendered) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#181a1f")
    draw = ImageDraw.Draw(sheet)
    for index, rendered_photo in enumerate(rendered):
        with Image.open(rendered_photo.output_path) as opened:
            thumbnail = opened.convert("RGB")
            thumbnail.thumbnail((cell_width - 16, cell_height - 40), Image.Resampling.LANCZOS)
        left = (index % columns) * cell_width + (cell_width - thumbnail.width) // 2
        top = (index // columns) * cell_height + 8
        sheet.paste(thumbnail, (left, top))
        label = f"#{index + 1}  score {rendered_photo.quality_score:.3f}"
        label_position = ((index % columns) * cell_width + 8, top + thumbnail.height + 8)
        draw.text(label_position, label, fill="white")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="JPEG", quality=90, optimize=True)


def render_slideshow(
    rendered: tuple[RenderedPhoto, ...],
    output_path: Path,
    output_width: int,
    cancelled: CancellationCheck | None = None,
) -> None:
    require_media_tools()
    if output_width < 320 or output_width > 7680:
        raise ValueError("slideshow width must be between 320 and 7680")
    if not rendered:
        raise ValueError("cannot create a slideshow without selected photos")
    output_height = round(output_width * 9 / 16)
    selected_pattern = rendered[0].output_path.parent / "selected_%04d.jpg"
    filter_graph = (
        f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
        f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p"
    )
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-framerate",
        "1/3",
        "-i",
        str(selected_pattern),
        "-vf",
        filter_graph,
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = run_cancellable(command, cancelled=cancelled)
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or "slideshow render failed")
    probe_media(output_path)


def _mark_duplicates(analyses: tuple[PhotoAnalysis, ...]) -> tuple[PhotoAnalysis, ...]:
    ordered = sorted(analyses, key=lambda analysis: analysis.quality_score, reverse=True)
    unique: list[PhotoAnalysis] = []
    results: dict[Path, PhotoAnalysis] = {}
    for analysis in ordered:
        duplicate = next(
            (candidate for candidate in unique if _hash_distance(analysis, candidate) <= 5),
            None,
        )
        updated = analysis.model_copy(
            update={"duplicate_of": duplicate.source_path if duplicate else None}
        )
        results[analysis.source_path] = updated
        if duplicate is None:
            unique.append(updated)
    return tuple(results[analysis.source_path] for analysis in analyses)


def _render_photo(
    analysis: PhotoAnalysis,
    output_directory: Path,
    index: int,
) -> RenderedPhoto:
    with Image.open(analysis.source_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Color(image).enhance(1.03)
    filename = f"selected_{index:04d}.jpg"
    output_path = output_directory / filename
    image.save(output_path, format="JPEG", quality=92, optimize=True)
    return RenderedPhoto(
        source_path=analysis.source_path,
        output_path=output_path,
        quality_score=analysis.quality_score,
    )


def _difference_hash(image: Image.Image) -> int:
    grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(grayscale, dtype=np.int16)
    differences = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in differences.flatten():
        value = (value << 1) | int(bit)
    return value


def _hash_distance(first: PhotoAnalysis, second: PhotoAnalysis) -> int:
    return (first.difference_hash ^ second.difference_hash).bit_count()
