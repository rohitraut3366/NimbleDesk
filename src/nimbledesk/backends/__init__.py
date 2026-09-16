from nimbledesk.backends.macos import MacOSNativeBackend
from nimbledesk.backends.native import NativeDesktopBackend
from nimbledesk.backends.portable import PortableDesktopBackend
from nimbledesk.backends.semantic import system_semantic_provider
from nimbledesk.backends.simulator import SimulatorBackend
from nimbledesk.backends.wayland import WaylandPortalBackend
from nimbledesk.backends.windows import WindowsNativeBackend

__all__ = [
    "NativeDesktopBackend",
    "MacOSNativeBackend",
    "AdapterDesktopBackend",
    "PortableDesktopBackend",
    "SimulatorBackend",
    "WaylandPortalBackend",
    "WindowsNativeBackend",
    "system_semantic_provider",
    "registry_for_host",
]
from nimbledesk.backends.adapters import AdapterDesktopBackend, registry_for_host
