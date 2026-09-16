from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch, raises

import nimbledesk.adapters.windows_process as windows_process
from nimbledesk.adapters.limits import MAXIMUM_WORKER_MEMORY_BYTES


def test_normal_token_job_contains_descendants_without_active_process_limit(
) -> None:
    configured: list[dict[str, Any]] = []
    job_handle = object()
    win32job = SimpleNamespace(
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS=0b0001,
        JOB_OBJECT_LIMIT_JOB_MEMORY=0b0010,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0b0100,
        JOB_OBJECT_LIMIT_PROCESS_MEMORY=0b1000,
        JobObjectExtendedLimitInformation=9,
        QueryInformationJobObject=lambda _handle, _kind: {
            "BasicLimitInformation": {"LimitFlags": 0, "ActiveProcessLimit": 0},
            "ProcessMemoryLimit": 0,
            "JobMemoryLimit": 0,
        },
        SetInformationJobObject=lambda _handle, _kind, value: configured.append(value),
    )
    windows_process._configure_job(
        job_handle, {"win32job": win32job}, active_process_limit=None  # type: ignore[dict-item]
    )

    limits = configured[0]
    assert limits["BasicLimitInformation"]["LimitFlags"] == 0b1110
    assert limits["BasicLimitInformation"]["ActiveProcessLimit"] == 0
    assert limits["ProcessMemoryLimit"] == MAXIMUM_WORKER_MEMORY_BYTES
    assert limits["JobMemoryLimit"] == MAXIMUM_WORKER_MEMORY_BYTES


def test_normal_token_job_closes_handles_when_assignment_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[object] = []
    process_handle = object()
    thread_handle = object()
    job_handle = object()

    def assignment_failed(_job: object, _process: object) -> None:
        raise OSError("nested jobs unavailable")

    startup = SimpleNamespace(dwFlags=0, hStdInput=None, hStdOutput=None, hStdError=None)
    win32api = SimpleNamespace(
        CloseHandle=lambda handle: events.append(handle),
        TerminateProcess=lambda handle, _code: events.extend(("terminate", handle)),
    )
    win32con = SimpleNamespace(
        STARTF_USESTDHANDLES=1,
        CREATE_NO_WINDOW=2,
        CREATE_SUSPENDED=4,
        CREATE_UNICODE_ENVIRONMENT=8,
    )
    win32job = SimpleNamespace(
        CreateJobObject=lambda *_args: job_handle,
        AssignProcessToJobObject=assignment_failed,
    )
    win32process = SimpleNamespace(
        STARTUPINFO=lambda: startup,
        CreateProcess=lambda *_args: (process_handle, thread_handle, 4242, 7),
        ResumeThread=lambda _thread: None,
    )
    modules = {
        "win32api": win32api,
        "win32con": win32con,
        "win32job": win32job,
        "win32process": win32process,
    }
    monkeypatch.setattr(windows_process, "_windows_modules", lambda: modules)
    monkeypatch.setattr(windows_process, "_configure_job", lambda *_args, **_kwargs: None)
    real_import = windows_process.importlib.import_module
    monkeypatch.setattr(
        windows_process.importlib,
        "import_module",
        lambda name: SimpleNamespace(get_osfhandle=lambda descriptor: descriptor)
        if name == "msvcrt"
        else real_import(name),
    )

    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
        raises(OSError, match="nested jobs unavailable"),
    ):
        windows_process.start_windows_job_process(
            ["worker.exe"],
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            environment={},
            cwd=Path("C:/Temp"),
        )

    assert events == ["terminate", process_handle, process_handle, thread_handle, job_handle]


def test_normal_token_launcher_assigns_job_before_resuming(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []
    process_handle = object()
    thread_handle = object()
    job_handle = object()
    startup = SimpleNamespace(dwFlags=0, hStdInput=None, hStdOutput=None, hStdError=None)
    win32api = SimpleNamespace(
        CloseHandle=lambda _handle: None,
        TerminateProcess=lambda *_args: None,
    )
    win32con = SimpleNamespace(
        STARTF_USESTDHANDLES=1,
        CREATE_NO_WINDOW=2,
        CREATE_SUSPENDED=4,
        CREATE_UNICODE_ENVIRONMENT=8,
    )
    win32job = SimpleNamespace(
        CreateJobObject=lambda *_args: job_handle,
        AssignProcessToJobObject=lambda job, process: (
            events.append("assign")
            if (job, process) == (job_handle, process_handle)
            else None
        ),
    )
    win32process = SimpleNamespace(
        STARTUPINFO=lambda: startup,
        CreateProcess=lambda *_args: (process_handle, thread_handle, 4242, 7),
        ResumeThread=lambda thread: (
            events.append("resume") if thread is thread_handle else None
        ),
    )
    modules = {
        "win32api": win32api,
        "win32con": win32con,
        "win32job": win32job,
        "win32process": win32process,
    }
    monkeypatch.setattr(windows_process, "_windows_modules", lambda: modules)
    monkeypatch.setattr(windows_process, "_configure_job", lambda *_args, **_kwargs: None)
    real_import = windows_process.importlib.import_module
    monkeypatch.setattr(
        windows_process.importlib,
        "import_module",
        lambda name: SimpleNamespace(get_osfhandle=lambda descriptor: descriptor)
        if name == "msvcrt"
        else real_import(name),
    )

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = windows_process.start_windows_job_process(
            ["worker.exe", "run"],
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            environment={"SYSTEMROOT": "C:\\Windows"},
            cwd=Path("C:/Temp"),
        )
        process.close()

    assert process.pid == 4242
    assert events == ["assign", "resume"]
