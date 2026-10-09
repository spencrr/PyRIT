# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Capability-driven binding resolution, with no source-target endpoint/credential export."""

from dataclasses import dataclass

from pyrit.models.agent_execution import AgentTargetConfiguration, ModelBinding
from pyrit.models.identifiers.target_identifier import TargetIdentifier
from pyrit.models.model_inference import InferenceRequirements
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry


@dataclass(frozen=True, kw_only=True)
class ResolvedModelBinding:
    """A pinned source target and the inference contract it must implement."""

    target: PromptTarget
    identifier_hash: str
    model: str
    requirements: InferenceRequirements


def resolve_model_binding(configuration: AgentTargetConfiguration) -> ResolvedModelBinding | None:
    """
    Resolve a registered target without provisioning resources or making model calls.

    Returns:
        ResolvedModelBinding | None: A pinned target, or native harness model access.

    Raises:
        ValueError: The source is missing, changed, or incompatible.
    """
    binding = configuration.model_binding
    if binding.target_registry_name is None:
        return None
    executable = configuration.harness_profile.command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable not in ("copilot", "copilot.exe", "copilot.cmd"):
        raise ValueError(
            "Target-backed BYOK launch configuration currently supports Copilot CLI; "
            "this harness needs its own provider-configuration adapter"
        )
    source = TargetRegistry.get_registry_singleton().instances.get(binding.target_registry_name)
    if source is None:
        raise ValueError(f"Model target '{binding.target_registry_name}' is not registered")
    capabilities = source.inference_capabilities
    if capabilities is None:
        raise ValueError("Selected target does not implement single-inference passthrough")
    requirements = configuration.harness_profile.inference_requirements
    if binding.wire_api is not None:
        requirements = requirements.model_copy(update={"wire_api": binding.wire_api})
    reasons = capabilities.incompatibilities(requirements)
    if reasons:
        raise ValueError("; ".join(reasons))
    identity = TargetIdentifier.from_component_identifier(source.get_identifier())
    if binding.target_identifier_hash and identity.hash != binding.target_identifier_hash:
        raise ValueError("Bound model target configuration changed; create a new agent target")
    model = binding.model or identity.underlying_model_name or identity.model_name
    if not model:
        raise ValueError("Supply the harness model identity for this target binding")
    return ResolvedModelBinding(target=source, identifier_hash=identity.hash, model=model, requirements=requirements)


def pinned_configuration(
    *, configuration: AgentTargetConfiguration, binding: ResolvedModelBinding
) -> AgentTargetConfiguration:
    """
    Snapshot the resolved identity without persisting credentials.

    Returns:
        AgentTargetConfiguration: Configuration with a pinned model binding.
    """
    return configuration.model_copy(
        update={
            "model_binding": ModelBinding(
                model=binding.model,
                target_registry_name=configuration.model_binding.target_registry_name,
                target_identifier_hash=binding.identifier_hash,
                wire_api=binding.requirements.wire_api,
            )
        }
    )
