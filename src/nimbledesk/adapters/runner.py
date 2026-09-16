from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import IO, Any, Protocol

from nimbledesk.adapters.limits import MAXIMUM_WORKER_MEMORY_BYTES
from nimbledesk.adapters.models import AdapterInvocation, AdapterManifest, AdapterResult

psutil: Any = import_module("psutil")

MAXIMUM_RESPONSE_BYTES = 1_000_000
MAXIMUM_STDERR_BYTES = 65_536


class AdapterError(RuntimeError):
    pass


class AdapterProcess(Protocol):
    stdin: IO[bytes] | None
    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


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
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["NIMBLEDESK_ADAPTER_TIMEOUT_SECONDS"] = str(
            specification.timeout_seconds
        )
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
            if manifest.package_path is not None:
                package_path = _canonical_without_symlinks(
                    manifest.package_path, "adapter package path"
                )
                if not package_path.is_dir():
                    raise AdapterError("adapter package path must be an existing directory")
                environment["NIMBLEDESK_ADAPTER_PACKAGE"] = str(package_path)
            worker_command = _adapter_worker_command(manifest.entrypoint)
            if manifest.isolation == "sandboxed":
                worker_command = _sandboxed_worker_command(
                    worker_command,
                    manifest,
                    arguments,
                    granted_paths,
                    scratch,
                )
            process: AdapterProcess
            if manifest.isolation == "sandboxed" and platform.system() == "Windows":
                request_path = scratch / "request.json"
                request_path.write_bytes(payload)
                environment["NIMBLEDESK_ADAPTER_REQUEST"] = str(request_path)
                if getattr(sys, "frozen", False):
                    from nimbledesk.adapters.windows_appcontainer import (
                        start_windows_appcontainer_process,
                    )

                    readable_paths, writable_paths = _windows_appcontainer_paths(
                        worker_command,
                        manifest,
                        arguments,
                        granted_paths,
                        scratch,
                    )
                    process = start_windows_appcontainer_process(
                        worker_command,
                        stdout_file=stdout_file,
                        stderr_file=stderr_file,
                        environment=environment,
                        cwd=scratch,
                        readable_paths=readable_paths,
                        writable_paths=writable_paths,
                        network_access=manifest.network_access,
                    )
                else:
                    from nimbledesk.adapters.windows_process import start_windows_restricted_process

                    process = start_windows_restricted_process(
                        worker_command,
                        stdout_file=stdout_file,
                        stderr_file=stderr_file,
                        environment=environment,
                        cwd=scratch,
                    )
            else:
                process = subprocess.Popen(
                    worker_command,
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=environment,
                    cwd=scratch,
                    start_new_session=os.name == "posix",
                )
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
            deadline = time.monotonic() + specification.timeout_seconds
            while process.poll() is None:
                if cancelled and cancelled():
                    _terminate_process(process)
                    self._wait_or_kill(process)
                    raise AdapterError("adapter execution was cancelled")
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    self._wait_or_kill(process)
                    raise AdapterError("adapter execution timed out")
                if os.fstat(stdout_file.fileno()).st_size > MAXIMUM_RESPONSE_BYTES:
                    _terminate_process(process)
                    self._wait_or_kill(process)
                    raise AdapterError("adapter response exceeded the one-megabyte limit")
                if os.fstat(stderr_file.fileno()).st_size > MAXIMUM_STDERR_BYTES:
                    _terminate_process(process)
                    self._wait_or_kill(process)
                    raise AdapterError("adapter stderr exceeded the 64-kilobyte limit")
                if _process_tree_memory_bytes(process) > MAXIMUM_WORKER_MEMORY_BYTES:
                    _terminate_process(process)
                    self._wait_or_kill(process)
                    raise AdapterError("adapter exceeded the 512 MiB memory limit")
                time.sleep(0.02)
            _kill_process_group(process)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(MAXIMUM_RESPONSE_BYTES + 1)
            stderr = stderr_file.read(MAXIMUM_STDERR_BYTES + 1)
            if len(stdout) > MAXIMUM_RESPONSE_BYTES:
                _close_process(process)
                raise AdapterError("adapter response exceeded the one-megabyte limit")
            if len(stderr) > MAXIMUM_STDERR_BYTES:
                _close_process(process)
                raise AdapterError("adapter stderr exceeded the 64-kilobyte limit")
            if process.returncode != 0:
                message = stderr[:8_192].decode("utf-8", errors="replace").strip()
                _close_process(process)
                raise AdapterError(message or f"adapter worker exited with {process.returncode}")
            _close_process(process)
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
    def _wait_or_kill(process: AdapterProcess) -> None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            process.wait()
        finally:
            _close_process(process)


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


def _adapter_worker_command(entrypoint: str) -> list[str]:
    executable = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        return [executable, "adapter-worker", entrypoint]
    return [executable, "-m", "nimbledesk.adapters.worker", entrypoint]


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
    package_paths = (
        (_canonical_without_symlinks(manifest.package_path, "adapter package path"),)
        if manifest.package_path is not None
        else ()
    )
    if current_platform == "Darwin":
        sandbox = shutil.which("sandbox-exec")
        if sandbox is None:
            raise AdapterError("sandboxed adapters require sandbox-exec on macOS")
        profile = scratch / "adapter.sb"
        profile.write_text(
            _macos_sandbox_profile(
                (*granted_paths, *package_paths),
                writable_paths,
                scratch,
                manifest.network_access,
            ),
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
            "--unshare-all",
            "--tmpfs",
            "/",
        ]
        readable_paths = {
            *(_existing_path(path) for path in _linux_runtime_paths()),
            *(_canonical_without_symlinks(path, "granted path") for path in granted_paths),
            *package_paths,
        }
        readable = tuple(path for path in readable_paths if path is not None)
        created_directories: set[Path] = set()
        for path in (Path("/proc"), Path("/dev"), *readable, scratch, *writable_paths):
            destination = path.resolve()
            _bubblewrap_directory(
                command,
                destination if destination.is_dir() else destination.parent,
                created_directories,
            )
        command.extend(("--proc", "/proc", "--dev", "/dev"))
        for path in sorted(readable, key=lambda item: len(item.parts)):
            _bubblewrap_bind(command, "--ro-bind", path, created_directories)
        _bubblewrap_bind(command, "--bind", scratch, created_directories)
        for path in writable_paths:
            _bubblewrap_bind(command, "--bind", path, created_directories)
        if manifest.network_access:
            command.append("--share-net")
        return [*command, "--", *worker_command]
    if current_platform == "Windows":
        return worker_command
    raise AdapterError("sandboxed adapters are not available on this platform")


def _windows_appcontainer_paths(
    worker_command: list[str],
    manifest: AdapterManifest,
    arguments: dict[str, object],
    granted_paths: tuple[Path, ...],
    scratch: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    executable = _canonical_without_symlinks(Path(worker_command[0]), "worker executable")
    package_paths = (
        (_canonical_without_symlinks(manifest.package_path, "adapter package path"),)
        if manifest.package_path is not None
        else ()
    )
    readable_paths = tuple(
        dict.fromkeys(
            (
                executable,
                *(_canonical_without_symlinks(path, "granted path") for path in granted_paths),
                *package_paths,
            )
        )
    )
    writable_paths = tuple(
        dict.fromkeys(
            (
                scratch.resolve(),
                *(
                    _canonical_without_symlinks(
                        Path(str(arguments[name])),
                        f"writable adapter path argument {name}",
                    )
                    for name in manifest.writable_path_arguments
                    if name in arguments
                ),
            )
        )
    )
    return readable_paths, writable_paths


def _close_process(process: AdapterProcess) -> None:
    close = getattr(process, "close", None)
    if close is not None:
        close()


def _terminate_process(process: AdapterProcess) -> None:
    process_id = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(process_id, int):
        try:
            os.killpg(process_id, signal.SIGTERM)
            return
        except ProcessLookupError:
            return
    process.terminate()


def _kill_process(process: AdapterProcess) -> None:
    process_id = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(process_id, int):
        try:
            os.killpg(process_id, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _kill_process_group(process: AdapterProcess) -> None:
    if process.poll() is None:
        return
    process_id = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(process_id, int):
        with suppress(ProcessLookupError):
            os.killpg(process_id, signal.SIGKILL)


def _process_tree_memory_bytes(process: AdapterProcess) -> int:
    process_id = getattr(process, "pid", None)
    if not isinstance(process_id, int):
        return 0
    try:
        root = psutil.Process(process_id)
        processes = (root, *root.children(recursive=True))
        return sum(candidate.memory_info().rss for candidate in processes)
    except (psutil.Error, ProcessLookupError):
        return 0


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
    lines.append(_macos_write_rule(scratch))
    lines.extend(_macos_write_rule(path) for path in writable_paths)
    if network_access:
        lines.append("(allow network*)")
    return "\n".join(lines) + "\n"


def _sandbox_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def _macos_write_rule(path: Path) -> str:
    escaped = _sandbox_path(path)
    if path.is_dir():
        return f'(allow file-write* (literal "{escaped}") (subpath "{escaped}"))'
    return f'(allow file-write* (literal "{escaped}"))'


def _linux_runtime_paths() -> tuple[Path, ...]:
    system_paths = (
        Path("/usr"),
        Path("/bin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc/ld.so.cache"),
        Path("/etc/ld.so.conf"),
        Path("/etc/ld.so.conf.d"),
        Path("/etc/nsswitch.conf"),
        Path("/etc/passwd"),
        Path("/etc/group"),
        Path("/etc/hosts"),
        Path("/etc/resolv.conf"),
        Path("/etc/ssl"),
        Path("/etc/localtime"),
    )
    return (
        *system_paths,
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(__file__).resolve().parents[2],
    )


def _existing_path(path: Path) -> Path | None:
    return path.resolve() if path.exists() else None


def _bubblewrap_bind(
    command: list[str], flag: str, path: Path, created_directories: set[Path]
) -> None:
    resolved = path.resolve()
    parent = resolved if resolved.is_dir() else resolved.parent
    _bubblewrap_directory(command, parent, created_directories)
    command.extend((flag, str(resolved), str(resolved)))


def _bubblewrap_directory(
    command: list[str], directory: Path, created_directories: set[Path]
) -> None:
    missing = [
        parent
        for parent in (directory, *directory.parents)
        if parent != Path("/") and parent not in created_directories
    ]
    for parent in reversed(missing):
        command.extend(("--dir", str(parent)))
        created_directories.add(parent)
