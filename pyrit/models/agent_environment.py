# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative execution-environment provider capabilities, independent of resource provisioning."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from pyrit.models.identifiers.class_name_utils import validate_registry_name


class EnvironmentCapabilities(BaseModel):
    """What a provider promises; an absent capability is refused explicitly, never emulated."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    isolated: bool = False


class EnvironmentProviderInfo(BaseModel):
    """Non-secret authoring metadata for a trusted installed provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    display_name: str
    description: str = ""
    capabilities: EnvironmentCapabilities = Field(default_factory=EnvironmentCapabilities)
    settings_schema: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        validate_registry_name(value)
        return value
