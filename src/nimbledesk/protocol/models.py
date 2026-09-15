from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION: Final[Literal["1.0.0"]] = "1.0.0"


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Capability(StrEnum):
    SCREEN_CAPTURE = "screen_capture"
    POINTER = "pointer"
    KEYBOARD = "keyboard"
    WINDOWS = "windows"
    ACCESSIBILITY = "accessibility"
    CLIPBOARD = "clipboard"
    APPLICATION_ADAPTERS = "application_adapters"
    MEDIA_ANALYSIS = "media_analysis"
    CREATIVE_PLANNING = "creative_planning"


class PermissionState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_DETERMINED = "not_determined"
    UNAVAILABLE = "unavailable"


class SessionState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


class ActionKind(StrEnum):
    MOVE_POINTER = "move_pointer"
    CLICK = "click"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    HOTKEY = "hotkey"
    FOCUS_WINDOW = "focus_window"
    WAIT = "wait"
    APP_COMMAND = "app_command"


class ActionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CONFIRMATION_REQUIRED = "confirmation_required"
    STALE_OBSERVATION = "stale_observation"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


class RiskLevel(StrEnum):
    OBSERVE = "observe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class Point(ProtocolModel):
    x: int
    y: int


class Rectangle(ProtocolModel):
    left: int
    top: int
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]

    def contains(self, point: Point) -> bool:
        return (
            self.left <= point.x < self.left + self.width
            and self.top <= point.y < self.top + self.height
        )


class Display(ProtocolModel):
    display_id: str
    logical_bounds: Rectangle
    physical_bounds: Rectangle
    scale: Annotated[float, Field(gt=0)] = 1.0
    rotation_degrees: Literal[0, 90, 180, 270] = 0
    primary: bool = False


class Window(ProtocolModel):
    window_id: str
    application_id: str
    application_name: str
    title: str
    bounds: Rectangle
    focused: bool = False


class CoordinateTarget(ProtocolModel):
    target_type: Literal["coordinate"] = "coordinate"
    point: Point
    display_id: str | None = None


class ElementTarget(ProtocolModel):
    target_type: Literal["element"] = "element"
    observation_id: str
    element_id: str


class SelectorTarget(ProtocolModel):
    target_type: Literal["selector"] = "selector"
    role: str | None = None
    name: str | None = None
    application_id: str | None = None
    window_id: str | None = None

    @model_validator(mode="after")
    def has_selector(self) -> SelectorTarget:
        if not any((self.role, self.name, self.application_id, self.window_id)):
            raise ValueError("selector target requires at least one selector")
        return self


class VisualTarget(ProtocolModel):
    target_type: Literal["visual"] = "visual"
    observation_id: str
    bounds: Rectangle
    signature: str
    confidence: Annotated[float, Field(ge=0, le=1)]


Target = Annotated[
    CoordinateTarget | ElementTarget | SelectorTarget | VisualTarget,
    Field(discriminator="target_type"),
]


class DesktopObservation(ProtocolModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: Annotated[int, Field(ge=0)]
    captured_at: float
    expires_at: float
    platform: str
    capabilities: frozenset[Capability]
    permissions: dict[Capability, PermissionState]
    displays: tuple[Display, ...]
    cursor: Point
    windows: tuple[Window, ...] = ()
    active_application_id: str | None = None
    focused_window_id: str | None = None
    screenshot_sha256: str | None = None
    warnings: tuple[str, ...] = ()


class ScreenCapture(ProtocolModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    observation_id: str
    mime_type: Literal["image/png"] = "image/png"
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    sha256: str = Field(min_length=64, max_length=64)
    data_base64: str


class ActionRequest(ProtocolModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    source_observation_id: str | None = None
    expected_application_id: str | None = None
    expected_window_id: str | None = None
    kind: ActionKind
    target: Target | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    deadline_ms: Annotated[int, Field(ge=1, le=120_000)] = 10_000
    approval_token: str | None = None


class ActionResult(ProtocolModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    action_id: str
    status: ActionStatus
    message: str
    started_at: float
    finished_at: float
    data: dict[str, Any] = Field(default_factory=dict)
    observation_id: str | None = None
    approval_id: str | None = None


class SessionConfig(ProtocolModel):
    input_enabled: bool = False
    allowed_applications: frozenset[str] = frozenset()
    granted_paths: tuple[str, ...] = ()
    max_actions: Annotated[int, Field(ge=1, le=100_000)] = 1_000
    max_duration_seconds: Annotated[int, Field(ge=1, le=86_400)] = 3_600


class Session(ProtocolModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    state: SessionState
    reason: str
    config: SessionConfig
    created_at: float
    expires_at: float
    action_count: Annotated[int, Field(ge=0)] = 0
