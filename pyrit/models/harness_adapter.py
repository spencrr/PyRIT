# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declarative launch-adapter capabilities, independent of resource provisioning."""

from pydantic import BaseModel, ConfigDict, field_validator

from pyrit.models.identifiers.class_name_utils import validate_registry_name
from pyrit.models.model_inference import InferenceWireApi


class HarnessAdapterInfo(BaseModel):
    """Non-secret authoring metadata for a trusted installed adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    display_name: str
    default_command: tuple[str, ...] = ()
    wire_apis: tuple[InferenceWireApi, ...] = ()
    native_model_credentials: tuple[str, ...] = ()
    reserved_environment: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        validate_registry_name(value)
        return value
