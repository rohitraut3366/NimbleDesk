from nimbledesk.backends.native import NativeDesktopBackend
from nimbledesk.backends.portable import PortableDesktopBackend
from nimbledesk.backends.semantic import MacOSAccessibilityProvider, SemanticSnapshot
from nimbledesk.protocol.models import (
    AccessibleElement,
    ActionKind,
    ActionRequest,
    ActionStatus,
    Capability,
    ElementTarget,
    PermissionState,
    Rectangle,
    Window,
)
from tests.fakes import FakeAutomation


class FakeSemanticProvider:
    provider_id = "fake-native"
    available = True

    def __init__(self) -> None:
        self.invoked: list[str] = []

    def observe(self) -> SemanticSnapshot:
        return SemanticSnapshot(
            permission=PermissionState.GRANTED,
            windows=(
                Window(
                    window_id="window-1",
                    application_id="fixture.app",
                    application_name="Fixture",
                    title="Fixture window",
                    bounds=Rectangle(left=50, top=50, width=800, height=600),
                    focused=True,
                ),
            ),
            elements=(
                AccessibleElement(
                    element_id="button-1",
                    window_id="window-1",
                    role="button",
                    name="Create",
                    actions=("invoke",),
                ),
            ),
            active_application_id="fixture.app",
            focused_window_id="window-1",
        )

    def invoke(self, element_id: str) -> None:
        if element_id != "button-1":
            raise ValueError("unknown element")
        self.invoked.append(element_id)


def test_native_backend_merges_semantic_observation_and_invokes_element() -> None:
    semantic = FakeSemanticProvider()
    backend = NativeDesktopBackend(PortableDesktopBackend(FakeAutomation()), semantic)

    observation = backend.observe()
    result = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.CLICK,
            target=ElementTarget(
                observation_id=observation.observation_id,
                element_id="button-1",
            ),
        )
    )

    assert observation.active_application_id == "fixture.app"
    assert observation.elements[0].name == "Create"
    assert Capability.ACCESSIBILITY in observation.capabilities
    assert observation.permissions[Capability.ACCESSIBILITY] is PermissionState.GRANTED
    assert result.status is ActionStatus.COMPLETED
    assert semantic.invoked == ["button-1"]


def test_native_backend_does_not_fall_back_when_semantic_target_is_unknown() -> None:
    automation = FakeAutomation()
    backend = NativeDesktopBackend(
        PortableDesktopBackend(automation), FakeSemanticProvider()
    )
    observation = backend.observe()

    result = backend.execute(
        ActionRequest(
            session_id="session",
            kind=ActionKind.CLICK,
            target=ElementTarget(
                observation_id=observation.observation_id,
                element_id="missing",
            ),
        )
    )

    assert result.status is ActionStatus.FAILED
    assert automation.calls == []


class DeniedMacOSAPI:
    @staticmethod
    def AXIsProcessTrusted() -> bool:
        return False


def test_macos_provider_reports_denied_permission() -> None:
    snapshot = MacOSAccessibilityProvider(DeniedMacOSAPI()).observe()

    assert snapshot.permission is PermissionState.DENIED
    assert "Grant Accessibility permission" in snapshot.warnings[0]
