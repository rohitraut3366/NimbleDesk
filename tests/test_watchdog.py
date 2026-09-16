from io import BytesIO

from nimbledesk.daemon.watchdog import MODIFIER_KEYS, MOUSE_BUTTONS, monitor_daemon


class RecordingAutomation:
    def __init__(self, failing_key: str | None = None) -> None:
        self.failing_key = failing_key
        self.released_keys: list[str] = []
        self.released_buttons: list[str] = []

    def keyUp(self, key: str) -> None:
        if key == self.failing_key:
            raise RuntimeError("fixture failure")
        self.released_keys.append(key)

    def mouseUp(self, button: str) -> None:
        self.released_buttons.append(button)


def test_watchdog_releases_input_after_daemon_pipe_closes() -> None:
    automation = RecordingAutomation()

    failures = monitor_daemon(BytesIO(b"daemon-alive"), automation)

    assert failures == []
    assert automation.released_keys == list(MODIFIER_KEYS)
    assert automation.released_buttons == list(MOUSE_BUTTONS)


def test_watchdog_continues_releasing_after_one_os_error() -> None:
    automation = RecordingAutomation(failing_key="ctrl")

    failures = monitor_daemon(BytesIO(), automation)

    assert failures == ["key:ctrl:fixture failure"]
    assert "shift" in automation.released_keys
    assert automation.released_buttons == list(MOUSE_BUTTONS)
