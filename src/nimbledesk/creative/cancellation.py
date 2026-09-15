from __future__ import annotations

import threading

from nimbledesk.media.process import ProcessCancelled


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled():
            raise ProcessCancelled("creation was cancelled")
