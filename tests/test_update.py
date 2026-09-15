import base64
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nimbledesk.update import (
    ReleaseArtifact,
    ReleaseManifest,
    SignedRelease,
    UpdateManager,
    canonical_manifest,
    verify_release,
)


def _release(archive: Path, version: str = "2.0.0") -> tuple[SignedRelease, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    manifest = ReleaseManifest(
        version=version,
        channel="stable",
        published_at=datetime.now(UTC),
        artifacts={
            "test": ReleaseArtifact(
                url=archive.as_uri(),
                size=archive.stat().st_size,
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
        },
    )
    signature = base64.b64encode(private_key.sign(canonical_manifest(manifest))).decode()
    return SignedRelease(key_id="release-2026", manifest=manifest, signature=signature), public_key


def _archive(path: Path, executable: bytes) -> None:
    with tarfile.open(path, "w:gz") as compressed:
        payload = io.BytesIO(executable)
        metadata = tarfile.TarInfo("NimbleDesk/nimbledesk")
        metadata.size = len(executable)
        metadata.mode = 0o755
        compressed.addfile(metadata, payload)


def test_signed_release_rejects_modified_manifest(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    _archive(archive, b"version two")
    release, public_key = _release(archive)
    payload = release.model_dump(mode="json")
    payload["manifest"]["version"] = "2.0.1"

    with pytest.raises(ValueError, match="signature is invalid"):
        verify_release(json.dumps(payload).encode(), {"release-2026": public_key})


def test_install_and_rollback_restore_version_and_migration_state(tmp_path: Path) -> None:
    root = tmp_path / "home" / ".nimbledesk"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "settings.json").write_text("version-one", encoding="utf-8")
    manager = UpdateManager(root)

    first_archive = tmp_path / "one.tar.gz"
    _archive(first_archive, b"version one")
    first_release, first_public_key = _release(first_archive, "1.0.0")
    first_payload = first_release.model_dump_json().encode()
    manager.install(first_payload, {"release-2026": first_public_key}, "test")

    (config / "settings.json").write_text("before-migration", encoding="utf-8")
    second_archive = tmp_path / "two.tar.gz"
    _archive(second_archive, b"version two")
    second_release, second_public_key = _release(second_archive, "2.0.0")
    installed = manager.install(
        second_release.model_dump_json().encode(),
        {"release-2026": second_public_key},
        "test",
    )
    (config / "settings.json").write_text("after-migration", encoding="utf-8")

    assert installed.current_version == "2.0.0"
    assert manager.executable().read_bytes() == b"version two"
    rolled_back = manager.rollback()
    assert rolled_back.current_version == "1.0.0"
    assert manager.executable().read_bytes() == b"version one"
    assert (config / "settings.json").read_text(encoding="utf-8") == "before-migration"


def test_install_rejects_artifact_changed_after_signing(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    _archive(archive, b"signed")
    release, public_key = _release(archive)
    archive.write_bytes(b"modified")

    with pytest.raises(ValueError, match="size does not match"):
        UpdateManager(tmp_path / "data").install(
            release.model_dump_json().encode(), {"release-2026": public_key}, "test"
        )


def test_install_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as compressed:
        payload = io.BytesIO(b"bad")
        metadata = tarfile.TarInfo("../outside")
        metadata.size = 3
        compressed.addfile(metadata, payload)
    release, public_key = _release(archive)

    with pytest.raises(ValueError, match="unsafe path"):
        UpdateManager(tmp_path / "data").install(
            release.model_dump_json().encode(), {"release-2026": public_key}, "test"
        )
