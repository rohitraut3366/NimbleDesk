from __future__ import annotations

import sys
from importlib import import_module
from typing import BinaryIO, Protocol, cast


class InputReleaseAutomation(Protocol):
    def keyUp(self, key: str) -> None: ...

    def mouseUp(self, button: str) -> None: ...


MODIFIER_KEYS = (
    "shift",
    "ctrl",
    "alt",
    "command",
    "winleft",
    "winright",
)
MOUSE_BUTTONS = ("left", "middle", "right")


def release_input(automation: InputReleaseAutomation) -> list[str]:
    failures: list[str] = []
    for key in MODIFIER_KEYS:
        try:
            automation.keyUp(key)
        except Exception as error:
            failures.append(f"key:{key}:{error}")
    for button in MOUSE_BUTTONS:
        try:
            automation.mouseUp(button=button)
        except Exception as error:
            failures.append(f"button:{button}:{error}")
    return failures


def monitor_daemon(stream: BinaryIO, automation: InputReleaseAutomation) -> list[str]:
    while stream.read(1):
        pass
    return release_input(automation)


def main() -> None:
    try:
        automation = cast(InputReleaseAutomation, import_module("pyautogui"))
        monitor_daemon(sys.stdin.buffer, automation)
    except Exception:
        # Startup recovery performs the same release after the service manager restarts the daemon.
        return


if __name__ == "__main__":
    main()
