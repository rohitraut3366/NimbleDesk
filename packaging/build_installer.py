from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

SystemName = Literal["Darwin", "Windows", "Linux"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a native NimbleDesk installer")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    parser.add_argument("--executable", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    executable_name = "nimbledesk.exe" if platform.system() == "Windows" else "nimbledesk"
    executable = arguments.executable or root / "dist" / "bundle" / executable_name
    build_installer(
        executable.resolve(),
        arguments.output.resolve(),
        arguments.version,
        platform.system(),  # type: ignore[arg-type]
        root / "build" / "installer",
    )


def build_installer(
    executable: Path,
    output: Path,
    version: str,
    system: SystemName,
    work: Path,
) -> Path:
    if not executable.is_file():
        raise FileNotFoundError(executable)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=True)
    native_version = _native_version(version)
    if system == "Darwin":
        return _build_macos(executable, output, native_version, work)
    if system == "Windows":
        return _build_windows(executable, output, native_version, work)
    if system == "Linux":
        return _build_linux(executable, output, native_version, work)
    raise ValueError(f"unsupported installer platform: {system}")


def _build_macos(executable: Path, output: Path, version: str, work: Path) -> Path:
    root = work / "root"
    app = root / "Applications" / "NimbleDesk.app"
    contents = app / "Contents"
    binary = contents / "MacOS" / "nimbledesk"
    binary.parent.mkdir(parents=True)
    shutil.copy2(executable, binary)
    binary.chmod(0o755)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleDisplayName": "NimbleDesk",
                "CFBundleExecutable": "nimbledesk",
                "CFBundleIdentifier": "io.nimbledesk.app",
                "CFBundleName": "NimbleDesk",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "LSMinimumSystemVersion": "12.0",
                "NSHighResolutionCapable": True,
            }
        )
    )
    application_identity = os.environ.get("NIMBLEDESK_APPLE_APPLICATION_IDENTITY")
    if application_identity:
        _run(
            [
                "codesign",
                "--force",
                "--options",
                "runtime",
                "--timestamp",
                "--sign",
                application_identity,
                str(app),
            ]
        )
    scripts = work / "scripts"
    scripts.mkdir()
    postinstall = scripts / "postinstall"
    postinstall.write_text(
        "#!/bin/sh\n"
        "mkdir -p /usr/local/bin\n"
        "ln -sfn /Applications/NimbleDesk.app/Contents/MacOS/nimbledesk "
        "/usr/local/bin/nimbledesk\n",
        encoding="utf-8",
    )
    postinstall.chmod(0o755)
    package = output / f"NimbleDesk-{version}-macOS.pkg"
    command = [
        "pkgbuild",
        "--root",
        str(root),
        "--scripts",
        str(scripts),
        "--identifier",
        "io.nimbledesk.app",
        "--version",
        version,
    ]
    identity = os.environ.get("NIMBLEDESK_APPLE_INSTALLER_IDENTITY")
    if identity:
        command.extend(("--sign", identity))
    command.append(str(package))
    _run(command)
    notary_profile = os.environ.get("NIMBLEDESK_APPLE_NOTARY_PROFILE")
    if notary_profile:
        _run(
            [
                "xcrun",
                "notarytool",
                "submit",
                str(package),
                "--keychain-profile",
                notary_profile,
                "--wait",
            ]
        )
        _run(["xcrun", "stapler", "staple", str(package)])
    return package


def _build_windows(executable: Path, output: Path, version: str, work: Path) -> Path:
    source = work / "nimbledesk.exe"
    shutil.copy2(executable, source)
    product_code = uuid.uuid5(uuid.NAMESPACE_URL, "https://nimbledesk.io/windows/product")
    upgrade_code = uuid.uuid5(uuid.NAMESPACE_URL, "https://nimbledesk.io/windows/upgrade")
    definition = work / "NimbleDesk.wxs"
    definition.write_text(
        f'''<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name="NimbleDesk" Manufacturer="NimbleDesk" Version="{version}"
           UpgradeCode="{upgrade_code}" Scope="perUser">
    <MajorUpgrade DowngradeErrorMessage="A newer NimbleDesk version is installed." />
    <MediaTemplate EmbedCab="yes" />
    <StandardDirectory Id="LocalAppDataFolder">
      <Directory Id="INSTALLFOLDER" Name="NimbleDesk">
        <Component Id="NimbleDeskExecutable" Guid="{product_code}">
          <File Id="NimbleDeskExe" Source="{source}" KeyPath="yes" />
          <Environment Id="NimbleDeskPath" Name="PATH" Value="[INSTALLFOLDER]"
                       Permanent="no" Part="last" Action="set" System="no" />
        </Component>
      </Directory>
    </StandardDirectory>
    <Feature Id="MainFeature">
      <ComponentRef Id="NimbleDeskExecutable" />
    </Feature>
  </Package>
</Wix>
''',
        encoding="utf-8",
    )
    installer = output / f"NimbleDesk-{version}-Windows.msi"
    _run(["wix", "build", str(definition), "-arch", "x64", "-o", str(installer)])
    certificate = os.environ.get("NIMBLEDESK_WINDOWS_SIGNING_CERTIFICATE")
    if certificate:
        password = os.environ.get("NIMBLEDESK_WINDOWS_SIGNING_PASSWORD")
        if not password:
            raise RuntimeError("Windows signing certificate was set without its password")
        _run(
            [
                "signtool",
                "sign",
                "/fd",
                "SHA256",
                "/tr",
                "http://timestamp.digicert.com",
                "/td",
                "SHA256",
                "/f",
                certificate,
                "/p",
                password,
                str(installer),
            ],
            redact=(password,),
        )
    return installer


def _build_linux(executable: Path, output: Path, version: str, work: Path) -> Path:
    package_root = work / "deb"
    binary = package_root / "usr" / "bin" / "nimbledesk"
    binary.parent.mkdir(parents=True)
    shutil.copy2(executable, binary)
    binary.chmod(0o755)
    control = package_root / "DEBIAN" / "control"
    control.parent.mkdir()
    architecture = _linux_architecture(platform.machine())
    control.write_text(
        "Package: nimbledesk\n"
        f"Version: {version}\n"
        f"Architecture: {architecture}\n"
        "Maintainer: NimbleDesk <releases@nimbledesk.local>\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Description: Local desktop automation and creative media runtime\n",
        encoding="utf-8",
    )
    installer = output / f"nimbledesk_{version}_{architecture}.deb"
    _run(["dpkg-deb", "--root-owner-group", "--build", str(package_root), str(installer)])
    return installer


def _linux_architecture(machine: str) -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine.lower(), machine.lower())


def _native_version(version: str) -> str:
    candidate = version.removeprefix("v").split("-", 1)[0]
    parts = candidate.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"installer version must start with a numeric version: {version}")
    return ".".join((*parts, *("0" for _ in range(3 - len(parts)))))


def _run(command: Sequence[str], *, redact: Sequence[str] = ()) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode == 0:
        return
    message = completed.stderr.strip() or completed.stdout.strip()
    for secret in redact:
        message = message.replace(secret, "[redacted]")
    raise RuntimeError(message or f"installer command failed: {command[0]}")


if __name__ == "__main__":
    main()
