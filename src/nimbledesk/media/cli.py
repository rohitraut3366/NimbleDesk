from __future__ import annotations

import argparse
import json
from pathlib import Path

from nimbledesk.media.models import AnalysisConfig
from nimbledesk.media.pipeline import HighlightPipeline, load_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze long-form footage and render ranked highlight clips"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--lead-in", type=float, default=8)
    parser.add_argument("--aftermath", type=float, default=12)
    parser.add_argument("--minimum-separation", type=float, default=20)
    parser.add_argument("--output-width", type=int)
    parser.add_argument(
        "--events",
        type=Path,
        help="Optional JSON timeline events from a game, replay, or manual markers",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    config = AnalysisConfig(
        highlight_count=arguments.count,
        lead_in_seconds=arguments.lead_in,
        aftermath_seconds=arguments.aftermath,
        minimum_peak_separation_seconds=arguments.minimum_separation,
        output_width=arguments.output_width,
    )
    manifest = HighlightPipeline().analyze_and_render(
        source=arguments.source,
        output_directory=arguments.output_directory,
        config=config,
        events=load_events(arguments.events),
    )
    print(
        json.dumps(
            {
                "source": str(manifest.source.path),
                "clips": [str(clip.output_path) for clip in manifest.clips],
                "manifest": str(arguments.output_directory / "highlights.json"),
            },
            indent=2,
        )
    )
