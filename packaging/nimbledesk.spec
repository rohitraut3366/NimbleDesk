# -*- mode: python ; coding: utf-8 -*-
import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent

windows_hiddenimports = (
    collect_submodules("dxcam")
    + [
        "pywintypes",
        "win32api",
        "win32con",
        "win32event",
        "win32job",
        "win32process",
        "win32security",
    ]
    if platform.system() == "Windows"
    else []
)

macos_hiddenimports = (
    ["AppKit", "ApplicationServices", "Quartz", "ScreenCaptureKit"]
    if platform.system() == "Darwin"
    else []
)

hiddenimports = (
    collect_submodules("dbus_next")
    + collect_submodules("faster_whisper")
    + collect_submodules("mcp")
    + collect_submodules("nimbledesk")
    + collect_submodules("pynput")
    + collect_submodules("pystray")
    + collect_submodules("starlette")
    + collect_submodules("uvicorn")
    + windows_hiddenimports
    + macos_hiddenimports
)

analysis = Analysis(
    [str(root / "src" / "nimbledesk" / "cli.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="nimbledesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
