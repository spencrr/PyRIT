# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Flat initializer inputs; resolved runtime configuration remains AgentTargetConfiguration."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.models.agent_execution import AgentEnvironment, EnvironmentTemplate, HarnessProfile
from pyrit.models.identifiers.class_name_utils import validate_registry_name
from pyrit.models.model_inference import InferenceWireApi
from pyrit.models.parameter import Parameter


def _name(value: str) -> str:
    validate_registry_name(value)
    return value


class HarnessProfileDefinition(HarnessProfile):
    """One named harness value; credentials remain environment names."""

    name: str
    _name_validator = field_validator("name")(_name)


class EnvironmentTemplateDefinition(EnvironmentTemplate):
    """One named starting environment, not permission to run it."""

    name: str
    _name_validator = field_validator("name")(_name)


class RegisteredHarnessProfile(BaseModel):
    """A selectable runtime preset with its registration source, not a live harness."""

    name: str
    profile: HarnessProfile
    initializer: str | None = None


class RegisteredEnvironmentTemplate(BaseModel):
    """A selectable starting environment without execution consent."""

    name: str
    template: EnvironmentTemplate
    initializer: str | None = None


class AgentPresetCatalog(BaseModel):
    """Snapshot of registered configuration, shared by API clients."""

    harness_profiles: list[RegisteredHarnessProfile] = Field(default_factory=list)
    environment_templates: list[RegisteredEnvironmentTemplate] = Field(default_factory=list)
    unavailable_targets: dict[str, str] = Field(default_factory=dict)
    environment_parameters: dict[AgentEnvironment, list[Parameter]] = Field(default_factory=dict)


class AgentPresetSelection(BaseModel):
    """Optional provenance references; resolved settings remain the target's identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    harness_profile: str | None = None
    environment_template: str | None = None

    @field_validator("harness_profile", "environment_template")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is not None:
            validate_registry_name(value)
        return value


class AgentTargetOrigin(str, Enum):
    """How a configured target was registered, not where its process runs."""

    INITIALIZER = "initializer"
    RUNTIME = "runtime"


class AgentTargetProvenance(BaseModel):
    """Origin retained outside the target's identity and execution recipe."""

    origin: AgentTargetOrigin
    presets: AgentPresetSelection = Field(default_factory=AgentPresetSelection)


class AgentTargetComposition(BaseModel):
    """Initializer references and execution limits, resolved before target construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    harness_profile: str
    environment_template: str
    model: str = ""
    model_target: str | None = None
    wire_api: InferenceWireApi | None = None
    required: bool = True
    local_execution_acknowledged: bool = False
    startup_timeout_seconds: float = Field(default=60, gt=0, le=600)
    turn_timeout_seconds: float = Field(default=180, gt=0, le=3600)
    idle_timeout_seconds: float = Field(default=300, gt=0, le=86400)
    lifetime_seconds: float = Field(default=900, gt=0, le=86400)
    cancellation_grace_seconds: float = Field(default=10, gt=0, le=60)
    approval_timeout_seconds: float = Field(default=120, gt=0, le=600)
    interactive_hold_seconds: float = Field(default=300, gt=0, le=3600)
    max_evidence_bytes: int = Field(default=16 * 1024 * 1024, ge=4096, le=256 * 1024 * 1024)
    artifact_paths: tuple[str, ...] = ()
    max_artifact_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)
    capture_inference_content: bool = False
    max_inference_requests: int = Field(default=100, ge=1, le=100)

    _name_validator = field_validator("name", "harness_profile", "environment_template")(_name)
