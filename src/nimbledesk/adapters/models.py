from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdapterCommand(AdapterModel):
    risk: Literal["observe", "low", "medium", "high", "critical"]
    read_only: bool = False
    reversible: bool = False
    timeout_seconds: Annotated[float, Field(ge=0.1, le=3600)] = 30
    required_arguments: tuple[str, ...] = ()
    path_arguments: tuple[str, ...] = ()


class AdapterManifest(AdapterModel):
    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,79}$")
    version: str
    vendor: str
    entrypoint: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$")
    supported_platforms: frozenset[Literal["Darwin", "Windows", "Linux"]]
    commands: dict[str, AdapterCommand]
    isolation: Literal["trusted", "sandboxed"] = "trusted"
    network_access: bool = False
    writable_path_arguments: tuple[str, ...] = ()

    @model_validator(mode="after")
    def writable_paths_are_declared(self) -> AdapterManifest:
        declared_paths = {
            path_argument
            for command in self.commands.values()
            for path_argument in command.path_arguments
        }
        undeclared = set(self.writable_path_arguments) - declared_paths
        if undeclared:
            raise ValueError(
                "writable adapter paths must be declared path arguments: "
                + ", ".join(sorted(undeclared))
            )
        return self


class AdapterInvocation(AdapterModel):
    command: str
    arguments: dict[str, Any]


class AdapterResult(AdapterModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    stdout_truncated: bool = False
