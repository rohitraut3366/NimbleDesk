import shutil
from pathlib import Path

import pytest
from PIL import Image

from nimbledesk.creative.cancellation import CancellationToken
from nimbledesk.media.photos import PhotoPipeline
from nimbledesk.media.process import ProcessCancelled


def checkerboard(size: int = 320) -> Image.Image:
    image = Image.new("RGB", (size, size), "white")
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            if (x // 20 + y // 20) % 2:
                pixels[x, y] = (20, 30, 40)
    return image


def test_photo_pipeline_removes_duplicates_and_creates_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    best = checkerboard()
    best.save(source / "best.png")
    best.save(source / "duplicate.png")
    Image.new("RGB", (320, 320), (120, 120, 120)).save(source / "flat.png")

    manifest = PhotoPipeline().create(source, output, count=3)

    assert len(manifest.analyzed) == 3
    assert sum(analysis.duplicate_of is not None for analysis in manifest.analyzed) >= 1
    assert len(manifest.selected) == 2
    assert all(photo.output_path.is_file() for photo in manifest.selected)
    assert manifest.contact_sheet.is_file()
    assert (output / "photos.json").is_file()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_photo_pipeline_can_render_valid_slideshow(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    checkerboard().save(source / "one.png")
    Image.new("RGB", (320, 320), (200, 80, 40)).save(source / "two.png")

    manifest = PhotoPipeline().create(
        source,
        output,
        count=2,
        create_slideshow=True,
        slideshow_width=320,
    )

    assert manifest.slideshow is not None
    assert manifest.slideshow.is_file()


def test_photo_pipeline_stops_before_processing_when_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    checkerboard().save(source / "one.png")
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(ProcessCancelled):
        PhotoPipeline().create(
            source,
            tmp_path / "output",
            cancelled=cancellation.is_cancelled,
        )
