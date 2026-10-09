# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models.agent_execution import (
    AgentExecution,
    AgentProfile,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)


def test_configuration_separates_model_harness_and_environment() -> None:
    harness = HarnessProfile(command=("copilot", "--acp"), credential_env=("COPILOT_GITHUB_TOKEN",))
    template = EnvironmentTemplate(image="copilot@sha256:test")
    first = AgentTargetConfiguration(
        harness_profile=harness, environment_template=template, model_binding=ModelBinding(model="first")
    )
    second = first.model_copy(update={"model_binding": ModelBinding(model="second")})
    assert second.harness_profile is first.harness_profile
    assert second.environment_template is first.environment_template
    assert first.model_binding.model == "first"
    assert second.to_profile().model == "second"
    with pytest.raises(ValidationError, match="frozen"):
        first.model_binding.model = "changed"


def test_legacy_recipe_roundtrip_and_saved_execution_projection() -> None:
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        command=("example", "--acp"),
        model="model",
        credential_env=("TEST_TOKEN",),
        artifact_paths=("receipt.json",),
        idle_timeout_seconds=12,
    )
    config = AgentTargetConfiguration.from_profile(profile)
    assert config.to_profile() == profile
    record = AgentExecution(owner_id="test", conversation_id="conversation", target_id="target", profile=profile)
    saved = record.model_dump_json()
    reloaded = AgentExecution.model_validate_json(saved)
    assert reloaded.configuration == config
    assert reloaded.profile == profile


@pytest.mark.parametrize(
    "fields",
    [
        {"environment_template": {"environment": "local"}},
        {"environment_template": {"environment": "docker"}},
        {"environment_template": {"image": "test"}, "harness_profile": {"command": []}},
        {"environment_template": {"image": "test"}, "harness_profile": {"credential_env": ["literal token"]}},
        {"environment_template": {"image": "test"}, "artifact_paths": ["../outside"]},
        {"environment_template": {"image": "test"}, "model_binding": {"endpoint": "https://unsupported"}},
    ],
)
def test_invalid_or_unsupported_configuration_is_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentTargetConfiguration.model_validate(fields)
