from __future__ import annotations

import platform
from pathlib import Path
from time import time

from nimbledesk.adapters.models import AdapterManifest
from nimbledesk.adapters.runner import AdapterError, IsolatedAdapterRunner
from nimbledesk.ports import DesktopBackend
from nimbledesk.protocol.models import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Capability,
    CaptureOptions,
    DesktopObservation,
    Rectangle,
    ScreenCapture,
)


class AdapterRegistry:
    def __init__(self, manifests: dict[str, AdapterManifest]) -> None:
        self.manifests = manifests

    @classmethod
    def load(cls, directory: Path) -> AdapterRegistry:
        manifests: dict[str, AdapterManifest] = {}
        if not directory.is_dir():
            return cls(manifests)
        for path in sorted(directory.glob("*.json")):
            manifest = AdapterManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if manifest.adapter_id in manifests:
                raise ValueError(f"duplicate adapter ID: {manifest.adapter_id}")
            manifests[manifest.adapter_id] = manifest
        return cls(manifests)


class AdapterDesktopBackend:
    def __init__(
        self,
        desktop: DesktopBackend,
        registry: AdapterRegistry,
        runner: IsolatedAdapterRunner | None = None,
    ) -> None:
        self._desktop = desktop
        self._registry = registry
        self._runner = runner or IsolatedAdapterRunner()

    @property
    def backend_id(self) -> str:
        return f"adapters+{self._desktop.backend_id}"

    @property
    def capabilities(self) -> frozenset[Capability]:
        capabilities = set(self._desktop.capabilities)
        if self._registry.manifests:
            capabilities.add(Capability.APPLICATION_ADAPTERS)
        return frozenset(capabilities)

    def observe(self) -> DesktopObservation:
        observation = self._desktop.observe()
        return observation.model_copy(update={"capabilities": self.capabilities})

    def capture(
        self,
        observation_id: str,
        region: Rectangle | None = None,
        options: CaptureOptions | None = None,
    ) -> ScreenCapture:
        return self._desktop.capture(observation_id, region, options)

    def execute(self, request: ActionRequest) -> ActionResult:
        if request.kind is not ActionKind.APP_COMMAND:
            return self._desktop.execute(request)
        started_at = time()
        try:
            adapter_id = str(request.arguments["adapter_id"])
            command = str(request.arguments["command"])
            command_arguments = request.arguments.get("arguments", {})
            if not isinstance(command_arguments, dict):
                raise AdapterError("adapter arguments must be an object")
            manifest = self._registry.manifests.get(adapter_id)
            if manifest is None:
                raise AdapterError(f"adapter is not installed: {adapter_id}")
            trusted_grants = request.arguments.get("_trusted_granted_paths", [])
            if not isinstance(trusted_grants, list):
                raise AdapterError("invalid trusted path grants")
            result = self._runner.execute(
                manifest,
                command,
                command_arguments,
                granted_paths=tuple(Path(path) for path in trusted_grants),
            )
            status = ActionStatus.COMPLETED if result.success else ActionStatus.FAILED
            message = "Adapter command completed" if result.success else (result.error or "failed")
            data = {"adapter_id": adapter_id, "command": command, "result": result.result}
        except (AdapterError, KeyError, TypeError, ValueError) as error:
            status = ActionStatus.FAILED
            message = str(error)
            data = {}
        return ActionResult(
            action_id=request.action_id,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=time(),
            data=data,
        )

    def cancel_input(self) -> None:
        self._desktop.cancel_input()


def registry_for_host(directory: Path) -> AdapterRegistry:
    registry = AdapterRegistry.load(directory)
    supported = {
        adapter_id: manifest
        for adapter_id, manifest in registry.manifests.items()
        if platform.system() in manifest.supported_platforms
    }
    return AdapterRegistry(supported)
