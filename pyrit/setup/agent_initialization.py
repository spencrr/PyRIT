# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared preset registration/composition used by built-in and private initializers."""

import logging
import os
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, get_origin

from pydantic import BaseModel

from pyrit.agent.environment import validate_environment_template
from pyrit.agent.harness import validate_harness_authentication, validate_inference_adapter
from pyrit.agent.model_binding import resolve_model_binding
from pyrit.common import REQUIRED_VALUE
from pyrit.models import Parameter
from pyrit.models.agent_execution import AgentTargetConfiguration, ModelBinding
from pyrit.models.agent_setup import (
    AgentPresetSelection,
    AgentTargetComposition,
    AgentTargetOrigin,
    AgentTargetProvenance,
)
from pyrit.registry import AgentConfigurationRegistry, TargetRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer

if TYPE_CHECKING:
    from pyrit.prompt_target import AgentTarget

logger = logging.getLogger(__name__)


class AgentConfigurationInitializer(PyRITInitializer):
    """An initializer with a non-mutating, isolated agent-configuration preflight."""

    @abstractmethod
    def preflight(self, presets: AgentConfigurationRegistry) -> AgentTargetComposition | None:
        """Register inert values in the supplied registry or return a composition to validate."""


def initializer_parameters(*, model: type[BaseModel], exclude: frozenset[str] = frozenset()) -> list[Parameter]:
    """
    Project model fields into the existing scalar/list initializer parameter contract.

    Returns:
        list[Parameter]: The model's editable flat parameters.
    """
    parameters = []
    for name, field in model.model_fields.items():
        if name in exclude:
            continue
        annotation = field.annotation
        default = REQUIRED_VALUE if field.is_required() else field.get_default(call_default_factory=True)
        if get_origin(annotation) is tuple:
            annotation = list[str]
            if isinstance(default, tuple):
                default = list(default)
        if isinstance(default, Enum):
            # The bag materializes declared defaults, so they must already be in their wire form.
            default = default.value
        parameters.append(
            Parameter(
                name=name,
                description=field.description or name.replace("_", " ").capitalize(),
                param_type=annotation,
                default=default,
            )
        )
    return parameters


def initializer_values(initializer: PyRITInitializer) -> dict[str, Any]:
    """
    Read the resolved parameter bag as model input without serializing nested Python objects.

    Returns:
        dict[str, Any]: Supplied or defaulted values; absent optional values stay model-owned.

    Raises:
        ValueError: A required parameter is absent.
    """
    result: dict[str, Any] = {}
    for parameter in type(initializer).supported_parameters():
        value = initializer.params.get(parameter.name)
        if value is None or value is REQUIRED_VALUE:
            if parameter.required:
                raise ValueError(f"Initializer requires '{parameter.name}'")
            continue
        result[parameter.name] = value
    return result


def resolve_agent_configuration(
    *,
    composition: AgentTargetComposition,
    presets: AgentConfigurationRegistry | None = None,
) -> AgentTargetConfiguration:
    """
    Resolve preset names without constructing targets, loading credentials, or provisioning.

    Returns:
        AgentTargetConfiguration: Detached settings ready for ordinary target construction.

    Raises:
        ValueError: References, consent, or harness authentication are incompatible.
    """
    presets = presets or AgentConfigurationRegistry.get_registry_singleton()
    harness = presets.get_harness(composition.harness_profile)
    template = presets.get_environment(composition.environment_template)
    template = template.model_copy(update={"local_execution_acknowledged": composition.local_execution_acknowledged})
    values = composition.model_dump(
        exclude={
            "harness_profile",
            "environment_template",
            "model",
            "model_target",
            "model_target_hash",
            "configuration_name",
            "expected_target_hash",
            "wire_api",
            "required",
            "local_execution_acknowledged",
            "name",
        }
    )
    if composition.configuration_name is not None:
        values["name"] = composition.configuration_name
    configuration = AgentTargetConfiguration(
        **values,
        harness_profile=harness,
        environment_template=template,
        model_binding=ModelBinding(
            model=composition.model,
            target_registry_name=composition.model_target,
            wire_api=composition.wire_api,
            target_identifier_hash=composition.model_target_hash,
        ),
    )
    configuration.to_profile()
    validate_environment_template(template)
    validate_harness_authentication(harness=harness, target_backed=composition.model_target is not None)
    if composition.model_target is not None:
        validate_inference_adapter(
            kind=harness.harness_kind, wire_api=composition.wire_api or harness.inference_requirements.wire_api
        )
    return configuration


def register_agent_target(composition: AgentTargetComposition) -> "AgentTarget | None":
    """
    Compose and register one target through TargetRegistry without starting an execution.

    Returns:
        AgentTarget | None: The registered target; None only for explicitly optional unavailable dependencies.

    Raises:
        ValueError: Static validation, required dependencies, or name conflicts fail.
        TypeError: The registered target implementation does not implement AgentTarget.
    """
    from pyrit.prompt_target import AgentTarget

    presets = AgentConfigurationRegistry.get_registry_singleton()
    registry = TargetRegistry.get_registry_singleton()
    configuration = resolve_agent_configuration(composition=composition, presets=presets)
    existing = registry.instances.get(composition.name)
    if existing is None:
        registry.instances.validate_name_available(composition.name)
    source_name = composition.model_target
    if source_name == composition.name:
        raise ValueError("An agent target cannot replace its own model source")
    missing: list[str] = []
    if source_name is not None and registry.instances.get(source_name) is None:
        missing.append(f"model target '{source_name}' (register model targets before agent compositions)")
    else:
        resolve_model_binding(configuration)
    missing.extend(
        f"credential reference '{name}'" for name in configuration.harness_profile.credential_env if not os.getenv(name)
    )
    if missing:
        reason = f"Agent target '{composition.name}' unavailable: {', '.join(missing)}"
        if composition.required or registry.instances.get(composition.name) is not None:
            raise ValueError(reason)
        presets.set_target_unavailable(name=composition.name, reason=reason)
        logger.warning(reason)
        return None
    target = registry.create_instance("AgentTarget", agent_configuration=configuration)
    if not isinstance(target, AgentTarget):
        raise TypeError("AgentTarget registry entry must construct an AgentTarget")
    if (
        composition.expected_target_hash is not None
        and target.get_identifier().hash != composition.expected_target_hash
    ):
        raise ValueError(f"Saved agent target '{composition.name}' no longer matches its pinned configuration")
    if existing is not None:
        if not isinstance(existing, AgentTarget) or existing.get_identifier().hash != target.get_identifier().hash:
            raise ValueError(f"Target '{composition.name}' already has a different definition")
        presets.set_target_unavailable(name=composition.name, reason=None)
        return existing
    registry.instances.register(
        target,
        name=composition.name,
        metadata={
            "agent_composition": composition.model_dump(mode="json"),
            "agent_configuration_provenance": AgentTargetProvenance(
                origin=AgentTargetOrigin.INITIALIZER,
                presets=AgentPresetSelection(
                    harness_profile=composition.harness_profile, environment_template=composition.environment_template
                ),
            ).model_dump(mode="json"),
        },
    )
    presets.set_target_unavailable(name=composition.name, reason=None)
    return target


def preflight_agent_initializers(
    *,
    initializers: Sequence[PyRITInitializer],
    environment: Mapping[str, str],
) -> None:
    """
    Validate declarative agent settings in an isolated namespace before live replacement.

    Raises:
        ValueError: The plan is invalid or requires opaque provider initialization that cannot be dry-run safely.
    """
    presets = AgentConfigurationRegistry()
    targets = TargetRegistry()
    compositions: dict[str, AgentTargetConfiguration] = {}
    for initializer in initializers:
        if isinstance(initializer, AgentConfigurationInitializer):
            composition = initializer.preflight(presets)
            if composition is None:
                continue
            targets.instances.validate_name_available(composition.name)
            configuration = resolve_agent_configuration(composition=composition, presets=presets)
            if composition.name in compositions and compositions[composition.name] != configuration:
                raise ValueError(f"Conflicting agent target composition '{composition.name}'")
            compositions[composition.name] = configuration
            missing = [name for name in configuration.harness_profile.credential_env if not environment.get(name)]
            if missing and composition.required:
                raise ValueError(f"Missing credential references for '{composition.name}': {', '.join(missing)}")
            if composition.model_target is not None or composition.expected_target_hash is not None:
                raise ValueError(
                    "Live replacement of target-backed or pinned agent compositions requires a restart: "
                    "their complete startup dependencies cannot be validated without executing initialization"
                )
