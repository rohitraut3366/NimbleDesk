from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes
from pathlib import Path
from time import sleep
from typing import Any, Protocol

from PIL import Image

from nimbledesk.backends.system_io import SystemIOBackend
from nimbledesk.protocol.models import Capability, Display, PermissionState, Point, Rectangle

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MONITORINFOF_PRIMARY = 0x0001
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
WHEEL_DELTA = 120
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
WM_CLOSE = 0x0010


class WindowsDesktopAPI(Protocol):
    def displays(self) -> tuple[tuple[int, Rectangle, float, bool], ...]: ...

    def cursor(self) -> Point: ...

    def capture(self, bounds: Rectangle) -> Image.Image: ...

    def move_pointer(self, point: Point) -> None: ...

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None: ...

    def scroll(self, horizontal: float, vertical: float) -> None: ...

    def key(self, virtual_key: int, pressed: bool) -> None: ...

    def type_text(self, text: str) -> None: ...

    def focus_window(self, handle: int) -> None: ...

    def move_window(self, handle: int, point: Point) -> None: ...

    def resize_window(self, handle: int, width: int, height: int) -> None: ...

    def minimize_window(self, handle: int) -> None: ...

    def maximize_window(self, handle: int) -> None: ...

    def close_window(self, handle: int) -> None: ...

    def read_clipboard(self) -> str: ...

    def write_clipboard(self, text: str) -> None: ...

    def launch_application(self, application_id: str) -> None: ...


class WindowsNativeBackend(SystemIOBackend):
    def __init__(self, controller: WindowsController | None = None) -> None:
        super().__init__(controller or WindowsController())


class WindowsController:
    controller_id = "windows-win32-gdi-sendinput"

    def __init__(self, api: WindowsDesktopAPI | None = None) -> None:
        self._api = api
        self._displays: tuple[Display, ...] = ()

    @property
    def available(self) -> bool:
        return self._api is not None or platform.system() == "Windows"

    def permissions(self) -> dict[Capability, PermissionState]:
        permission = PermissionState.GRANTED if self.available else PermissionState.UNAVAILABLE
        return {
            Capability.SCREEN_CAPTURE: permission,
            Capability.POINTER: permission,
            Capability.KEYBOARD: permission,
            Capability.WINDOWS: permission,
            Capability.CLIPBOARD: permission,
        }

    def displays(self) -> tuple[Display, ...]:
        self._displays = _layout_displays(self._require_api().displays())
        return self._displays

    def cursor(self) -> Point:
        physical = self._require_api().cursor()
        return self._logical_point(physical)

    def capture(self, display: Display, region: Rectangle | None) -> Image.Image:
        bounds = (
            display.physical_bounds
            if region is None
            else self._physical_rectangle(display, region)
        )
        return self._require_api().capture(bounds)

    def move_pointer(self, point: Point) -> None:
        display = self._logical_display(point)
        self._require_api().move_pointer(self._physical_point(display, point))

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None:
        display = self._logical_display(point)
        self._require_api().pointer_button(
            self._physical_point(display, point), button, pressed
        )

    def scroll(self, horizontal: float, vertical: float) -> None:
        self._require_api().scroll(horizontal, vertical)

    def key(self, key: str, pressed: bool) -> None:
        self._require_api().key(_windows_virtual_key(key), pressed)

    def type_text(self, text: str) -> None:
        self._require_api().type_text(text)

    def focus_window(self, window_id: str) -> None:
        self._require_api().focus_window(self._window_handle(window_id))

    def move_window(self, window_id: str, point: Point) -> None:
        self._require_api().move_window(self._window_handle(window_id), point)

    def resize_window(self, window_id: str, width: int, height: int) -> None:
        self._require_api().resize_window(self._window_handle(window_id), width, height)

    def minimize_window(self, window_id: str) -> None:
        self._require_api().minimize_window(self._window_handle(window_id))

    def maximize_window(self, window_id: str) -> None:
        self._require_api().maximize_window(self._window_handle(window_id))

    def close_window(self, window_id: str) -> None:
        self._require_api().close_window(self._window_handle(window_id))

    @staticmethod
    def _window_handle(window_id: str) -> int:
        try:
            handle = int(window_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as error:
            raise ValueError("Windows UIA window ID does not contain a native handle") from error
        return handle

    def read_clipboard(self) -> str:
        return self._require_api().read_clipboard()

    def write_clipboard(self, text: str) -> None:
        self._require_api().write_clipboard(text)

    def launch_application(self, application_id: str) -> None:
        self._require_api().launch_application(application_id)

    def _require_api(self) -> WindowsDesktopAPI:
        if self._api is None:
            if platform.system() != "Windows":
                raise RuntimeError("native Win32 APIs are unavailable")
            self._api = Win32DesktopAPI()
        return self._api

    def _logical_display(self, point: Point) -> Display:
        displays = self._displays or self.displays()
        display = next(
            (candidate for candidate in displays if candidate.logical_bounds.contains(point)),
            None,
        )
        if display is None:
            raise ValueError("point is outside the Windows virtual desktop")
        return display

    def _physical_display(self, point: Point) -> Display:
        displays = self._displays or self.displays()
        display = next(
            (candidate for candidate in displays if candidate.physical_bounds.contains(point)),
            None,
        )
        if display is None:
            raise ValueError("cursor is outside the Windows virtual desktop")
        return display

    def _physical_point(self, display: Display, point: Point) -> Point:
        logical = display.logical_bounds
        physical = display.physical_bounds
        return Point(
            x=physical.left + round((point.x - logical.left) * display.scale),
            y=physical.top + round((point.y - logical.top) * display.scale),
        )

    def _logical_point(self, point: Point) -> Point:
        display = self._physical_display(point)
        logical = display.logical_bounds
        physical = display.physical_bounds
        return Point(
            x=logical.left + round((point.x - physical.left) / display.scale),
            y=logical.top + round((point.y - physical.top) / display.scale),
        )

    def _physical_rectangle(self, display: Display, rectangle: Rectangle) -> Rectangle:
        start = self._physical_point(
            display, Point(x=rectangle.left, y=rectangle.top)
        )
        return Rectangle(
            left=start.x,
            top=start.y,
            width=max(1, round(rectangle.width * display.scale)),
            height=max(1, round(rectangle.height * display.scale)),
        )


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUT_UNION)]


class Win32DesktopAPI:
    def __init__(self) -> None:
        windows_dll: Any = ctypes.__dict__["WinDLL"]
        self._user32 = windows_dll("user32", use_last_error=True)
        self._gdi32 = windows_dll("gdi32", use_last_error=True)
        self._shcore = windows_dll("shcore", use_last_error=True)
        self._kernel32 = windows_dll("kernel32", use_last_error=True)
        self._shell32 = windows_dll("shell32", use_last_error=True)
        self._callback_type: Any = ctypes.__dict__["WINFUNCTYPE"](
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )
        with _ignore_os_error():
            self._user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self._configure_signatures()

    def displays(self) -> tuple[tuple[int, Rectangle, float, bool], ...]:
        result: list[tuple[int, Rectangle, float, bool]] = []
        errors: list[OSError] = []

        def received(handle: Any, _dc: Any, _rectangle: Any, _data: Any) -> bool:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if not self._user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                errors.append(_windows_error())
                return False
            bounds = info.rcMonitor
            scale = self._monitor_scale(handle)
            result.append(
                (
                    int(handle or 0),
                    Rectangle(
                        left=bounds.left,
                        top=bounds.top,
                        width=bounds.right - bounds.left,
                        height=bounds.bottom - bounds.top,
                    ),
                    scale,
                    bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
            return True

        callback = self._callback_type(received)
        enumerated = self._user32.EnumDisplayMonitors(None, None, callback, 0)
        if errors:
            raise errors[0]
        if not enumerated:
            raise _windows_error()
        return tuple(result)

    def cursor(self) -> Point:
        point = POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            raise _windows_error()
        return Point(x=point.x, y=point.y)

    def capture(self, bounds: Rectangle) -> Image.Image:
        screen_dc = self._user32.GetDC(None)
        if not screen_dc:
            raise RuntimeError("Windows GetDC failed")
        memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            self._user32.ReleaseDC(None, screen_dc)
            raise RuntimeError("Windows CreateCompatibleDC failed")
        bitmap = self._gdi32.CreateCompatibleBitmap(
            screen_dc, bounds.width, bounds.height
        )
        if not bitmap:
            self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(None, screen_dc)
            raise RuntimeError("Windows CreateCompatibleBitmap failed")
        previous = self._gdi32.SelectObject(memory_dc, bitmap)
        if not previous:
            self._gdi32.DeleteObject(bitmap)
            self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(None, screen_dc)
            raise RuntimeError("Windows SelectObject failed")
        try:
            if not self._gdi32.BitBlt(
                memory_dc,
                0,
                0,
                bounds.width,
                bounds.height,
                screen_dc,
                bounds.left,
                bounds.top,
                SRCCOPY | CAPTUREBLT,
            ):
                raise _windows_error()
            bitmap_info = BITMAPINFO()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bitmap_info.bmiHeader.biWidth = bounds.width
            bitmap_info.bmiHeader.biHeight = -bounds.height
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            buffer = ctypes.create_string_buffer(bounds.width * bounds.height * 4)
            rows = self._gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                bounds.height,
                buffer,
                ctypes.byref(bitmap_info),
                0,
            )
            if rows != bounds.height:
                raise _windows_error()
            return Image.frombuffer(
                "RGB",
                (bounds.width, bounds.height),
                buffer.raw,
                "raw",
                "BGRX",
                0,
                1,
            ).copy()
        finally:
            self._gdi32.SelectObject(memory_dc, previous)
            self._gdi32.DeleteObject(bitmap)
            self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(None, screen_dc)

    def move_pointer(self, point: Point) -> None:
        self._send_mouse(point, 0, MOUSEEVENTF_MOVE)

    def pointer_button(self, point: Point, button: str, pressed: bool) -> None:
        flag = {
            ("left", True): MOUSEEVENTF_LEFTDOWN,
            ("left", False): MOUSEEVENTF_LEFTUP,
            ("right", True): MOUSEEVENTF_RIGHTDOWN,
            ("right", False): MOUSEEVENTF_RIGHTUP,
            ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
            ("middle", False): MOUSEEVENTF_MIDDLEUP,
        }[(button, pressed)]
        self._send_mouse(point, 0, MOUSEEVENTF_MOVE | flag)

    def scroll(self, horizontal: float, vertical: float) -> None:
        cursor = self.cursor()
        if vertical:
            self._send_mouse(cursor, round(vertical * WHEEL_DELTA), MOUSEEVENTF_WHEEL)
        if horizontal:
            self._send_mouse(cursor, round(horizontal * WHEEL_DELTA), MOUSEEVENTF_HWHEEL)

    def key(self, virtual_key: int, pressed: bool) -> None:
        self._send_input(
            INPUT(
                type=1,
                ki=KEYBDINPUT(
                    wVk=virtual_key,
                    wScan=0,
                    dwFlags=0 if pressed else KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                ),
            )
        )

    def type_text(self, text: str) -> None:
        encoded = text.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[index : index + 2], "little")
            for pressed in (True, False):
                self._send_input(
                    INPUT(
                        type=1,
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=code_unit,
                            dwFlags=KEYEVENTF_UNICODE | (0 if pressed else KEYEVENTF_KEYUP),
                            time=0,
                            dwExtraInfo=0,
                        ),
                    )
                )

    def focus_window(self, handle: int) -> None:
        if not self._user32.IsWindow(handle):
            raise ValueError("Windows target handle no longer exists")
        if not self._user32.SetForegroundWindow(handle):
            raise RuntimeError(
                "Windows refused foreground activation; the target may be elevated "
                "or on a secure desktop"
            )

    def move_window(self, handle: int, point: Point) -> None:
        rectangle = self._window_rectangle(handle)
        self._set_window_bounds(handle, point.x, point.y, rectangle.width, rectangle.height)

    def resize_window(self, handle: int, width: int, height: int) -> None:
        rectangle = self._window_rectangle(handle)
        self._set_window_bounds(handle, rectangle.left, rectangle.top, width, height)

    def minimize_window(self, handle: int) -> None:
        self._show_window(handle, SW_MINIMIZE)

    def maximize_window(self, handle: int) -> None:
        self._show_window(handle, SW_MAXIMIZE)

    def close_window(self, handle: int) -> None:
        self._validate_window(handle)
        if not self._user32.PostMessageW(handle, WM_CLOSE, 0, 0):
            raise _windows_error()

    def _window_rectangle(self, handle: int) -> Rectangle:
        self._validate_window(handle)
        rectangle = RECT()
        if not self._user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            raise _windows_error()
        return Rectangle(
            left=rectangle.left,
            top=rectangle.top,
            width=rectangle.right - rectangle.left,
            height=rectangle.bottom - rectangle.top,
        )

    def _set_window_bounds(
        self, handle: int, left: int, top: int, width: int, height: int
    ) -> None:
        self._validate_window(handle)
        if not self._user32.MoveWindow(handle, left, top, width, height, True):
            raise _windows_error()

    def _show_window(self, handle: int, command: int) -> None:
        self._validate_window(handle)
        self._user32.ShowWindow(handle, command)

    def _validate_window(self, handle: int) -> None:
        if not self._user32.IsWindow(handle):
            raise ValueError("Windows target handle no longer exists")

    def read_clipboard(self) -> str:
        self._open_clipboard()
        try:
            if not self._user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = self._user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                raise _windows_error()
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                raise _windows_error()
            try:
                byte_count = int(self._kernel32.GlobalSize(handle))
                maximum_units = min(100_001, max(0, byte_count // 2))
                return ctypes.wstring_at(pointer, maximum_units).split("\0", 1)[0]
            finally:
                self._kernel32.GlobalUnlock(handle)
        finally:
            self._user32.CloseClipboard()

    def write_clipboard(self, text: str) -> None:
        encoded = (text + "\0").encode("utf-16-le")
        self._open_clipboard()
        memory = None
        try:
            if not self._user32.EmptyClipboard():
                raise _windows_error()
            memory = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not memory:
                raise _windows_error()
            pointer = self._kernel32.GlobalLock(memory)
            if not pointer:
                raise _windows_error()
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                self._kernel32.GlobalUnlock(memory)
            if not self._user32.SetClipboardData(CF_UNICODETEXT, memory):
                raise _windows_error()
            memory = None
        finally:
            if memory:
                self._kernel32.GlobalFree(memory)
            self._user32.CloseClipboard()

    def launch_application(self, application_id: str) -> None:
        candidate = Path(application_id)
        target = (
            str(candidate.resolve())
            if candidate.is_absolute() and candidate.is_file()
            else f"shell:AppsFolder\\{application_id}"
        )
        result = self._shell32.ShellExecuteW(None, "open", target, None, None, 1)
        if int(result) <= 32:
            raise RuntimeError(f"Windows application launch failed with code {int(result)}")

    def _open_clipboard(self) -> None:
        for _ in range(10):
            if self._user32.OpenClipboard(None):
                return
            sleep(0.02)
        raise RuntimeError("Windows clipboard is busy")

    def _monitor_scale(self, handle: Any) -> float:
        horizontal = wintypes.UINT()
        vertical = wintypes.UINT()
        result = self._shcore.GetDpiForMonitor(
            handle, 0, ctypes.byref(horizontal), ctypes.byref(vertical)
        )
        return horizontal.value / 96 if result == 0 and horizontal.value else 1.0

    def _send_mouse(self, point: Point, data: int, flags: int) -> None:
        displays = self.displays()
        left = min(display[1].left for display in displays)
        top = min(display[1].top for display in displays)
        right = max(display[1].left + display[1].width for display in displays)
        bottom = max(display[1].top + display[1].height for display in displays)
        normalized_x = round((point.x - left) * 65535 / max(1, right - left - 1))
        normalized_y = round((point.y - top) * 65535 / max(1, bottom - top - 1))
        self._send_input(
            INPUT(
                type=0,
                mi=MOUSEINPUT(
                    dx=normalized_x,
                    dy=normalized_y,
                    mouseData=data & 0xFFFFFFFF,
                    dwFlags=flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time=0,
                    dwExtraInfo=0,
                ),
            )
        )

    def _send_input(self, input_event: INPUT) -> None:
        if self._user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(INPUT)) != 1:
            raise RuntimeError(
                "Windows SendInput was blocked by UIPI, the secure desktop, "
                "or a revoked input session"
            )

    def _configure_signatures(self) -> None:
        self._user32.EnumDisplayMonitors.argtypes = (
            wintypes.HDC,
            ctypes.POINTER(RECT),
            self._callback_type,
            wintypes.LPARAM,
        )
        self._user32.EnumDisplayMonitors.restype = wintypes.BOOL
        self._user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.GetMonitorInfoW.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(MONITORINFOEXW),
        )
        self._user32.GetMonitorInfoW.restype = wintypes.BOOL
        self._user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        )
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetDC.argtypes = (wintypes.HWND,)
        self._user32.GetDC.restype = wintypes.HDC
        self._user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        self._user32.ReleaseDC.restype = ctypes.c_int
        self._user32.IsWindow.argtypes = (wintypes.HWND,)
        self._user32.IsWindow.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
        self._user32.GetWindowRect.restype = wintypes.BOOL
        self._user32.MoveWindow.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.BOOL,
        )
        self._user32.MoveWindow.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.PostMessageW.restype = wintypes.BOOL
        self._user32.OpenClipboard.argtypes = (wintypes.HWND,)
        self._user32.OpenClipboard.restype = wintypes.BOOL
        self._user32.CloseClipboard.restype = wintypes.BOOL
        self._user32.EmptyClipboard.restype = wintypes.BOOL
        self._user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
        self._user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        self._user32.GetClipboardData.argtypes = (wintypes.UINT,)
        self._user32.GetClipboardData.restype = wintypes.HANDLE
        self._user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
        self._user32.SetClipboardData.restype = wintypes.HANDLE
        self._gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.CreateCompatibleBitmap.argtypes = (
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
        )
        self._gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
        self._gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
        self._gdi32.SelectObject.restype = wintypes.HANDLE
        self._gdi32.BitBlt.argtypes = (
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        )
        self._gdi32.BitBlt.restype = wintypes.BOOL
        self._gdi32.GetDIBits.argtypes = (
            wintypes.HDC,
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPVOID,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
        )
        self._gdi32.GetDIBits.restype = ctypes.c_int
        self._gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
        self._gdi32.DeleteObject.restype = wintypes.BOOL
        self._gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        self._gdi32.DeleteDC.restype = wintypes.BOOL
        self._shcore.GetDpiForMonitor.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        )
        self._shcore.GetDpiForMonitor.restype = ctypes.c_long
        self._kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        self._kernel32.GlobalAlloc.restype = wintypes.HANDLE
        self._kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
        self._kernel32.GlobalLock.restype = wintypes.LPVOID
        self._kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._kernel32.GlobalSize.argtypes = (wintypes.HANDLE,)
        self._kernel32.GlobalSize.restype = ctypes.c_size_t
        self._kernel32.GlobalFree.argtypes = (wintypes.HANDLE,)
        self._kernel32.GlobalFree.restype = wintypes.HANDLE
        self._shell32.ShellExecuteW.argtypes = (
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        )
        self._shell32.ShellExecuteW.restype = wintypes.HANDLE


class _ignore_os_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, _type: object, _value: object, _traceback: object) -> bool:
        return True


def _layout_displays(
    raw_displays: tuple[tuple[int, Rectangle, float, bool], ...],
) -> tuple[Display, ...]:
    if not raw_displays:
        return ()
    primary = next((item for item in raw_displays if item[3]), raw_displays[0])
    placed: dict[int, Rectangle] = {
        primary[0]: Rectangle(
            left=primary[1].left,
            top=primary[1].top,
            width=max(1, round(primary[1].width / primary[2])),
            height=max(1, round(primary[1].height / primary[2])),
        )
    }
    unresolved = {item[0]: item for item in raw_displays if item[0] != primary[0]}
    while unresolved:
        progress = False
        for handle, item in tuple(unresolved.items()):
            _, physical, scale, _is_primary = item
            logical_width = max(1, round(physical.width / scale))
            logical_height = max(1, round(physical.height / scale))
            for placed_handle, placed_logical in placed.items():
                placed_physical = next(
                    candidate[1]
                    for candidate in raw_displays
                    if candidate[0] == placed_handle
                )
                left: int | None = None
                top: int | None = None
                if physical.left + physical.width == placed_physical.left:
                    left = placed_logical.left - logical_width
                elif physical.left == placed_physical.left + placed_physical.width:
                    left = placed_logical.left + placed_logical.width
                if physical.top + physical.height == placed_physical.top:
                    top = placed_logical.top - logical_height
                elif physical.top == placed_physical.top + placed_physical.height:
                    top = placed_logical.top + placed_logical.height
                if left is not None:
                    top = _aligned_axis(
                        physical.top,
                        physical.top + physical.height,
                        placed_physical.top,
                        placed_physical.top + placed_physical.height,
                        placed_logical.top,
                        placed_logical.top + placed_logical.height,
                        logical_height,
                        scale,
                    )
                elif top is not None:
                    left = _aligned_axis(
                        physical.left,
                        physical.left + physical.width,
                        placed_physical.left,
                        placed_physical.left + placed_physical.width,
                        placed_logical.left,
                        placed_logical.left + placed_logical.width,
                        logical_width,
                        scale,
                    )
                if left is not None and top is not None:
                    placed[handle] = Rectangle(
                        left=left,
                        top=top,
                        width=logical_width,
                        height=logical_height,
                    )
                    del unresolved[handle]
                    progress = True
                    break
        if progress:
            continue
        handle, (_, physical, scale, _is_primary) = unresolved.popitem()
        placed[handle] = Rectangle(
            left=round(physical.left / primary[2]),
            top=round(physical.top / primary[2]),
            width=max(1, round(physical.width / scale)),
            height=max(1, round(physical.height / scale)),
        )
    return tuple(
        Display(
            display_id=str(handle),
            logical_bounds=placed[handle],
            physical_bounds=physical,
            scale=scale,
            primary=is_primary,
        )
        for handle, physical, scale, is_primary in raw_displays
    )


def _aligned_axis(
    physical_start: int,
    physical_end: int,
    neighbor_physical_start: int,
    neighbor_physical_end: int,
    neighbor_logical_start: int,
    neighbor_logical_end: int,
    logical_size: int,
    scale: float,
) -> int:
    if physical_start == neighbor_physical_start:
        return neighbor_logical_start
    if physical_end == neighbor_physical_end:
        return neighbor_logical_end - logical_size
    return neighbor_logical_start + round(
        (physical_start - neighbor_physical_start) / scale
    )


def _windows_error() -> OSError:
    windows_error: Any = ctypes.__dict__["WinError"]
    get_last_error: Any = ctypes.__dict__["get_last_error"]
    error = windows_error(get_last_error())
    return error if isinstance(error, OSError) else OSError(str(error))


def _windows_virtual_key(key: str) -> int:
    normalized = key.casefold()
    named = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "shift": 0x10,
        "control": 0x11,
        "ctrl": 0x11,
        "alt": 0x12,
        "escape": 0x1B,
        "space": 0x20,
        "pageup": 0x21,
        "pagedown": 0x22,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "insert": 0x2D,
        "delete": 0x2E,
        "win": 0x5B,
        "command": 0x5B,
    }
    if normalized in named:
        return named[normalized]
    if normalized.startswith("f") and normalized[1:].isdigit():
        function = int(normalized[1:])
        if 1 <= function <= 24:
            return 0x6F + function
    if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
        return ord(normalized.upper())
    raise ValueError(f"unsupported Windows key: {key}")
