from __future__ import annotations

from time import time

from nimbledesk.backends.semantic import SemanticProvider
from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    DesktopObservation,
    ElementTarget,
    PermissionState,
    Rectangle,
    ScreenCapture,
)


class NativeDesktopBackend:
    """Composes native semantic accessibility with portable capture and input."""

    def __init__(self, portable: DesktopBackend, semantic: SemanticProvider) -> None:
        self._portable = portable
        self._semantic = semantic
        self._observation_id: str | None = None

    @property
    def backend_id(self) -> str:
        return f"native:{self._semantic.provider_id}+{self._portable.backend_id}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        capabilities = set(self._portable.capabilities)
        if self._semantic.available:
            capabilities.update({Capability.WINDOWS, Capability.ACCESSIBILITY})
        return frozenset(capabilities)

    def observe(self) -> DesktopObservation:
        portable = self._portable.observe()
        semantic = self._semantic.observe()
        permissions = dict(portable.permissions)
        permissions[Capability.ACCESSIBILITY] = semantic.permission
        permissions[Capability.WINDOWS] = (
            PermissionState.GRANTED
            if semantic.windows
            else semantic.permission
        )
        observation = portable.model_copy(
            update={
                "capabilities": self.capabilities,
                "permissions": permissions,
                "windows": semantic.windows,
                "elements": semantic.elements,
                "active_application_id": semantic.active_application_id,
                "focused_window_id": semantic.focused_window_id,
                "warnings": (*portable.warnings, *semantic.warnings),
            }
        )
        self._observation_id = observation.observation_id
        return observation

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        return self._portable.capture(observation_id, region, options)

    def execute(self, request: ActionRequest) -> ActionResult:
        if not isinstance(request.target, ElementTarget):
            return self._portable.execute(request)
        started_at = time()
        if request.target.observation_id != self._observation_id:
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.STALE_OBSERVATION,
                message="Semantic element observation is stale",
                started_at=started_at,
                finished_at=time(),
                data={"backend": self.backend_id},
            )
        try:
            self._semantic.invoke(request.target.element_id)
        except (RuntimeError, ValueError) as error:
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                message=str(error),
                started_at=started_at,
                finished_at=time(),
                data={"backend": self.backend_id},
            )
        return ActionResult(
            action_id=request.action_id,
            status=ActionStatus.COMPLETED,
            message="Semantic element invoked",
            started_at=started_at,
            finished_at=time(),
            data={"backend": self.backend_id},
        )

    def cancel_input(self) -> None:
        self._portable.cancel_input()
