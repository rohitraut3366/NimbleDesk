import signal
from typing import Any

from pytest import MonkeyPatch

import nimbledesk.daemon.server as server


def test_daemon_main_restores_signal_handler_after_orderly_shutdown(
    monkeypatch: MonkeyPatch,
) -> None:
    handlers: list[object] = []
    previous = object()
    monkeypatch.setattr(
        server.signal,
        "signal",
        lambda selected_signal, handler: (
            handlers.append((selected_signal, handler)) or previous
        ),
    )

    def discard_coroutine(coroutine: Any) -> None:
        coroutine.close()

    monkeypatch.setattr(server.asyncio, "run", discard_coroutine)

    server.main()

    assert handlers == [
        (signal.SIGTERM, server._request_shutdown),
        (signal.SIGTERM, previous),
    ]


def test_daemon_termination_signal_requests_asyncio_cleanup() -> None:
    try:
        server._request_shutdown(signal.SIGTERM, None)
    except KeyboardInterrupt:
        return
    raise AssertionError("termination did not interrupt the daemon")
