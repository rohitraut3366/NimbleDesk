from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch, raises

import nimbledesk.adapters.windows_process as windows_process
from nimbledesk.adapters.limits import MAXIMUM_WORKER_MEMORY_BYTES


class FakePopen:
    pid = 4242


def test_normal_token_job_contains_descendants_without_active_process_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    closed: list[object] = []
    assigned: list[tuple[object, object]] = []
    configured: list[dict[str, Any]] = []
    process_handle = object()
    job_handle = object()
    win32api = SimpleNamespace(
        OpenProcess=lambda access, inherit, process_id: (
            process_handle
            if (access, inherit, process_id) == (0b11, False, FakePopen.pid)
            else None
        ),
        CloseHandle=lambda handle: closed.append(handle),
    )
    win32con = SimpleNamespace(PROCESS_SET_QUOTA=0b01, PROCESS_TERMINATE=0b10)
    win32job = SimpleNamespace(
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS=0b0001,
        JOB_OBJECT_LIMIT_JOB_MEMORY=0b0010,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0b0100,
        JOB_OBJECT_LIMIT_PROCESS_MEMORY=0b1000,
        JobObjectExtendedLimitInformation=9,
        CreateJobObject=lambda _security, _name: job_handle,
        QueryInformationJobObject=lambda _handle, _kind: {
            "BasicLimitInformation": {"LimitFlags": 0, "ActiveProcessLimit": 0},
            "ProcessMemoryLimit": 0,
            "JobMemoryLimit": 0,
        },
        SetInformationJobObject=lambda _handle, _kind, value: configured.append(value),
        AssignProcessToJobObject=lambda job, process: assigned.append((job, process)),
    )
    monkeypatch.setattr(
        windows_process,
        "_windows_modules",
        lambda: {"win32api": win32api, "win32con": win32con, "win32job": win32job},
    )

    job = windows_process.attach_process_to_windows_job(FakePopen())  # type: ignore[arg-type]
    job.close()

    assert assigned == [(job_handle, process_handle)]
    assert closed == [process_handle, job_handle]
    limits = configured[0]
    assert limits["BasicLimitInformation"]["LimitFlags"] == 0b1110
    assert limits["BasicLimitInformation"]["ActiveProcessLimit"] == 0
    assert limits["ProcessMemoryLimit"] == MAXIMUM_WORKER_MEMORY_BYTES
    assert limits["JobMemoryLimit"] == MAXIMUM_WORKER_MEMORY_BYTES


def test_normal_token_job_closes_handles_when_assignment_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    closed: list[object] = []
    process_handle = object()
    job_handle = object()

    def assignment_failed(_job: object, _process: object) -> None:
        raise OSError("nested jobs unavailable")

    win32api = SimpleNamespace(
        OpenProcess=lambda *_args: process_handle,
        CloseHandle=lambda handle: closed.append(handle),
    )
    win32con = SimpleNamespace(PROCESS_SET_QUOTA=1, PROCESS_TERMINATE=2)
    win32job = SimpleNamespace(
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS=1,
        JOB_OBJECT_LIMIT_JOB_MEMORY=2,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=4,
        JOB_OBJECT_LIMIT_PROCESS_MEMORY=8,
        JobObjectExtendedLimitInformation=9,
        CreateJobObject=lambda _security, _name: job_handle,
        QueryInformationJobObject=lambda _handle, _kind: {
            "BasicLimitInformation": {"LimitFlags": 0},
            "ProcessMemoryLimit": 0,
            "JobMemoryLimit": 0,
        },
        SetInformationJobObject=lambda *_args: None,
        AssignProcessToJobObject=assignment_failed,
    )
    monkeypatch.setattr(
        windows_process,
        "_windows_modules",
        lambda: {"win32api": win32api, "win32con": win32con, "win32job": win32job},
    )

    with raises(OSError, match="nested jobs unavailable"):
        windows_process.attach_process_to_windows_job(FakePopen())  # type: ignore[arg-type]

    assert closed == [job_handle, process_handle]


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
