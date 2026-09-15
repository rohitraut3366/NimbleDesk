from __future__ import annotations

import importlib
import importlib.util
import platform
from dataclasses import dataclass
from typing import Any, Protocol

from nimbledesk.protocol.models import AccessibleElement, PermissionState, Rectangle, Window


@dataclass(frozen=True)
class SemanticSnapshot:
    permission: PermissionState
    windows: tuple[Window, ...] = ()
    elements: tuple[AccessibleElement, ...] = ()
    active_application_id: str | None = None
    focused_window_id: str | None = None
    warnings: tuple[str, ...] = ()


class SemanticProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def available(self) -> bool: ...

    def observe(self) -> SemanticSnapshot: ...

    def invoke(self, element_id: str) -> None: ...


def system_semantic_provider() -> SemanticProvider:
    system = platform.system()
    if system == "Darwin":
        return MacOSAccessibilityProvider()
    if system == "Windows":
        return WindowsUIAutomationProvider()
    if system == "Linux":
        return LinuxATSPIProvider()
    return UnavailableSemanticProvider(f"unsupported operating system: {system}")


class UnavailableSemanticProvider:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def provider_id(self) -> str:
        return "unavailable"

    @property
    def available(self) -> bool:
        return False

    def observe(self) -> SemanticSnapshot:
        return SemanticSnapshot(
            permission=PermissionState.UNAVAILABLE,
            warnings=(self._reason,),
        )

    def invoke(self, element_id: str) -> None:
        raise RuntimeError(f"semantic accessibility is unavailable: {self._reason}")


class MacOSAccessibilityProvider:
    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self._elements: dict[str, Any] = {}

    @property
    def provider_id(self) -> str:
        return "macos-ax"

    @property
    def available(self) -> bool:
        return (
            self._module is not None
            or importlib.util.find_spec("ApplicationServices") is not None
        )

    def observe(self) -> SemanticSnapshot:
        api = self._load()
        if api is None:
            return SemanticSnapshot(
                permission=PermissionState.UNAVAILABLE,
                warnings=("Install pyobjc-framework-ApplicationServices for native macOS AX",),
            )
        if not bool(api.AXIsProcessTrusted()):
            return SemanticSnapshot(
                permission=PermissionState.DENIED,
                warnings=("Grant Accessibility permission to the NimbleDesk daemon",),
            )
        system = api.AXUIElementCreateSystemWide()
        application = self._copy(api, system, api.kAXFocusedApplicationAttribute)
        if application is None:
            return SemanticSnapshot(
                permission=PermissionState.GRANTED,
                warnings=("macOS AX did not return a focused application",),
            )
        application_name = str(
            self._copy(api, application, api.kAXTitleAttribute) or "Focused application"
        )
        application_id = f"pid:{self._pid(api, application)}"
        window_ref = self._copy(api, application, api.kAXFocusedWindowAttribute)
        window_id = f"{application_id}:window"
        window_title = str(
            self._copy(api, window_ref, api.kAXTitleAttribute) or application_name
        )
        window_bounds = self._bounds(api, window_ref) or Rectangle(
            left=0, top=0, width=1, height=1
        )
        windows = (
            Window(
                window_id=window_id,
                application_id=application_id,
                application_name=application_name,
                title=window_title,
                bounds=window_bounds,
                focused=True,
            ),
        )
        self._elements.clear()
        elements = self._walk(api, window_ref or application, window_id)
        return SemanticSnapshot(
            permission=PermissionState.GRANTED,
            windows=windows,
            elements=elements,
            active_application_id=application_id,
            focused_window_id=window_id,
        )

    def invoke(self, element_id: str) -> None:
        api = self._load()
        element = self._elements.get(element_id)
        if api is None or element is None:
            raise ValueError("accessibility element is unknown or expired")
        actions = self._actions(api, element)
        action = next(
            (
                candidate
                for candidate in ("AXPress", "AXConfirm", "AXShowMenu")
                if candidate in actions
            ),
            None,
        )
        if action is None:
            raise ValueError("accessibility element has no invokable action")
        result = api.AXUIElementPerformAction(element, action)
        if result not in (None, 0):
            raise RuntimeError(f"macOS AX action failed with error {result}")

    def _load(self) -> Any | None:
        if self._module is None and self.available:
            self._module = importlib.import_module("ApplicationServices")
        return self._module

    def _walk(self, api: Any, root: Any, window_id: str) -> tuple[AccessibleElement, ...]:
        pending: list[tuple[Any, str | None]] = [(root, None)]
        result: list[AccessibleElement] = []
        while pending and len(result) < 500:
            element, parent_id = pending.pop(0)
            element_id = f"ax-{len(result)}"
            role = str(self._copy(api, element, api.kAXRoleAttribute) or "unknown")
            name = str(
                self._copy(api, element, api.kAXTitleAttribute)
                or self._copy(api, element, api.kAXDescriptionAttribute)
                or ""
            )
            actions = self._actions(api, element)
            result.append(
                AccessibleElement(
                    element_id=element_id,
                    window_id=window_id,
                    role=role.removeprefix("AX").casefold(),
                    name=name,
                    bounds=self._bounds(api, element),
                    value=self._safe_value(api, element),
                    enabled=bool(self._copy(api, element, api.kAXEnabledAttribute) is not False),
                    focused=bool(self._copy(api, element, api.kAXFocusedAttribute) is True),
                    parent_id=parent_id,
                    actions=tuple(action.removeprefix("AX").casefold() for action in actions),
                )
            )
            self._elements[element_id] = element
            children = self._copy(api, element, api.kAXChildrenAttribute) or []
            pending.extend((child, element_id) for child in children)
        return tuple(result)

    @staticmethod
    def _copy(api: Any, element: Any, attribute: str) -> Any | None:
        if element is None:
            return None
        result = api.AXUIElementCopyAttributeValue(element, attribute, None)
        if isinstance(result, tuple) and len(result) == 2:
            return result[1] if result[0] == 0 else None
        return result

    @staticmethod
    def _actions(api: Any, element: Any) -> tuple[str, ...]:
        result = api.AXUIElementCopyActionNames(element, None)
        if isinstance(result, tuple) and len(result) == 2:
            result = result[1] if result[0] == 0 else ()
        return tuple(str(action) for action in (result or ()))

    def _bounds(self, api: Any, element: Any) -> Rectangle | None:
        position = self._copy(api, element, api.kAXPositionAttribute)
        size = self._copy(api, element, api.kAXSizeAttribute)
        point = self._ax_value(api, position, api.kAXValueCGPointType)
        dimensions = self._ax_value(api, size, api.kAXValueCGSizeType)
        if point is None or dimensions is None:
            return None
        width, height = int(dimensions[0]), int(dimensions[1])
        if width <= 0 or height <= 0:
            return None
        return Rectangle(left=int(point[0]), top=int(point[1]), width=width, height=height)

    @staticmethod
    def _ax_value(api: Any, value: Any, value_type: Any) -> tuple[float, float] | None:
        if value is None:
            return None
        result = api.AXValueGetValue(value, value_type, None)
        if isinstance(result, tuple) and len(result) == 2 and result[0]:
            item = result[1]
            if hasattr(item, "x"):
                return float(item.x), float(item.y)
            if hasattr(item, "width"):
                return float(item.width), float(item.height)
            if isinstance(item, tuple) and len(item) == 2:
                return float(item[0]), float(item[1])
        return None

    def _safe_value(self, api: Any, element: Any) -> str | None:
        value = self._copy(api, element, api.kAXValueAttribute)
        return value if isinstance(value, str) and len(value) <= 500 else None

    @staticmethod
    def _pid(api: Any, application: Any) -> int:
        result = api.AXUIElementGetPid(application, None)
        if isinstance(result, tuple) and len(result) == 2 and result[0] == 0:
            return int(result[1])
        return 0


class WindowsUIAutomationProvider:
    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self._elements: dict[str, Any] = {}

    @property
    def provider_id(self) -> str:
        return "windows-uia"

    @property
    def available(self) -> bool:
        return self._module is not None or importlib.util.find_spec("pywinauto") is not None

    def observe(self) -> SemanticSnapshot:
        if not self.available:
            return SemanticSnapshot(
                permission=PermissionState.UNAVAILABLE,
                warnings=("Install pywinauto for native Windows UI Automation",),
            )
        module = self._module or importlib.import_module("pywinauto")
        self._module = module
        try:
            window = module.Desktop(backend="uia").get_active()
            info = window.element_info
            rectangle = _rectangle(info.rectangle)
            window_id = f"uia-window:{info.handle}"
            application_id = f"pid:{info.process_id}"
            elements: list[AccessibleElement] = []
            self._elements.clear()
            for index, control in enumerate((window, *window.descendants())[:2000]):
                control_info = control.element_info
                element_id = f"uia-{index}"
                actions = tuple(
                    name
                    for name in ("invoke", "select", "toggle", "expand", "collapse")
                    if hasattr(control, name)
                )
                elements.append(
                    AccessibleElement(
                        element_id=element_id,
                        window_id=window_id,
                        role=str(control_info.control_type or "unknown").casefold(),
                        name=str(control_info.name or ""),
                        bounds=_rectangle(control_info.rectangle),
                        enabled=bool(control_info.enabled),
                        focused=bool(control_info.has_keyboard_focus),
                        actions=actions,
                    )
                )
                self._elements[element_id] = control
            return SemanticSnapshot(
                permission=PermissionState.GRANTED,
                windows=(
                    Window(
                        window_id=window_id,
                        application_id=application_id,
                        application_name=str(info.name or "Focused application"),
                        title=str(info.name or ""),
                        bounds=rectangle or Rectangle(left=0, top=0, width=1, height=1),
                        focused=True,
                    ),
                ),
                elements=tuple(elements),
                active_application_id=application_id,
                focused_window_id=window_id,
            )
        except Exception as error:
            return SemanticSnapshot(
                permission=PermissionState.UNAVAILABLE,
                warnings=(f"Windows UI Automation failed: {error}",),
            )

    def invoke(self, element_id: str) -> None:
        control = self._elements.get(element_id)
        if control is None:
            raise ValueError("accessibility element is unknown or expired")
        for action in ("invoke", "select", "toggle", "expand"):
            method = getattr(control, action, None)
            if callable(method):
                method()
                return
        raise ValueError("accessibility element has no invokable action")


class LinuxATSPIProvider:
    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self._elements: dict[str, Any] = {}

    @property
    def provider_id(self) -> str:
        return "linux-atspi"

    @property
    def available(self) -> bool:
        return self._module is not None or importlib.util.find_spec("pyatspi") is not None

    def observe(self) -> SemanticSnapshot:
        if not self.available:
            return SemanticSnapshot(
                permission=PermissionState.UNAVAILABLE,
                warnings=("Install the system python3-pyatspi package for AT-SPI",),
            )
        api = self._module or importlib.import_module("pyatspi")
        self._module = api
        try:
            desktop = api.Registry.getDesktop(0)
            application = next(
                (app for app in desktop if app.getState().contains(api.STATE_ACTIVE)),
                None,
            )
            if application is None:
                return SemanticSnapshot(permission=PermissionState.GRANTED)
            window = next(iter(application), application)
            application_id = f"atspi:{application.name}"
            window_id = f"{application_id}:window"
            elements: list[AccessibleElement] = []
            self._elements.clear()
            pending: list[tuple[Any, str | None]] = [(window, None)]
            while pending and len(elements) < 2000:
                item, parent_id = pending.pop(0)
                element_id = f"atspi-{len(elements)}"
                state = item.getState()
                actions = _atspi_actions(item)
                elements.append(
                    AccessibleElement(
                        element_id=element_id,
                        window_id=window_id,
                        role=str(item.getRoleName() or "unknown").casefold(),
                        name=str(item.name or ""),
                        bounds=_atspi_bounds(item, api),
                        enabled=state.contains(api.STATE_ENABLED),
                        focused=state.contains(api.STATE_FOCUSED),
                        parent_id=parent_id,
                        actions=actions,
                    )
                )
                self._elements[element_id] = item
                pending.extend((child, element_id) for child in item)
            bounds = _atspi_bounds(window, api) or Rectangle(left=0, top=0, width=1, height=1)
            return SemanticSnapshot(
                permission=PermissionState.GRANTED,
                windows=(
                    Window(
                        window_id=window_id,
                        application_id=application_id,
                        application_name=str(application.name or "Focused application"),
                        title=str(window.name or application.name or ""),
                        bounds=bounds,
                        focused=True,
                    ),
                ),
                elements=tuple(elements),
                active_application_id=application_id,
                focused_window_id=window_id,
            )
        except Exception as error:
            return SemanticSnapshot(
                permission=PermissionState.UNAVAILABLE,
                warnings=(f"AT-SPI collection failed: {error}",),
            )

    def invoke(self, element_id: str) -> None:
        item = self._elements.get(element_id)
        if item is None:
            raise ValueError("accessibility element is unknown or expired")
        action = item.queryAction()
        for index in range(action.nActions):
            if action.getName(index).casefold() in {"click", "press", "activate", "invoke"}:
                if not action.doAction(index):
                    raise RuntimeError("AT-SPI action was rejected")
                return
        raise ValueError("accessibility element has no invokable action")


def _rectangle(value: Any) -> Rectangle | None:
    if value is None:
        return None
    left, top, right, bottom = int(value.left), int(value.top), int(value.right), int(value.bottom)
    if right <= left or bottom <= top:
        return None
    return Rectangle(left=left, top=top, width=right - left, height=bottom - top)


def _atspi_bounds(item: Any, api: Any) -> Rectangle | None:
    try:
        extents = item.queryComponent().getExtents(api.DESKTOP_COORDS)
    except Exception:
        return None
    if extents.width <= 0 or extents.height <= 0:
        return None
    return Rectangle(
        left=int(extents.x),
        top=int(extents.y),
        width=int(extents.width),
        height=int(extents.height),
    )


def _atspi_actions(item: Any) -> tuple[str, ...]:
    try:
        action = item.queryAction()
    except Exception:
        return ()
    return tuple(str(action.getName(index)).casefold() for index in range(action.nActions))
