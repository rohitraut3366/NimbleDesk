from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import IO, Any, BinaryIO

from nimbledesk.adapters.limits import MAXIMUM_WORKER_MEMORY_BYTES


class WindowsManagedProcess:
    """Small Popen-compatible wrapper around a Job Object worker."""

    stdin: IO[bytes] | None = None

    def __init__(
        self,
        process_handle: Any,
        job_handle: Any,
        null_input: BinaryIO,
        modules: dict[str, ModuleType],
        command: list[str],
        process_id: int,
    ) -> None:
        self._process_handle = process_handle
        self._job_handle = job_handle
        self._null_input = null_input
        self._modules = modules
        self._command = command
        self.pid = process_id
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        win32con = self._modules["win32con"]
        win32event = self._modules["win32event"]
        if win32event.WaitForSingleObject(self._process_handle, 0) == win32con.WAIT_TIMEOUT:
            return None
        self.returncode = int(
            self._modules["win32process"].GetExitCodeProcess(self._process_handle)
        )
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        win32con = self._modules["win32con"]
        win32event = self._modules["win32event"]
        milliseconds = win32event.INFINITE if timeout is None else max(0, round(timeout * 1_000))
        if (
            win32event.WaitForSingleObject(self._process_handle, milliseconds)
            == win32con.WAIT_TIMEOUT
        ):
            raise subprocess.TimeoutExpired(
                self._command, 0 if timeout is None else timeout
            )
        result = self.poll()
        assert result is not None
        return result

    def terminate(self) -> None:
        self._modules["win32api"].TerminateProcess(self._process_handle, 1)

    def kill(self) -> None:
        self.terminate()

    def close(self) -> None:
        win32api = self._modules["win32api"]
        if self._process_handle is not None:
            win32api.CloseHandle(self._process_handle)
            self._process_handle = None
        if self._job_handle is not None:
            win32api.CloseHandle(self._job_handle)
            self._job_handle = None
        self._null_input.close()


def start_windows_restricted_process(
    command: list[str],
    *,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
    environment: dict[str, str],
    cwd: Path,
) -> WindowsManagedProcess:
    modules = _windows_modules()
    win32api = modules["win32api"]
    win32con = modules["win32con"]
    win32job = modules["win32job"]
    win32process = modules["win32process"]
    win32security = modules["win32security"]
    windows_msvcrt = importlib.import_module("msvcrt")
    null_input = open(os.devnull, "rb")  # noqa: SIM115 - owned by returned process
    for file in (null_input, stdout_file, stderr_file):
        os.set_inheritable(file.fileno(), True)
    input_handle = windows_msvcrt.get_osfhandle(null_input.fileno())
    output_handle = windows_msvcrt.get_osfhandle(stdout_file.fileno())
    error_handle = windows_msvcrt.get_osfhandle(stderr_file.fileno())

    current_token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ALL_ACCESS
    )
    restricted_token = None
    process_handle = None
    thread_handle = None
    job_handle = None
    try:
        restriction_flags = win32security.DISABLE_MAX_PRIVILEGE | getattr(
            win32security, "LUA_TOKEN", 0
        )
        restricted_token = win32security.CreateRestrictedToken(
            current_token,
            restriction_flags,
            [],
            [],
            [],
        )
        startup = win32process.STARTUPINFO()
        startup.dwFlags |= win32con.STARTF_USESTDHANDLES
        startup.hStdInput = input_handle
        startup.hStdOutput = output_handle
        startup.hStdError = error_handle
        process_handle, thread_handle, process_id, _thread_id = win32process.CreateProcessAsUser(
            restricted_token,
            None,
            subprocess.list2cmdline(command),
            None,
            None,
            True,
            win32con.CREATE_NO_WINDOW
            | win32con.CREATE_SUSPENDED
            | win32con.CREATE_UNICODE_ENVIRONMENT,
            environment,
            str(cwd),
            startup,
        )
        job_handle = win32job.CreateJobObject(None, "")
        _configure_job(job_handle, modules, active_process_limit=1)
        win32job.AssignProcessToJobObject(job_handle, process_handle)
        win32process.ResumeThread(thread_handle)
        win32api.CloseHandle(thread_handle)
        thread_handle = None
        return WindowsManagedProcess(
            process_handle, job_handle, null_input, modules, command, int(process_id)
        )
    except Exception:
        if process_handle is not None:
            win32api.TerminateProcess(process_handle, 1)
            win32api.CloseHandle(process_handle)
        if thread_handle is not None:
            win32api.CloseHandle(thread_handle)
        if job_handle is not None:
            win32api.CloseHandle(job_handle)
        null_input.close()
        raise
    finally:
        win32api.CloseHandle(current_token)
        if restricted_token is not None:
            win32api.CloseHandle(restricted_token)


def start_windows_job_process(
    command: list[str],
    *,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
    environment: dict[str, str],
    cwd: Path,
) -> WindowsManagedProcess:
    """Start a normal-token worker suspended, contain it, and then resume it."""

    modules = _windows_modules()
    win32api = modules["win32api"]
    win32con = modules["win32con"]
    win32job = modules["win32job"]
    win32process = modules["win32process"]
    windows_msvcrt = importlib.import_module("msvcrt")
    null_input = open(os.devnull, "rb")  # noqa: SIM115 - owned by returned process
    for file in (null_input, stdout_file, stderr_file):
        os.set_inheritable(file.fileno(), True)
    startup = win32process.STARTUPINFO()
    startup.dwFlags |= win32con.STARTF_USESTDHANDLES
    startup.hStdInput = windows_msvcrt.get_osfhandle(null_input.fileno())
    startup.hStdOutput = windows_msvcrt.get_osfhandle(stdout_file.fileno())
    startup.hStdError = windows_msvcrt.get_osfhandle(stderr_file.fileno())
    process_handle = None
    thread_handle = None
    job_handle = None
    try:
        process_handle, thread_handle, process_id, _thread_id = win32process.CreateProcess(
            None,
            subprocess.list2cmdline(command),
            None,
            None,
            True,
            win32con.CREATE_NO_WINDOW
            | win32con.CREATE_SUSPENDED
            | win32con.CREATE_UNICODE_ENVIRONMENT,
            environment,
            str(cwd),
            startup,
        )
        job_handle = win32job.CreateJobObject(None, "")
        _configure_job(job_handle, modules, active_process_limit=None)
        win32job.AssignProcessToJobObject(job_handle, process_handle)
        win32process.ResumeThread(thread_handle)
        win32api.CloseHandle(thread_handle)
        thread_handle = None
        return WindowsManagedProcess(
            process_handle, job_handle, null_input, modules, command, int(process_id)
        )
    except Exception:
        if process_handle is not None:
            win32api.TerminateProcess(process_handle, 1)
            win32api.CloseHandle(process_handle)
        if thread_handle is not None:
            win32api.CloseHandle(thread_handle)
        if job_handle is not None:
            win32api.CloseHandle(job_handle)
        null_input.close()
        raise


def _windows_modules() -> dict[str, ModuleType]:
    return {
        name: importlib.import_module(name)
        for name in (
            "win32api",
            "win32con",
            "win32event",
            "win32job",
            "win32process",
            "win32security",
        )
    }


def _configure_job(
    job_handle: Any,
    modules: dict[str, ModuleType],
    *,
    active_process_limit: int | None,
) -> None:
    win32job = modules["win32job"]
    limits = win32job.QueryInformationJobObject(
        job_handle, win32job.JobObjectExtendedLimitInformation
    )
    limits["BasicLimitInformation"]["LimitFlags"] |= (
        win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
        | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
    )
    if active_process_limit is not None:
        limits["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        limits["BasicLimitInformation"]["ActiveProcessLimit"] = active_process_limit
    limits["ProcessMemoryLimit"] = MAXIMUM_WORKER_MEMORY_BYTES
    limits["JobMemoryLimit"] = MAXIMUM_WORKER_MEMORY_BYTES
    win32job.SetInformationJobObject(
        job_handle,
        win32job.JobObjectExtendedLimitInformation,
        limits,
    )
