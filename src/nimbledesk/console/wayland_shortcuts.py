from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from contextlib import suppress
from importlib import import_module
from typing import Any, TypeVar
from uuid import uuid4

T = TypeVar("T")
SHORTCUT_ID = "pause-all-sessions"


class WaylandGlobalShortcut:
    """A compositor-owned shortcut registered through the XDG desktop portal."""

    def __init__(self, hotkey: str, activated: Callable[[], None]) -> None:
        self._hotkey = hotkey
        self._activated = activated
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="nimbledesk-global-shortcut",
        )
        self._thread.start()
        self._bus: Any | None = None
        self._portal: Any | None = None
        self._session: str | None = None
        self._listening = False

    def start(self) -> None:
        if self._session is not None:
            return
        try:
            self._call(self._start())
        except RuntimeError:
            self.stop()
            raise

    def stop(self) -> None:
        if self._session is not None and self._bus is not None:
            with suppress(RuntimeError):
                self._call(self._close_session())
        if self._bus is not None and self._listening:
            self._bus.remove_message_handler(self._received)
            self._listening = False
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
        self._session = None

    async def _start(self) -> None:
        try:
            MessageBus = import_module("dbus_next.aio").MessageBus
            Variant = import_module("dbus_next.signature").Variant
        except ImportError as error:
            raise RuntimeError("Wayland global shortcuts require dbus-next") from error
        self._bus = await MessageBus().connect()
        destination = "org.freedesktop.portal.Desktop"
        desktop_path = "/org/freedesktop/portal/desktop"
        introspection = await self._bus.introspect(destination, desktop_path)
        desktop = self._bus.get_proxy_object(destination, desktop_path, introspection)
        self._portal = desktop.get_interface("org.freedesktop.portal.GlobalShortcuts")
        self._bus.add_message_handler(self._received)
        self._listening = True

        session_token = f"nimbledesk_shortcuts_{uuid4().hex}"
        request_token = f"nimbledesk_{uuid4().hex}"
        created = await self._request(
            self._portal.call_create_session(
                {
                    "handle_token": Variant("s", request_token),
                    "session_handle_token": Variant("s", session_token),
                }
            ),
            request_token,
        )
        self._session = str(_value(created["session_handle"]))

        request_token = f"nimbledesk_{uuid4().hex}"
        await self._request(
            self._portal.call_bind_shortcuts(
                self._session,
                [
                    (
                        SHORTCUT_ID,
                        {
                            "description": Variant("s", "Pause all NimbleDesk sessions"),
                            "preferred_trigger": Variant(
                                "s", _portal_trigger(self._hotkey)
                            ),
                        },
                    )
                ],
                "",
                {"handle_token": Variant("s", request_token)},
            ),
            request_token,
        )

    def _received(self, message: Any) -> None:
        if (
            message.interface == "org.freedesktop.portal.GlobalShortcuts"
            and message.member == "Activated"
            and len(message.body) >= 2
            and str(message.body[0]) == self._session
            and str(message.body[1]) == SHORTCUT_ID
        ):
            self._activated()

    async def _request(
        self, request_call: Coroutine[Any, Any, str], request_token: str
    ) -> dict[str, Any]:
        assert self._bus is not None
        response: asyncio.Future[tuple[int, dict[str, Any]]] = self._loop.create_future()
        sender = str(self._bus.unique_name).removeprefix(":").replace(".", "_")
        expected_path = f"/org/freedesktop/portal/desktop/request/{sender}/{request_token}"

        def received(message: Any) -> None:
            if (
                message.path == expected_path
                and message.interface == "org.freedesktop.portal.Request"
                and message.member == "Response"
                and not response.done()
            ):
                code, results = message.body
                response.set_result((int(code), dict(results)))

        self._bus.add_message_handler(received)
        try:
            returned_path = await request_call
            if returned_path != expected_path:
                raise RuntimeError("global-shortcut portal returned an unexpected handle")
            code, results = await asyncio.wait_for(response, timeout=120)
        finally:
            self._bus.remove_message_handler(received)
        if code == 1:
            raise RuntimeError("Wayland global-shortcut registration was cancelled")
        if code != 0:
            raise RuntimeError(f"Wayland global-shortcut portal denied the request ({code})")
        return results

    async def _close_session(self) -> None:
        assert self._bus is not None and self._session is not None
        destination = "org.freedesktop.portal.Desktop"
        introspection = await self._bus.introspect(destination, self._session)
        session = self._bus.get_proxy_object(destination, self._session, introspection)
        await session.get_interface("org.freedesktop.portal.Session").call_close()

    def _call(self, coroutine: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=130)
        except Exception as error:
            future.cancel()
            raise RuntimeError(str(error) or type(error).__name__) from error


def _portal_trigger(hotkey: str) -> str:
    names = {
        "ctrl": "CTRL",
        "control": "CTRL",
        "alt": "ALT",
        "shift": "SHIFT",
        "cmd": "SUPER",
        "command": "SUPER",
        "super": "SUPER",
        "win": "SUPER",
    }
    trigger: list[str] = []
    for token in hotkey.split("+"):
        normalized = token.removeprefix("<").removesuffix(">").casefold()
        trigger.append(names.get(normalized, normalized.upper()))
    return "+".join(trigger)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)
