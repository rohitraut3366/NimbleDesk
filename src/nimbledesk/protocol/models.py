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
    OCR = "ocr"


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
    MOVE_WINDOW = "move_window"
    RESIZE_WINDOW = "resize_window"
    MINIMIZE_WINDOW = "minimize_window"
    MAXIMIZE_WINDOW = "maximize_window"
    CLOSE_WINDOW = "close_window"
    READ_CLIPBOARD = "read_clipboard"
    WRITE_CLIPBOARD = "write_clipboard"
    LAUNCH_APPLICATION = "launch_application"
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


class AccessibleElement(ProtocolModel):
    element_id: str
    window_id: str
    role: str
    name: str
    bounds: Rectangle | None = None
    value: str | None = None
    enabled: bool = True
    focused: bool = False
    parent_id: str | None = None
    actions: tuple[str, ...] = ()


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
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: Annotated[float, Field(ge=0, le=1)]


class TextTarget(ProtocolModel):
    target_type: Literal["text"] = "text"
    observation_id: str
    text: str = Field(min_length=1, max_length=500)
    search_bounds: Rectangle | None = None
    exact: bool = False
    minimum_confidence: Annotated[float, Field(ge=0.5, le=1)] = 0.75


Target = Annotated[
    CoordinateTarget | ElementTarget | SelectorTarget | VisualTarget | TextTarget,
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
    elements: tuple[AccessibleElement, ...] = ()
    active_application_id: str | None = None
    focused_window_id: str | None = None
    screenshot_sha256: str | None = None
    windows_sha256: str | None = None
    ui_tree_sha256: str | None = None
    warnings: tuple[str, ...] = ()


class ImageUsage(ProtocolModel):
    source_width: Annotated[int, Field(gt=0)]
    source_height: Annotated[int, Field(gt=0)]
    output_pixels: Annotated[int, Field(gt=0)]
    encoded_bytes: Annotated[int, Field(gt=0)]
    estimated_512px_tiles: Annotated[int, Field(gt=0)]
    estimated_image_tokens: Annotated[int, Field(gt=0)]


class ScreenCapture(ProtocolModel):
    protocol_version: Literal["1.0.0"] = PROTOCOL_VERSION
    observation_id: str
    mime_type: Literal["image/png", "image/jpeg"]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    sha256: str = Field(min_length=64, max_length=64)
    data_base64: str
    usage: ImageUsage | None = None


class CaptureOptions(ProtocolModel):
    image_format: Literal["png", "jpeg"] = "jpeg"
    max_width: Annotated[int, Field(ge=64, le=4096)] = 1280
    max_height: Annotated[int, Field(ge=64, le=4096)] = 800
    jpeg_quality: Annotated[int, Field(ge=20, le=95)] = 75


class ResponseBudget(ProtocolModel):
    max_estimated_text_tokens: Annotated[int, Field(ge=128, le=100_000)] = 2_000
    max_windows: Annotated[int, Field(ge=0, le=200)] = 10
    max_elements: Annotated[int, Field(ge=0, le=2_000)] = 100


class RecoveryOptions(ProtocolModel):
    max_reobservations: Annotated[int, Field(ge=0, le=3)] = 0


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
    recovery: RecoveryOptions = RecoveryOptions()


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
    clipboard_enabled: bool = False
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
