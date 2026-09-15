from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)

    @field_validator("url")
    @classmethod
    def require_secure_url(cls, value: str) -> str:
        scheme = urllib.parse.urlparse(value).scheme
        if scheme not in {"https", "file"}:
            raise ValueError("release artifact URL must use HTTPS or file://")
        return value


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    version: str = Field(min_length=1, pattern=r"^[0-9A-Za-z][0-9A-Za-z.+-]*$")
    channel: str = Field(min_length=1)
    published_at: datetime
    minimum_version: str | None = None
    artifacts: dict[str, ReleaseArtifact]

    @field_validator("artifacts")
    @classmethod
    def require_artifact(cls, value: dict[str, ReleaseArtifact]) -> dict[str, ReleaseArtifact]:
        if not value:
            raise ValueError("release manifest must contain an artifact")
        return value


class SignedRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: str = Field(min_length=1)
    algorithm: Literal["Ed25519"] = "Ed25519"
    manifest: ReleaseManifest
    signature: str = Field(min_length=1)


class InstallState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    current_version: str
    previous_version: str | None = None
    installed_at: datetime
    backup: str | None = None


def canonical_manifest(manifest: ReleaseManifest) -> bytes:
    payload = manifest.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_release(payload: bytes, trusted_keys: Mapping[str, bytes]) -> SignedRelease:
    release = SignedRelease.model_validate_json(payload)
    encoded_key = trusted_keys.get(release.key_id)
    if encoded_key is None:
        raise ValueError(f"untrusted release signing key: {release.key_id}")
    try:
        signature = base64.b64decode(release.signature, validate=True)
        Ed25519PublicKey.from_public_bytes(encoded_key).verify(
            signature, canonical_manifest(release.manifest)
        )
    except (InvalidSignature, ValueError) as error:
        raise ValueError("release signature is invalid") from error
    return release


def load_trusted_keys(path: Path) -> dict[str, bytes]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("trusted key file must be a JSON object")
    keys: dict[str, bytes] = {}
    for key_id, encoded_key in document.items():
        if not isinstance(key_id, str) or not isinstance(encoded_key, str):
            raise ValueError("trusted key identifiers and values must be strings")
        raw_key = base64.b64decode(encoded_key, validate=True)
        if len(raw_key) != 32:
            raise ValueError(f"Ed25519 public key {key_id!r} must contain 32 bytes")
        keys[key_id] = raw_key
    return keys


class UpdateManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".nimbledesk"
        self.application_root = self.root / "application"
        self.versions_root = self.application_root / "versions"
        self.state_path = self.application_root / "install-state.json"
        self.backups_root = self.root / "backups"

    def state(self) -> InstallState | None:
        if not self.state_path.is_file():
            return None
        return InstallState.model_validate_json(self.state_path.read_bytes())

    def install(
        self,
        release_payload: bytes,
        trusted_keys: Mapping[str, bytes],
        artifact_name: str,
    ) -> InstallState:
        release = verify_release(release_payload, trusted_keys)
        artifact = release.manifest.artifacts.get(artifact_name)
        if artifact is None:
            raise ValueError(f"release has no artifact named {artifact_name!r}")
        current_state = self.state()
        if current_state and current_state.current_version == release.manifest.version:
            return current_state

        self.versions_root.mkdir(parents=True, exist_ok=True)
        version_path = self.versions_root / release.manifest.version
        if version_path.exists():
            raise FileExistsError(f"version path already exists: {version_path}")

        backup = self._backup_state(current_state.current_version if current_state else "initial")
        with tempfile.TemporaryDirectory(dir=self.application_root) as temporary_directory:
            temporary_root = Path(temporary_directory)
            archive = temporary_root / "release.archive"
            _download(artifact.url, archive, artifact.size, artifact.sha256)
            extracted = temporary_root / "extracted"
            extracted.mkdir()
            _extract_archive(archive, extracted)
            payload_root = _single_payload_root(extracted)
            staging = self.versions_root / f".{release.manifest.version}.staging"
            try:
                shutil.copytree(payload_root, staging)
                os.replace(staging, version_path)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

        new_state = InstallState(
            current_version=release.manifest.version,
            previous_version=current_state.current_version if current_state else None,
            installed_at=datetime.now(UTC),
            backup=str(backup) if backup else None,
        )
        self._write_state(new_state)
        return new_state

    def rollback(self) -> InstallState:
        current_state = self.state()
        if current_state is None or current_state.previous_version is None:
            raise RuntimeError("no previous NimbleDesk version is available")
        previous_path = self.versions_root / current_state.previous_version
        if not previous_path.is_dir():
            raise RuntimeError("the previous NimbleDesk version is missing")
        rolled_back = InstallState(
            current_version=current_state.previous_version,
            previous_version=current_state.current_version,
            installed_at=datetime.now(UTC),
            backup=current_state.backup,
        )
        if current_state.backup:
            self._restore_backup(Path(current_state.backup))
        self._write_state(rolled_back)
        return rolled_back

    def executable(self) -> Path:
        current_state = self.state()
        if current_state is None:
            raise RuntimeError("NimbleDesk is not installed by the update manager")
        name = "nimbledesk.exe" if os.name == "nt" else "nimbledesk"
        candidate = self.versions_root / current_state.current_version / name
        if not candidate.is_file():
            raise RuntimeError(f"installed executable is missing: {candidate}")
        return candidate

    def _backup_state(self, version: str) -> Path | None:
        sources = [self.root / name for name in ("config", "state", "profiles")]
        existing_sources = [source for source in sources if source.exists()]
        if not existing_sources:
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.backups_root / f"{stamp}-{_safe_version(version)}"
        backup.mkdir(parents=True)
        for source in existing_sources:
            destination = backup / source.name
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        return backup

    def _restore_backup(self, backup: Path) -> None:
        resolved_backup = backup.resolve()
        if self.backups_root.resolve() not in resolved_backup.parents:
            raise ValueError("backup is outside the NimbleDesk backup directory")
        if not resolved_backup.is_dir():
            raise RuntimeError("rollback backup is missing")
        for source in resolved_backup.iterdir():
            destination = self.root / source.name
            temporary = self.root / f".{source.name}.restore"
            if temporary.exists():
                _remove_path(temporary)
            if source.is_dir():
                shutil.copytree(source, temporary)
            else:
                shutil.copy2(source, temporary)
            if destination.exists():
                _remove_path(destination)
            os.replace(temporary, destination)

    def _write_state(self, state: InstallState) -> None:
        self.application_root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install verified NimbleDesk updates or roll back")
    parser.add_argument("--root", type=Path)
    subparsers = parser.add_subparsers(dest="action", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(action="status")
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("release", type=Path)
    install_parser.add_argument("--trusted-keys", type=Path, required=True)
    install_parser.add_argument("--artifact", required=True)
    subparsers.add_parser("rollback")
    arguments = parser.parse_args()
    manager = UpdateManager(arguments.root)
    if arguments.action == "status":
        state = manager.state()
        print(state.model_dump_json(indent=2) if state else "NimbleDesk is not installed")
    elif arguments.action == "rollback":
        print(manager.rollback().model_dump_json(indent=2))
    else:
        print(
            manager.install(
                arguments.release.read_bytes(),
                load_trusted_keys(arguments.trusted_keys),
                arguments.artifact,
            ).model_dump_json(indent=2)
        )


def _download(url: str, destination: Path, expected_size: int, expected_hash: str) -> None:
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "NimbleDesk-Updater/1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > expected_size:
                raise ValueError("release artifact exceeds its signed size")
            digest.update(chunk)
            output.write(chunk)
    if size != expected_size:
        raise ValueError("release artifact size does not match signed metadata")
    if digest.hexdigest() != expected_hash:
        raise ValueError("release artifact digest does not match signed metadata")


def _extract_archive(archive: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as compressed:
            zip_members = compressed.infolist()
            _validate_members((item.filename for item in zip_members), destination)
            if any(stat.S_ISLNK(item.external_attr >> 16) for item in zip_members):
                raise ValueError("release archives cannot contain links")
            compressed.extractall(destination)
        return
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as compressed:
            tar_members = compressed.getmembers()
            _validate_members((item.name for item in tar_members), destination)
            if any(item.issym() or item.islnk() for item in tar_members):
                raise ValueError("release archives cannot contain links")
            compressed.extractall(destination, filter="data")
        return
    raise ValueError("release artifact must be a ZIP or tar archive")


def _validate_members(names: Iterable[str], destination: Path) -> None:
    root = destination.resolve()
    for name in names:
        candidate = (destination / str(name)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("release archive contains an unsafe path")


def _single_payload_root(extracted: Path) -> Path:
    children = list(extracted.iterdir())
    return children[0] if len(children) == 1 and children[0].is_dir() else extracted


def _safe_version(version: str) -> str:
    return "".join(
        character if character.isalnum() or character in ".-_" else "_"
        for character in version
    )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
