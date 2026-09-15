from __future__ import annotations

from PIL import Image


class FakeAutomation:
    FAILSAFE = False
    PAUSE = 0.0

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def size(self) -> tuple[int, int]:
        return 1440, 900

    def position(self) -> tuple[int, int]:
        return 10, 20

    def screenshot(self) -> Image.Image:
        return Image.new("RGB", self.size(), "white")

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))

        return record
