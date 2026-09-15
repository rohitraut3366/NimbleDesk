from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nimbledesk.update import ReleaseArtifact, ReleaseManifest, canonical_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an Ed25519-signed NimbleDesk release")
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--minimum-version")
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        metavar="NAME=PATH=HTTPS_URL",
    )
    arguments = parser.parse_args()
    artifacts = dict(_artifact(value) for value in arguments.artifact)
    manifest = ReleaseManifest(
        version=arguments.version,
        channel=arguments.channel,
        published_at=datetime.now(UTC),
        minimum_version=arguments.minimum_version,
        artifacts=artifacts,
    )
    private_key = serialization.load_pem_private_key(
        arguments.private_key.read_bytes(), password=None
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("release signing key must be an Ed25519 private key")
    signature = private_key.sign(canonical_manifest(manifest))
    document = {
        "key_id": arguments.key_id,
        "algorithm": "Ed25519",
        "manifest": manifest.model_dump(mode="json", exclude_none=True),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _artifact(value: str) -> tuple[str, ReleaseArtifact]:
    parts = value.split("=", 2)
    if len(parts) != 3:
        raise ValueError("artifact must use NAME=PATH=HTTPS_URL")
    name, raw_path, url = parts
    path = Path(raw_path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return name, ReleaseArtifact(url=url, sha256=digest.hexdigest(), size=path.stat().st_size)


if __name__ == "__main__":
    main()
