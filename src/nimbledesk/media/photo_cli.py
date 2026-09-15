from __future__ import annotations

import argparse
import json
from pathlib import Path

from nimbledesk.media.photos import PhotoPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select, correct, and arrange the best photos from a folder"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--slideshow", action="store_true")
    parser.add_argument("--slideshow-width", type=int, default=1920)
    parser.add_argument("--social-assets", action="store_true")
    parser.add_argument("--animated-gif", action="store_true")
    parser.add_argument("--title", default="Photo story")
    parser.add_argument(
        "--platform",
        choices=("youtube", "instagram", "tiktok"),
        default="instagram",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    manifest = PhotoPipeline().create(
        arguments.source,
        arguments.output_directory,
        count=arguments.count,
        create_slideshow=arguments.slideshow,
        slideshow_width=arguments.slideshow_width,
        create_social_assets=arguments.social_assets,
        create_animated_gif=arguments.animated_gif,
        title=arguments.title,
        platform=arguments.platform,
    )
    print(
        json.dumps(
            {
                "selected": [str(photo.output_path) for photo in manifest.selected],
                "contact_sheet": str(manifest.contact_sheet),
                "slideshow": str(manifest.slideshow) if manifest.slideshow else None,
                "thumbnail": str(manifest.thumbnail) if manifest.thumbnail else None,
                "poster": str(manifest.poster) if manifest.poster else None,
                "collage": str(manifest.collage) if manifest.collage else None,
                "carousel": [str(path) for path in manifest.carousel],
                "animated_gif": (
                    str(manifest.animated_gif) if manifest.animated_gif else None
                ),
                "manifest": str(arguments.output_directory / "photos.json"),
            },
            indent=2,
        )
    )
