from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
import uuid
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO

from nimbledesk.adapters.windows_process import (
    WindowsRestrictedProcess,
    _configure_job,
    _windows_modules,
)

APP_CONTAINER_READ = 0x001200A9
APP_CONTAINER_WRITE = 0x001301BF
CONTAINER_INHERIT_ACE = 0x02
OBJECT_INHERIT_ACE = 0x01
SECURITY_CAPABILITIES_ATTRIBUTE = 0x00020009
INTERNET_CLIENT_CAPABILITY = "S-1-15-3-1"
SE_GROUP_ENABLED = 0x00000004


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", wintypes.LPVOID),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class WindowsAppContainerProcess(WindowsRestrictedProcess):
    def __init__(
        self,
        process_handle: Any,
        job_handle: Any,
        null_input: BinaryIO,
        modules: dict[str, ModuleType],
        command: list[str],
        cleanup: Callable[[], None],
    ) -> None:
        super().__init__(process_handle, job_handle, null_input, modules, command)
        self._appcontainer_cleanup = cleanup

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._appcontainer_cleanup()


def start_windows_appcontainer_process(
    command: list[str],
    *,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
    environment: dict[str, str],
    cwd: Path,
    readable_paths: tuple[Path, ...],
    writable_paths: tuple[Path, ...],
    network_access: bool,
) -> WindowsAppContainerProcess:
    modules = _windows_modules()
    win32api = modules["win32api"]
    win32con = modules["win32con"]
    win32job = modules["win32job"]
    windows_msvcrt = importlib.import_module("msvcrt")
    windows_dll: Any = ctypes.__dict__["WinDLL"]
    windows_error: Any = ctypes.__dict__["WinError"]
    get_last_error: Any = ctypes.__dict__["get_last_error"]
    userenv = windows_dll("userenv", use_last_error=True)
    kernel32 = windows_dll("kernel32", use_last_error=True)
    advapi32 = windows_dll("advapi32", use_last_error=True)
    userenv.CreateAppContainerProfile.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    )
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = (wintypes.LPCWSTR,)
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    advapi32.ConvertStringSidToSidW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)
    kernel32.LocalFree.restype = wintypes.LPVOID
    kernel32.InitializeProcThreadAttributeList.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = (wintypes.LPVOID,)
    kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    )
    kernel32.CreateProcessW.restype = wintypes.BOOL

    profile_name = f"NimbleDesk.Adapter.{uuid.uuid4()}"
    appcontainer_sid = wintypes.LPVOID()
    result = userenv.CreateAppContainerProfile(
        profile_name, "NimbleDesk adapter", "Ephemeral isolated adapter", None, 0,
        ctypes.byref(appcontainer_sid),
    )
    if result != 0:
        raise windows_error(result)
    try:
        appcontainer_sid_string = _sid_string(
            appcontainer_sid, advapi32, kernel32, windows_error, get_last_error
        )
    except Exception:
        userenv.DeleteAppContainerProfile(profile_name)
        kernel32.LocalFree(appcontainer_sid)
        raise

    acl_restorers: list[Callable[[], None]] = []
    null_input: BinaryIO | None = None
    process_handle: Any = None
    thread_handle: Any = None
    job_handle: Any = None
    attribute_buffer: Any = None
    capability_sid = wintypes.LPVOID()
    profile_cleaned = False

    def cleanup_profile() -> None:
        nonlocal profile_cleaned
        if profile_cleaned:
            return
        profile_cleaned = True
        cleanup_errors: list[Exception] = []
        for restore in reversed(acl_restorers):
            try:
                restore()
            except Exception as error:
                cleanup_errors.append(error)
        delete_result = userenv.DeleteAppContainerProfile(profile_name)
        if delete_result != 0:
            cleanup_errors.append(windows_error(delete_result))
        if appcontainer_sid:
            kernel32.LocalFree(appcontainer_sid)
        if cleanup_errors:
            raise cleanup_errors[0]

    try:
        for path in readable_paths:
            acl_restorers.append(
                _grant_path(path, appcontainer_sid_string, APP_CONTAINER_READ, modules)
            )
        for path in writable_paths:
            acl_restorers.append(
                _grant_path(path, appcontainer_sid_string, APP_CONTAINER_WRITE, modules)
            )

        capabilities: Any = None
        capability_count = 0
        if network_access:
            if not advapi32.ConvertStringSidToSidW(
                INTERNET_CLIENT_CAPABILITY, ctypes.byref(capability_sid)
            ):
                raise windows_error(get_last_error())
            capabilities = (SID_AND_ATTRIBUTES * 1)(
                SID_AND_ATTRIBUTES(capability_sid, SE_GROUP_ENABLED)
            )
            capability_count = 1
        security_capabilities = SECURITY_CAPABILITIES(
            appcontainer_sid,
            capabilities,
            capability_count,
            0,
        )

        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_buffer, 1, 0, ctypes.byref(attribute_size)
        ):
            raise windows_error(get_last_error())
        if not kernel32.UpdateProcThreadAttribute(
            attribute_buffer,
            0,
            SECURITY_CAPABILITIES_ATTRIBUTE,
            ctypes.byref(security_capabilities),
            ctypes.sizeof(security_capabilities),
            None,
            None,
        ):
            raise windows_error(get_last_error())

        null_input = open(os.devnull, "rb")  # noqa: SIM115 - owned by returned process
        for file in (null_input, stdout_file, stderr_file):
            os.set_inheritable(file.fileno(), True)
        startup = STARTUPINFOEXW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdInput = windows_msvcrt.get_osfhandle(null_input.fileno())
        startup.hStdOutput = windows_msvcrt.get_osfhandle(stdout_file.fileno())
        startup.hStdError = windows_msvcrt.get_osfhandle(stderr_file.fileno())
        startup.lpAttributeList = ctypes.cast(attribute_buffer, wintypes.LPVOID)
        process_information = PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        environment_block = ctypes.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0"
        )
        creation_flags = (
            win32con.CREATE_NO_WINDOW
            | win32con.CREATE_SUSPENDED
            | win32con.CREATE_UNICODE_ENVIRONMENT
            | 0x00080000  # EXTENDED_STARTUPINFO_PRESENT
        )
        if not kernel32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            True,
            creation_flags,
            environment_block,
            str(cwd),
            ctypes.byref(startup),
            ctypes.byref(process_information),
        ):
            raise windows_error(get_last_error())
        process_handle = process_information.hProcess
        thread_handle = process_information.hThread
        job_handle = win32job.CreateJobObject(None, None)
        _configure_job(job_handle, modules, active_process_limit=2)
        win32job.AssignProcessToJobObject(job_handle, process_handle)
        modules["win32process"].ResumeThread(thread_handle)
        win32api.CloseHandle(thread_handle)
        thread_handle = None
        kernel32.DeleteProcThreadAttributeList(attribute_buffer)
        attribute_buffer = None
        if capability_sid:
            kernel32.LocalFree(capability_sid)
            capability_sid = wintypes.LPVOID()
        return WindowsAppContainerProcess(
            process_handle,
            job_handle,
            null_input,
            modules,
            command,
            cleanup_profile,
        )
    except Exception:
        if process_handle is not None:
            win32api.TerminateProcess(process_handle, 1)
            win32api.CloseHandle(process_handle)
        if thread_handle is not None:
            win32api.CloseHandle(thread_handle)
        if job_handle is not None:
            win32api.CloseHandle(job_handle)
        if null_input is not None:
            null_input.close()
        cleanup_profile()
        raise
    finally:
        if attribute_buffer is not None:
            kernel32.DeleteProcThreadAttributeList(attribute_buffer)
        if capability_sid:
            kernel32.LocalFree(capability_sid)


def _grant_path(
    path: Path,
    sid_string: str,
    access_mask: int,
    modules: dict[str, ModuleType],
) -> Callable[[], None]:
    win32security = modules["win32security"]
    resolved = str(path.resolve())
    security_information = win32security.DACL_SECURITY_INFORMATION
    object_type = win32security.SE_FILE_OBJECT
    descriptor = win32security.GetNamedSecurityInfo(
        resolved, object_type, security_information
    )
    original_dacl = descriptor.GetSecurityDescriptorDacl()
    if original_dacl is None:
        return lambda: None
    dacl = _copy_dacl(original_dacl, win32security)
    sid = win32security.ConvertStringSidToSid(sid_string)
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
        access_mask,
        sid,
    )
    win32security.SetNamedSecurityInfo(
        resolved, object_type, security_information, None, None, dacl, None
    )

    def restore() -> None:
        current_descriptor = win32security.GetNamedSecurityInfo(
            resolved, object_type, security_information
        )
        current_dacl = current_descriptor.GetSecurityDescriptorDacl()
        restored_dacl = _copy_dacl(current_dacl, win32security, excluded_sid=sid_string)
        win32security.SetNamedSecurityInfo(
            resolved,
            object_type,
            security_information,
            None,
            None,
            restored_dacl,
            None,
        )

    return restore


def _copy_dacl(
    dacl: Any,
    win32security: ModuleType,
    *,
    excluded_sid: str | None = None,
) -> Any:
    acl_size = dacl.GetAclSize()["AclBytesInUse"] if dacl is not None else 0
    copied = win32security.ACL(max(1_024, int(acl_size) + 256))
    if dacl is not None:
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            if excluded_sid is None or str(ace[-1]) != excluded_sid:
                copied.AddAce(win32security.ACL_REVISION, copied.GetAceCount(), ace)
    return copied


def _sid_string(
    sid: Any,
    advapi32: Any,
    kernel32: Any,
    windows_error: Any,
    get_last_error: Any,
) -> str:
    sid_text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
        raise windows_error(get_last_error())
    try:
        return str(sid_text.value)
    finally:
        kernel32.LocalFree(sid_text)
