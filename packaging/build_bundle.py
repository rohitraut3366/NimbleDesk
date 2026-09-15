from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

from verify_bundle import verify_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "pyinstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(root / "dist" / "bundle"),
            "--workpath",
            str(root / "build" / "pyinstaller"),
            str(root / "packaging" / "nimbledesk.spec"),
        ],
        cwd=root,
        env={
            **os.environ,
            "PYINSTALLER_CONFIG_DIR": str(root / "build" / "pyinstaller-config"),
        },
        check=True,
    )
    executable_name = "nimbledesk.exe" if platform.system() == "Windows" else "nimbledesk"
    executable = root / "dist" / "bundle" / executable_name
    if not executable.is_file():
        raise FileNotFoundError(executable)
    verify_bundle(executable)
    staging = root / "build" / "bundle-staging" / "NimbleDesk"
    if staging.parent.exists():
        shutil.rmtree(staging.parent)
    staging.mkdir(parents=True)
    shutil.copy2(executable, staging / executable_name)
    shutil.copy2(root / "README.md", staging / "README.md")
    arguments.output.mkdir(parents=True, exist_ok=True)
    system = platform.system().lower()
    machine = platform.machine().lower().replace("amd64", "x86_64")
    archive = arguments.output / f"nimbledesk-{system}-{machine}"
    format_name = "zip" if system == "windows" else "gztar"
    shutil.make_archive(str(archive), format_name, staging.parent, staging.name)


if __name__ == "__main__":
    main()
