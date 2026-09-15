from __future__ import annotations

import json
import os
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
        adapter = subprocess.run(
            [
                str(executable),
                "adapter-worker",
                "nimbledesk.adapters.fixture:handle",
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "NIMBLEDESK_ADAPTER_REQUEST": str(request)},
            timeout=60,
        )
        if adapter.returncode != 0:
            raise RuntimeError(
                "bundled adapter worker failed: "
                + adapter.stderr.decode("utf-8", errors="replace")[:8_192]
            )
        payload = json.loads(adapter.stdout)
        if payload.get("success") is not True or payload.get("result") != {
            "received": {"project": "bundle-contract"}
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify internal standalone worker routes")
    parser.add_argument("executable", type=Path)
    verify_bundle(parser.parse_args().executable)
