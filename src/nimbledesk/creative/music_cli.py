from __future__ import annotations

import argparse
import json
from pathlib import Path

from nimbledesk.creative.music_index import index_music_directory, write_music_catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze licensed local music and create a NimbleDesk catalog"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--license", required=True, dest="license_terms")
    parser.add_argument("--attribution")
    parser.add_argument("--mood", action="append", default=[])
    parser.add_argument("--contains-vocals", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    assets = index_music_directory(
        arguments.source,
        license_terms=arguments.license_terms,
        attribution=arguments.attribution,
        declared_moods=tuple(arguments.mood),
        instrumental=not arguments.contains_vocals,
    )
    write_music_catalog(assets, arguments.output)
    print(json.dumps({"catalog": str(arguments.output), "tracks": len(assets)}, indent=2))
