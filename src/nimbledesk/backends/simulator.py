from __future__ import annotations

from time import time

from PIL import Image

from nimbledesk.backends.images import encode_capture
from nimbledesk.protocol.models import (
    AccessibleElement,
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    CoordinateTarget,
    DesktopObservation,
    Display,
    ElementTarget,
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
        self._observation_id: str | None = None
        self.clipboard_text = ""
        self.launched_applications: list[str] = []

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
                Capability.ACCESSIBILITY,
                Capability.CLIPBOARD,
            }
        )

    def observe(self) -> DesktopObservation:
        captured_at = time()
        self._sequence += 1
        display_bounds = Rectangle(left=0, top=0, width=1920, height=1080)
        observation = DesktopObservation(
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
            elements=(
                AccessibleElement(
                    element_id="fixture-title",
                    window_id="fixture-window",
                    role="heading",
                    name="NimbleDesk Fixture",
                    bounds=Rectangle(left=140, top=140, width=300, height=40),
                ),
                AccessibleElement(
                    element_id="fixture-create",
                    window_id="fixture-window",
                    role="button",
                    name="Create",
                    bounds=Rectangle(left=140, top=210, width=120, height=40),
                    actions=("invoke",),
                ),
            ),
            active_application_id="fixture.app",
            focused_window_id="fixture-window",
        )
        self._observation_id = observation.observation_id
        return observation

    def execute(self, request: ActionRequest) -> ActionResult:
        started_at = time()
        if request.kind is ActionKind.READ_CLIPBOARD:
            maximum = int(request.arguments.get("maximum_characters", 10_000))
            text = self.clipboard_text[:maximum]
            self.executed_actions.append(request)
            return self._result(
                request,
                ActionStatus.COMPLETED,
                "Clipboard read by simulator",
                started_at,
                {
                    "text": text,
                    "characters": len(text),
                    "truncated": len(self.clipboard_text) > maximum,
                },
            )
        if request.kind is ActionKind.WRITE_CLIPBOARD:
            self.clipboard_text = str(request.arguments["text"])
            self.executed_actions.append(request)
            return self._result(
                request,
                ActionStatus.COMPLETED,
                "Clipboard written by simulator",
                started_at,
                {"characters": len(self.clipboard_text)},
            )
        if request.kind is ActionKind.LAUNCH_APPLICATION:
            application_id = str(request.arguments["application_id"])
            self.launched_applications.append(application_id)
            self.executed_actions.append(request)
            return self._result(
                request,
                ActionStatus.COMPLETED,
                "Application launched by simulator",
                started_at,
                {"application_id": application_id},
            )
        if isinstance(request.target, ElementTarget):
            if request.target.observation_id != self._observation_id:
                return self._result(
                    request,
                    ActionStatus.STALE_OBSERVATION,
                    "Element observation is stale",
                    started_at,
                )
            if request.target.element_id != "fixture-create":
                return self._result(
                    request, ActionStatus.FAILED, "Element is unknown or not actionable", started_at
                )
            self.executed_actions.append(request)
            return self._result(
                request,
                ActionStatus.COMPLETED,
                "Semantic element invoked by simulator",
                started_at,
            )
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

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        bounds = region or Rectangle(left=0, top=0, width=1920, height=1080)
        image = Image.new("RGB", (bounds.width, bounds.height), color=(32, 36, 43))
        return encode_capture(image, observation_id, options)

    def cancel_input(self) -> None:
        self.input_cancelled = True

    @staticmethod
    def _result(
        request: ActionRequest,
        status: ActionStatus,
        message: str,
        started_at: float,
        data: dict[str, object] | None = None,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            data={"backend": "simulator", **(data or {})},
        )
