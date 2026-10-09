# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.agent.model_binding import resolve_model_binding
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile, ModelBinding
from pyrit.models.model_inference import InferenceRequirements, InferenceWireApi
from pyrit.prompt_target import AgentTarget, OpenAIChatTarget, TextTarget
from pyrit.registry import TargetRegistry
from tests.unit.agent.test_inference_relay import CustomInferenceTarget


@pytest.mark.usefixtures("patch_central_database")
def test_any_target_implementing_capabilities_can_bind() -> None:
    registry = TargetRegistry.get_registry_singleton()
    source = CustomInferenceTarget()
    config = AgentTargetConfiguration(
        model_binding=ModelBinding(model="base-model", target_registry_name="custom"),
        environment_template=EnvironmentTemplate(image="test"),
    )
    with patch.object(registry.instances, "get", return_value=source):
        binding = resolve_model_binding(config)
        assert binding.target is source
        assert binding.identifier_hash == source.get_identifier().hash
        target = AgentTarget(agent_configuration=config)
        assert target.agent_configuration.model_binding.target_identifier_hash == binding.identifier_hash


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("source_type", [TextTarget, OpenAIChatTarget])
def test_source_rejection_is_explicit(source_type: type[TextTarget] | type[OpenAIChatTarget]) -> None:
    source = (
        source_type()
        if source_type is TextTarget
        else source_type(endpoint="https://provider.test/v1", model_name="model", api_key="backend-secret")
    )
    config = AgentTargetConfiguration(
        model_binding=ModelBinding(model="model", target_registry_name="source"),
        harness_profile=HarnessProfile(
            inference_requirements=InferenceRequirements(wire_api=InferenceWireApi.RESPONSES)
        ),
        environment_template=EnvironmentTemplate(image="test"),
    )
    with patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=source):
        with pytest.raises(ValueError, match="inference"):
            resolve_model_binding(config)


@pytest.mark.usefixtures("patch_central_database")
def test_changed_source_identity_and_missing_modality_fail_preflight() -> None:
    source = CustomInferenceTarget()
    config = AgentTargetConfiguration(
        model_binding=ModelBinding(model="model", target_registry_name="source", target_identifier_hash="old"),
        environment_template=EnvironmentTemplate(image="test"),
    )
    with patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=source):
        with pytest.raises(ValueError, match="changed"):
            resolve_model_binding(config)
        config = config.model_copy(
            update={
                "model_binding": ModelBinding(model="model", target_registry_name="source"),
                "harness_profile": HarnessProfile(
                    inference_requirements=InferenceRequirements(input_modalities=frozenset({"audio"}))
                ),
            }
        )
        with pytest.raises(ValueError, match="audio"):
            resolve_model_binding(config)


def test_other_harness_cannot_silently_ignore_copilot_provider_configuration() -> None:
    config = AgentTargetConfiguration(
        model_binding=ModelBinding(model="model", target_registry_name="source"),
        harness_profile=HarnessProfile(command=("opencode", "acp")),
        environment_template=EnvironmentTemplate(image="test"),
    )
    with pytest.raises(ValueError, match="provider-configuration adapter"):
        resolve_model_binding(config)
