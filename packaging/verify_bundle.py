from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path


def verify_bundle(executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="nimbledesk-bundle-contract-") as temporary:
        root = Path(temporary)
        request = root / "adapter-request.json"
        request.write_text(
            json.dumps(
                {
                    "command": "inspect",
                    "arguments": {"project": "bundle-contract"},
                }
            ),
            encoding="utf-8",
        )
        adapter_package = root / "adapter-package"
        adapter_package.mkdir()
        (adapter_package / "bundle_external.py").write_text(
            "def handle(command, arguments):\n"
            "    return {'command': command, 'value': arguments['project']}\n",
            encoding="utf-8",
        )
        adapter = subprocess.run(
            [
                str(executable),
                "adapter-worker",
                "bundle_external:handle",
            ],
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "NIMBLEDESK_ADAPTER_REQUEST": str(request),
                "NIMBLEDESK_ADAPTER_PACKAGE": str(adapter_package),
            },
            timeout=60,
        )
        if adapter.returncode != 0:
            raise RuntimeError(
                "bundled adapter worker failed: "
                + adapter.stderr.decode("utf-8", errors="replace")[:8_192]
            )
        payload = json.loads(adapter.stdout)
        if payload.get("success") is not True or payload.get("result") != {
            "command": "inspect",
            "value": "bundle-contract",
        }:
            raise RuntimeError("bundled adapter worker returned an invalid contract result")

        davinci_result = root / "davinci-result.json"
        davinci = subprocess.run(
            [
                str(executable),
                "davinci-worker",
                str(root / "missing-plan.json"),
                str(root / "missing-timeline.fcpxml"),
                str(root / "output"),
                "0",
                "1",
                str(davinci_result),
                str(root / "cancel"),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if davinci.returncode != 1 or not davinci_result.is_file():
            raise RuntimeError("bundled DaVinci worker did not return its structured failure")
        davinci_payload = json.loads(davinci_result.read_text(encoding="utf-8"))
        if (
            davinci_payload.get("success") is not False
            or "missing-plan.json" not in str(davinci_payload.get("error", ""))
        ):
            raise RuntimeError("bundled DaVinci worker returned an invalid failure contract")

        isolation_report = root / "adapter-isolation-contract.json"
        isolation_contract = subprocess.run(
            [
                str(executable),
                "adapter-isolation-contract",
                "--target-id",
                f"bundle-{platform.system().lower()}-{platform.machine().lower()}",
                "--output",
                str(isolation_report),
            ],
            capture_output=True,
            check=False,
            timeout=180,
        )
        if isolation_contract.returncode != 0 or not isolation_report.is_file():
            raise RuntimeError(
                "bundled adapter isolation contract failed: "
                + isolation_contract.stderr.decode("utf-8", errors="replace")[:8_192]
            )
        if json.loads(isolation_report.read_text(encoding="utf-8")).get("passed") is not True:
            raise RuntimeError("bundled cross-platform adapter isolation did not pass")

        if platform.system() == "Windows":
            windows_report = root / "windows-adapter-contract.json"
            windows_contract = subprocess.run(
                [
                    str(executable),
                    "windows-adapter-contract",
                    "--output",
                    str(windows_report),
                ],
                capture_output=True,
                check=False,
                timeout=120,
            )
            if windows_contract.returncode != 0 or not windows_report.is_file():
                raise RuntimeError(
                    "bundled Windows adapter contract failed: "
                    + windows_contract.stderr.decode("utf-8", errors="replace")[:8_192]
                )
            if json.loads(windows_report.read_text(encoding="utf-8")).get("passed") is not True:
                raise RuntimeError("bundled Windows adapter isolation did not pass")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify internal standalone worker routes")
    parser.add_argument("executable", type=Path)
    verify_bundle(parser.parse_args().executable)
