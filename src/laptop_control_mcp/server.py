from __future__ import annotations

from functools import lru_cache
import os
import platform
from typing import Literal

from mcp.server.fastmcp import FastMCP, Image

from laptop_control_mcp.controller import DesktopController, Region


mcp = FastMCP("Laptop Control")


@lru_cache(maxsize=1)
def controller() -> DesktopController:
    import pyautogui

    return DesktopController(pyautogui)


@mcp.tool()
def platform_info() -> dict[str, str | bool]:
    """Return the operating system and desktop-session details useful for diagnostics."""
    return {
        "operating_system": platform.system(),
        "operating_system_version": platform.release(),
        "input_enabled": controller().input_enabled,
        "linux_session_type": os.getenv("XDG_SESSION_TYPE", "") if platform.system() == "Linux" else "",
    }


@mcp.tool()
def screen_size() -> dict[str, int]:
    """Return the usable screen width and height in pixels."""
    width, height = controller().screen_size()
    return {"width": width, "height": height}


@mcp.tool()
def cursor_position() -> dict[str, int]:
    """Return the current mouse cursor position in pixels."""
    x, y = controller().cursor_position()
    return {"x": x, "y": y}


@mcp.tool()
def take_screenshot(
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> Image:
    """Capture the screen, optionally cropped to a pixel rectangle."""
    values = (left, top, width, height)
    if any(value is not None for value in values) and not all(value is not None for value in values):
        raise ValueError("left, top, width, and height must be provided together")
    region = None
    if all(value is not None for value in values):
        region = Region(left=left, top=top, width=width, height=height)  # type: ignore[arg-type]
    return Image(data=controller().screenshot_png(region), format="png")


@mcp.tool()
def move_mouse(x: int, y: int, duration: float = 0.2) -> str:
    """Move the cursor to an absolute screen coordinate."""
    controller().move_mouse(x, y, duration)
    return f"Moved cursor to ({x}, {y})"


@mcp.tool()
def click(
    x: int,
    y: int,
    button: Literal["left", "middle", "right"] = "left",
    clicks: int = 1,
    interval: float = 0.1,
) -> str:
    """Click at an absolute screen coordinate."""
    controller().click(x, y, button, clicks, interval)
    return f"Clicked {button} at ({x}, {y}) {clicks} time(s)"


@mcp.tool()
def scroll(amount: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll vertically. Positive moves up; negative moves down."""
    controller().scroll(amount, x, y)
    return f"Scrolled by {amount}"


@mcp.tool()
def type_text(text: str, interval: float = 0.02) -> str:
    """Type text into the currently focused control."""
    controller().type_text(text, interval)
    return f"Typed {len(text)} character(s)"


@mcp.tool()
def press_key(key: str, presses: int = 1, interval: float = 0.1) -> str:
    """Press a named key such as enter, tab, escape, backspace, or f5."""
    controller().press_key(key, presses, interval)
    return f"Pressed {key} {presses} time(s)"


@mcp.tool()
def hotkey(keys: list[str], interval: float = 0.05) -> str:
    """Press a key combination, for example ['command', 'l']."""
    controller().hotkey(keys, interval)
    return f"Pressed {' + '.join(keys)}"


@mcp.tool()
def wait(seconds: float) -> str:
    """Wait briefly for an application or animation to settle."""
    controller().wait(seconds)
    return f"Waited {seconds} second(s)"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
