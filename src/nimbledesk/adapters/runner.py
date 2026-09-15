from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from nimbledesk.adapters.models import AdapterInvocation, AdapterManifest, AdapterResult

MAXIMUM_RESPONSE_BYTES = 1_000_000
MAXIMUM_STDERR_BYTES = 65_536


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
        payload = invocation.model_dump_json().encode("utf-8")
        with (
            tempfile.TemporaryDirectory(prefix="nimbledesk-adapter-") as scratch_name,
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            scratch = Path(scratch_name)
            environment.update(
                {
                    "HOME": str(scratch),
                    "TMPDIR": str(scratch),
                    "TEMP": str(scratch),
                    "TMP": str(scratch),
                    "XDG_CACHE_HOME": str(scratch / "cache"),
                    "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
                }
            )
            worker_command = [
                str(Path(sys.executable).resolve()),
                "-m",
                "nimbledesk.adapters.worker",
                manifest.entrypoint,
            ]
            if manifest.isolation == "sandboxed":
                worker_command = _sandboxed_worker_command(
                    worker_command,
                    manifest,
                    arguments,
                    granted_paths,
                    scratch,
                )
            process = subprocess.Popen(
                worker_command,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=scratch,
            )
            assert process.stdin is not None
            process.stdin.write(payload)
            process.stdin.close()
            deadline = time.monotonic() + specification.timeout_seconds
            while process.poll() is None:
                if cancelled and cancelled():
                    process.terminate()
                    self._wait_or_kill(process)
                    raise AdapterError("adapter execution was cancelled")
                if time.monotonic() >= deadline:
                    process.terminate()
                    self._wait_or_kill(process)
                    raise AdapterError("adapter execution timed out")
                if os.fstat(stdout_file.fileno()).st_size > MAXIMUM_RESPONSE_BYTES:
                    process.terminate()
                    self._wait_or_kill(process)
                    raise AdapterError("adapter response exceeded the one-megabyte limit")
                if os.fstat(stderr_file.fileno()).st_size > MAXIMUM_STDERR_BYTES:
                    process.terminate()
                    self._wait_or_kill(process)
                    raise AdapterError("adapter stderr exceeded the 64-kilobyte limit")
                time.sleep(0.02)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(MAXIMUM_RESPONSE_BYTES + 1)
            stderr = stderr_file.read(MAXIMUM_STDERR_BYTES + 1)
            if len(stdout) > MAXIMUM_RESPONSE_BYTES:
                raise AdapterError("adapter response exceeded the one-megabyte limit")
            if len(stderr) > MAXIMUM_STDERR_BYTES:
                raise AdapterError("adapter stderr exceeded the 64-kilobyte limit")
            if process.returncode != 0:
                message = stderr[:8_192].decode("utf-8", errors="replace").strip()
                raise AdapterError(message or f"adapter worker exited with {process.returncode}")
        return _strict_adapter_result(stdout)

    @staticmethod
    def _validate_paths(
        path_arguments: tuple[str, ...],
        arguments: dict[str, object],
        granted_paths: tuple[Path, ...],
    ) -> None:
        grants = tuple(_canonical_without_symlinks(path, "granted path") for path in granted_paths)
        for name in path_arguments:
            value = arguments.get(name)
            if not isinstance(value, str):
                raise AdapterError(f"adapter path argument must be a string: {name}")
            path = _canonical_without_symlinks(Path(value), f"adapter path argument {name}")
            if not any(path == grant or grant in path.parents for grant in grants):
                raise AdapterError(f"adapter path is outside session grants: {name}")

    @staticmethod
    def _wait_or_kill(process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _canonical_without_symlinks(path: Path, description: str) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise AdapterError(f"{description} contains a symbolic link")
    return absolute.resolve()


def _strict_adapter_result(payload: bytes) -> AdapterResult:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AdapterError(f"adapter response contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        decoded = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
        return AdapterResult.model_validate(decoded)
    except AdapterError:
        raise
    except Exception as error:
        raise AdapterError("adapter returned an invalid response") from error


def _sandboxed_worker_command(
    worker_command: list[str],
    manifest: AdapterManifest,
    arguments: dict[str, object],
    granted_paths: tuple[Path, ...],
    scratch: Path,
) -> list[str]:
    current_platform = platform.system()
    writable_paths = tuple(
        _canonical_without_symlinks(
            Path(str(arguments[name])), f"writable adapter path argument {name}"
        )
        for name in manifest.writable_path_arguments
        if name in arguments
    )
    if current_platform == "Darwin":
        sandbox = shutil.which("sandbox-exec")
        if sandbox is None:
            raise AdapterError("sandboxed adapters require sandbox-exec on macOS")
        profile = scratch / "adapter.sb"
        profile.write_text(
            _macos_sandbox_profile(granted_paths, writable_paths, scratch, manifest.network_access),
            encoding="utf-8",
        )
        return [sandbox, "-f", str(profile), *worker_command]
    if current_platform == "Linux":
        bubblewrap = shutil.which("bwrap")
        if bubblewrap is None:
            raise AdapterError("sandboxed adapters require bubblewrap on Linux")
        command = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--bind",
            str(scratch),
            str(scratch),
        ]
        if not manifest.network_access:
            command.append("--unshare-net")
        for path in writable_paths:
            command.extend(("--bind", str(path), str(path)))
        return [*command, "--", *worker_command]
    raise AdapterError("sandboxed adapters are not available on this platform")


def _macos_sandbox_profile(
    granted_paths: tuple[Path, ...],
    writable_paths: tuple[Path, ...],
    scratch: Path,
    network_access: bool,
) -> str:
    readable = {
        Path("/System"),
        Path("/usr"),
        Path("/Library"),
        Path("/private/etc"),
        Path("/private/var/db/timezone"),
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(__file__).resolve().parents[2],
        scratch,
        *(_canonical_without_symlinks(path, "granted path") for path in granted_paths),
    }
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow ipc-posix-shm)",
        '(allow file-read* (literal "/"))',
    ]
    lines.extend(
        f'(allow file-read* file-map-executable (subpath "{_sandbox_path(path)}"))'
        for path in readable
    )
    lines.append(f'(allow file-write* (subpath "{_sandbox_path(scratch)}"))')
    lines.extend(
        f'(allow file-write* (subpath "{_sandbox_path(path)}"))' for path in writable_paths
    )
    if network_access:
        lines.append("(allow network*)")
    return "\n".join(lines) + "\n"


def _sandbox_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
