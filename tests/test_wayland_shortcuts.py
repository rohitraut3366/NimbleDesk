from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import nimbledesk.console.wayland_shortcuts as shortcuts
from nimbledesk.console.wayland_shortcuts import (
    SHORTCUT_ID,
    WaylandGlobalShortcut,
    _portal_trigger,
)


class Variant:
    def __init__(self, signature: str, value: object) -> None:
        self.signature = signature
        self.value = value


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def call_close(self) -> None:
        self.closed = True


class FakePortal:
    def __init__(self, bus: FakeBus) -> None:
        self.bus = bus
        self.bound: tuple[object, ...] | None = None

    async def call_create_session(self, options: dict[str, Variant]) -> str:
        token = str(options["handle_token"].value)
        path = self.bus.request_path(token)
        self.bus.emit_response(
            path,
            {"session_handle": Variant("o", "/org/freedesktop/portal/session/test")},
        )
        return path

    async def call_bind_shortcuts(self, *arguments: object) -> str:
        self.bound = arguments
        options = arguments[-1]
        assert isinstance(options, dict)
        token = str(options["handle_token"].value)
        path = self.bus.request_path(token)
        self.bus.emit_response(path, {})
        return path


class FakeProxy:
    def __init__(self, portal: FakePortal, session: FakeSession) -> None:
        self.portal = portal
        self.session = session

    def get_interface(self, name: str) -> object:
        if name == "org.freedesktop.portal.GlobalShortcuts":
            return self.portal
        if name == "org.freedesktop.portal.Session":
            return self.session
        raise AssertionError(name)


class FakeBus:
    unique_name = ":1.55"

    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.session = FakeSession()
        self.portal = FakePortal(self)
        self.proxy = FakeProxy(self.portal, self.session)

    async def connect(self) -> FakeBus:
        return self

    async def introspect(self, _destination: str, path: str) -> str:
        return path

    def get_proxy_object(
        self, _destination: str, _path: str, _introspection: str
    ) -> FakeProxy:
        return self.proxy

    def add_message_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def remove_message_handler(self, handler: Any) -> None:
        self.handlers.remove(handler)

    def request_path(self, token: str) -> str:
        return f"/org/freedesktop/portal/desktop/request/1_55/{token}"

    def emit_response(self, path: str, results: dict[str, object]) -> None:
        self.emit(
            SimpleNamespace(
                path=path,
                interface="org.freedesktop.portal.Request",
                member="Response",
                body=[0, results],
            )
        )

    def emit(self, message: object) -> None:
        for handler in tuple(self.handlers):
            handler(message)


def test_wayland_global_shortcut_registers_and_dispatches_activation(
    monkeypatch: Any,
) -> None:
    bus = FakeBus()

    def imported(name: str) -> object:
        if name == "dbus_next.aio":
            return SimpleNamespace(MessageBus=lambda: bus)
        if name == "dbus_next.signature":
            return SimpleNamespace(Variant=Variant)
        raise AssertionError(name)

    monkeypatch.setattr(shortcuts, "import_module", imported)
    activations: list[str] = []
    listener = WaylandGlobalShortcut(
        "<ctrl>+<alt>+<shift>+p", lambda: activations.append("pause")
    )

    listener.start()
    bus.emit(
        SimpleNamespace(
            path="/org/freedesktop/portal/desktop",
            interface="org.freedesktop.portal.GlobalShortcuts",
            member="Activated",
            body=["/org/freedesktop/portal/session/test", SHORTCUT_ID, 1, {}],
        )
    )
    listener.stop()

    assert activations == ["pause"]
    assert bus.session.closed is True
    assert bus.portal.bound is not None
    session, registrations, parent_window, _options = bus.portal.bound
    assert session == "/org/freedesktop/portal/session/test"
    assert parent_window == ""
    shortcut_id, shortcut_options = registrations[0]
    assert shortcut_id == SHORTCUT_ID
    assert shortcut_options["preferred_trigger"].value == "CTRL+ALT+SHIFT+P"


def test_portal_trigger_maps_cross_platform_modifier_names() -> None:
    assert _portal_trigger("<control>+<command>+x") == "CTRL+SUPER+X"
