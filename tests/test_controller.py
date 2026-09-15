from __future__ import annotations

from PIL import Image
import pytest

from laptop_control_mcp.controller import DesktopController, InputDisabledError, Region


class FakeBackend:
    FAILSAFE = False
    PAUSE = 0.0

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def size(self) -> tuple[int, int]:
        return 1440, 900

    def position(self) -> tuple[int, int]:
        return 10, 20

    def screenshot(self) -> Image.Image:
        return Image.new("RGB", self.size(), "white")

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def test_input_starts_disabled() -> None:
    controller = DesktopController(FakeBackend(), input_enabled=False)

    with pytest.raises(InputDisabledError):
        controller.click(10, 20)


def test_click_validates_coordinates_before_calling_backend() -> None:
    backend = FakeBackend()
    controller = DesktopController(backend, input_enabled=True)

    with pytest.raises(ValueError, match="outside"):
        controller.click(1440, 20)

    assert backend.calls == []


def test_crop_returns_requested_dimensions() -> None:
    controller = DesktopController(FakeBackend(), input_enabled=False)

    image_bytes = controller.screenshot_png(Region(100, 200, 300, 250))

    from io import BytesIO

    with Image.open(BytesIO(image_bytes)) as image:
        assert image.size == (300, 250)


def test_hotkey_limits_number_of_keys() -> None:
    controller = DesktopController(FakeBackend(), input_enabled=True)

    with pytest.raises(ValueError, match="between 2 and 5"):
        controller.hotkey(["command"])

