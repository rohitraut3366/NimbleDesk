from __future__ import annotations

import base64
import hashlib
import io
from time import time

from PIL import Image

from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CoordinateTarget,
    DesktopObservation,
    Display,
    PermissionState,
    Point,
    Rectangle,
    ScreenCapture,
    Window,
)


class SimulatorBackend:
    """Deterministic backend used to test the complete runtime without desktop input."""

    def __init__(self) -> None:
        self._sequence = 0
        self._cursor = Point(x=100, y=100)
        self.executed_actions: list[ActionRequest] = []
        self.input_cancelled = False

    @property
    def backend_id(self) -> str:
        return "simulator"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.SCREEN_CAPTURE,
                Capability.POINTER,
                Capability.KEYBOARD,
                Capability.WINDOWS,
            }
        )

    def observe(self) -> DesktopObservation:
        captured_at = time()
        self._sequence += 1
        display_bounds = Rectangle(left=0, top=0, width=1920, height=1080)
        return DesktopObservation(
            sequence=self._sequence,
            captured_at=captured_at,
            expires_at=captured_at + 5,
            platform="simulator",
            capabilities=self.capabilities,
            permissions={capability: PermissionState.GRANTED for capability in self.capabilities},
            displays=(
                Display(
                    display_id="primary",
                    logical_bounds=display_bounds,
                    physical_bounds=display_bounds,
                    primary=True,
                ),
            ),
            cursor=self._cursor,
            windows=(
                Window(
                    window_id="fixture-window",
                    application_id="fixture.app",
                    application_name="Fixture",
                    title="NimbleDesk Fixture",
                    bounds=Rectangle(left=100, top=100, width=800, height=600),
                    focused=True,
                ),
            ),
            active_application_id="fixture.app",
            focused_window_id="fixture-window",
        )

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        if isinstance(request.target, CoordinateTarget):
            desktop = Rectangle(left=0, top=0, width=1920, height=1080)
            if not desktop.contains(request.target.point):
                return self._result(
                    request,
                    ActionStatus.FAILED,
                    "Target is outside the desktop",
                    started_at,
                )
            if request.kind in {ActionKind.MOVE_POINTER, ActionKind.CLICK, ActionKind.DRAG}:
                self._cursor = request.target.point
        self.executed_actions.append(request)
        return self._result(
            request,
            ActionStatus.COMPLETED,
            "Action executed by simulator",
            started_at,
        )

    def capture(self, observation_id: str, region: Rectangle | None = None) -> ScreenCapture:
        bounds = region or Rectangle(left=0, top=0, width=1920, height=1080)
        image = Image.new("RGB", (bounds.width, bounds.height), color=(32, 36, 43))
        output = io.BytesIO()
        image.save(output, format="PNG")
        image_bytes = output.getvalue()
        return ScreenCapture(
            observation_id=observation_id,
            width=bounds.width,
            height=bounds.height,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            data_base64=base64.b64encode(image_bytes).decode("ascii"),
        )

    def cancel_input(self) -> None:
        self.input_cancelled = True

    @staticmethod
    def _result(
        request: ActionRequest,
        status: ActionStatus,
        message: str,
        started_at: float,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            data={"backend": "simulator"},
        )
