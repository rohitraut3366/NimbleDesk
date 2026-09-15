from __future__ import annotations

import base64
import hashlib
import io
from typing import Literal

from PIL import Image

from nimbledesk.protocol.models import CaptureOptions, ScreenCapture


def encode_capture(
    image: Image.Image,
    observation_id: str,
    options: CaptureOptions | None = None,
) -> ScreenCapture:
    selected_options = options or CaptureOptions()
    image.thumbnail(
        (selected_options.max_width, selected_options.max_height),
        resample=Image.Resampling.LANCZOS,
    )
    output = io.BytesIO()
    mime_type: Literal["image/png", "image/jpeg"]
    if selected_options.image_format == "jpeg":
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=selected_options.jpeg_quality,
            optimize=True,
        )
        mime_type = "image/jpeg"
    else:
        image.save(output, format="PNG", optimize=True)
        mime_type = "image/png"
    image_bytes = output.getvalue()
    return ScreenCapture(
        observation_id=observation_id,
        mime_type=mime_type,
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        data_base64=base64.b64encode(image_bytes).decode("ascii"),
    )
