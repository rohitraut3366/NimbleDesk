from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from typing import Protocol

from PIL import Image as PillowImage


class DesktopBackend(Protocol):
    FAILSAFE: bool
    PAUSE: float

    def size(self) -> tuple[int, int]: ...

    def position(self) -> tuple[int, int]: ...

    def screenshot(self) -> PillowImage.Image: ...

    def moveTo(self, x: int, y: int, duration: float = 0) -> None: ...

    def click(self, x: int | None = None, y: int | None = None, clicks: int = 1,
              interval: float = 0, button: str = "left") -> None: ...

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> None: ...

    def write(self, message: str, interval: float = 0) -> None: ...

    def press(self, keys: str | list[str], presses: int = 1, interval: float = 0) -> None: ...

    def hotkey(self, *args: str, interval: float = 0) -> None: ...


class InputDisabledError(PermissionError):
    pass


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int


class DesktopController:
    VALID_BUTTONS = {"left", "middle", "right"}
    MAX_CLICKS = 10
    MAX_KEY_PRESSES = 20
    MAX_TEXT_LENGTH = 10_000
    MAX_MOVE_DURATION = 5.0
    MAX_KEY_INTERVAL = 2.0

    def __init__(self, backend: DesktopBackend, input_enabled: bool | None = None) -> None:
        self.backend = backend
        self.input_enabled = (
            _read_bool("LAPTOP_CONTROL_ENABLE_INPUT")
            if input_enabled is None
            else input_enabled
        )
        self.backend.FAILSAFE = True
        self.backend.PAUSE = 0.1

    def screen_size(self) -> tuple[int, int]:
        size = self.backend.size()
        return int(size[0]), int(size[1])

    def cursor_position(self) -> tuple[int, int]:
        position = self.backend.position()
        return int(position[0]), int(position[1])

    def screenshot_png(self, region: Region | None = None) -> bytes:
        image = self.backend.screenshot()
        if region is not None:
            self._validate_region(region)
            image = image.crop(
                (
                    region.left,
                    region.top,
                    region.left + region.width,
                    region.top + region.height,
                )
            )
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def move_mouse(self, x: int, y: int, duration: float = 0.2) -> None:
        self._require_input()
        self._validate_point(x, y)
        duration = _number_in_range("duration", duration, 0, self.MAX_MOVE_DURATION)
        self.backend.moveTo(x, y, duration=duration)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> None:
        self._require_input()
        self._validate_point(x, y)
        if button not in self.VALID_BUTTONS:
            raise ValueError(f"button must be one of: {', '.join(sorted(self.VALID_BUTTONS))}")
        clicks = int(_number_in_range("clicks", clicks, 1, self.MAX_CLICKS))
        interval = _number_in_range("interval", interval, 0, self.MAX_KEY_INTERVAL)
        self.backend.click(x=x, y=y, clicks=clicks, interval=interval, button=button)

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        self._require_input()
        if amount == 0 or abs(amount) > 100:
            raise ValueError("amount must be between -100 and 100, excluding 0")
        if (x is None) != (y is None):
            raise ValueError("x and y must be provided together")
        if x is not None and y is not None:
            self._validate_point(x, y)
        self.backend.scroll(amount, x=x, y=y)

    def type_text(self, text: str, interval: float = 0.02) -> None:
        self._require_input()
        if not text:
            raise ValueError("text cannot be empty")
        if len(text) > self.MAX_TEXT_LENGTH:
            raise ValueError(f"text cannot exceed {self.MAX_TEXT_LENGTH} characters")
        interval = _number_in_range("interval", interval, 0, self.MAX_KEY_INTERVAL)
        self.backend.write(text, interval=interval)

    def press_key(self, key: str, presses: int = 1, interval: float = 0.1) -> None:
        self._require_input()
        if not key or len(key) > 30:
            raise ValueError("key must contain between 1 and 30 characters")
        presses = int(_number_in_range("presses", presses, 1, self.MAX_KEY_PRESSES))
        interval = _number_in_range("interval", interval, 0, self.MAX_KEY_INTERVAL)
        self.backend.press(key, presses=presses, interval=interval)

    def hotkey(self, keys: list[str], interval: float = 0.05) -> None:
        self._require_input()
        if not 2 <= len(keys) <= 5:
            raise ValueError("hotkey requires between 2 and 5 keys")
        if any(not key or len(key) > 30 for key in keys):
            raise ValueError("each key must contain between 1 and 30 characters")
        interval = _number_in_range("interval", interval, 0, self.MAX_KEY_INTERVAL)
        self.backend.hotkey(*keys, interval=interval)

    def wait(self, seconds: float) -> None:
        seconds = _number_in_range("seconds", seconds, 0, 10)
        time.sleep(seconds)

    def _require_input(self) -> None:
        if not self.input_enabled:
            raise InputDisabledError(
                "Input is disabled. Restart the server with "
                "LAPTOP_CONTROL_ENABLE_INPUT=1 after reviewing the requested actions."
            )

    def _validate_point(self, x: int, y: int) -> None:
        width, height = self.screen_size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"point ({x}, {y}) is outside the {width}x{height} screen")

    def _validate_region(self, region: Region) -> None:
        if region.width <= 0 or region.height <= 0:
            raise ValueError("region width and height must be positive")
        self._validate_point(region.left, region.top)
        width, height = self.screen_size()
        if region.left + region.width > width or region.top + region.height > height:
            raise ValueError("region extends beyond the screen")


def _read_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _number_in_range(name: str, value: float, minimum: float, maximum: float) -> float:
    numeric_value = float(value)
    if not minimum <= numeric_value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return numeric_value

