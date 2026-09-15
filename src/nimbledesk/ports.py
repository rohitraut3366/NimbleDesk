from __future__ import annotations

from typing import Protocol

from nimbledesk.protocol.models import ActionRequest, ActionResult, Capability, DesktopObservation


class DesktopBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[Capability]: ...

    def observe(self) -> DesktopObservation: ...

    def execute(self, request: ActionRequest) -> ActionResult: ...

    def cancel_input(self) -> None: ...


class RuntimeClient(Protocol):
    def observe(self, session_id: str) -> DesktopObservation: ...

    def execute(self, request: ActionRequest) -> ActionResult: ...
