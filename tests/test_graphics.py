from pathlib import Path

from PIL import Image

from nimbledesk.creative.graphics import write_text_graphic


def test_portrait_caption_graphic_fits_delivery_safe_area(tmp_path: Path) -> None:
    output = tmp_path / "caption.png"

    write_text_graphic(
        "We survived that impossible fight and\nfound the winning move.",
        "caption",
        1080,
        1920,
        output,
    )

    with Image.open(output) as image:
        bounds = image.convert("RGBA").getchannel("A").getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    assert left >= 1080 * 0.05
    assert right <= 1080 * 0.95
    assert top >= 1920 * 0.05
    assert bottom <= 1920 * 0.95
