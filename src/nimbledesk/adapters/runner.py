from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from nimbledesk.adapters.models import AdapterInvocation, AdapterManifest, AdapterResult

MAXIMUM_RESPONSE_BYTES = 1_000_000


class AdapterError(RuntimeError):
    pass


class IsolatedAdapterRunner:
    def execute(
        self,
        manifest: AdapterManifest,
        command: str,
        arguments: dict[str, object],
        granted_paths: tuple[Path, ...] = (),
        cancelled: Callable[[], bool] | None = None,
    ) -> AdapterResult:
        if platform.system() not in manifest.supported_platforms:
            raise AdapterError(f"adapter does not support {platform.system()}")
        specification = manifest.commands.get(command)
        if specification is None:
            raise AdapterError(f"adapter command is not declared: {command}")
        missing = set(specification.required_arguments) - set(arguments)
        if missing:
            raise AdapterError("missing adapter arguments: " + ", ".join(sorted(missing)))
        self._validate_paths(specification.path_arguments, arguments, granted_paths)
        invocation = AdapterInvocation(command=command, arguments=arguments)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}
        }
        environment["PYTHONNOUSERSITE"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-m", "nimbledesk.adapters.worker", manifest.entrypoint],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            cwd=tempfile.gettempdir(),
        )
        payload = invocation.model_dump_json().encode("utf-8")
        deadline = time.monotonic() + specification.timeout_seconds
        first_communication = True
        while True:
            if cancelled and cancelled():
                process.terminate()
                self._wait_or_kill(process)
                raise AdapterError("adapter execution was cancelled")
            if time.monotonic() >= deadline:
                process.terminate()
                self._wait_or_kill(process)
                raise AdapterError("adapter execution timed out")
            try:
                stdout, stderr = process.communicate(
                    input=payload if first_communication else None,
                    timeout=min(0.1, deadline - time.monotonic()),
                )
                break
            except subprocess.TimeoutExpired:
                first_communication = False
        if len(stdout) > MAXIMUM_RESPONSE_BYTES:
            raise AdapterError("adapter response exceeded the one-megabyte limit")
        if process.returncode != 0:
            message = stderr[:8_192].decode("utf-8", errors="replace").strip()
            raise AdapterError(message or f"adapter worker exited with {process.returncode}")
        try:
            return AdapterResult.model_validate_json(stdout)
        except Exception as error:
            raise AdapterError("adapter returned an invalid response") from error

    @staticmethod
    def _validate_paths(
        path_arguments: tuple[str, ...],
        arguments: dict[str, object],
        granted_paths: tuple[Path, ...],
    ) -> None:
        grants = tuple(path.expanduser().resolve() for path in granted_paths)
        for name in path_arguments:
            value = arguments.get(name)
            if not isinstance(value, str):
                raise AdapterError(f"adapter path argument must be a string: {name}")
            path = Path(value).expanduser().resolve()
            if not any(path == grant or grant in path.parents for grant in grants):
                raise AdapterError(f"adapter path is outside session grants: {name}")

    @staticmethod
    def _wait_or_kill(process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
