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

_LOCAL = {"environment": "local", "local_execution_acknowledged": True}


def test_configuration_separates_model_harness_and_environment() -> None:
    harness = HarnessProfile(command=("copilot", "--acp"), credential_env=("COPILOT_GITHUB_TOKEN",))
    template = EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
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


def test_flattened_profile_persists_with_the_execution() -> None:
    config = AgentTargetConfiguration(
        model_binding=ModelBinding(model="model"),
        harness_profile=HarnessProfile(command=("example", "--acp"), credential_env=("TEST_TOKEN",)),
        environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True),
        artifact_paths=("receipt.json",),
        idle_timeout_seconds=12,
    )
    profile = config.to_profile()
    assert profile == AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        command=("example", "--acp"),
        model="model",
        credential_env=("TEST_TOKEN",),
        artifact_paths=("receipt.json",),
        idle_timeout_seconds=12,
    )
    record = AgentExecution(owner_id="test", conversation_id="conversation", target_id="target", profile=profile)
    reloaded = AgentExecution.model_validate_json(record.model_dump_json())
    assert reloaded.profile == profile


@pytest.mark.parametrize(
    "fields",
    [
        {"environment_template": {"environment": "local"}},
        {"environment_template": {}},
        {"environment_template": {"environment": "docker", "image": "test", "docker_cpus": 0}},
        {"environment_template": _LOCAL, "harness_profile": {"command": []}},
        {"environment_template": _LOCAL, "harness_profile": {"credential_env": ["literal token"]}},
        {"environment_template": _LOCAL, "artifact_paths": ["../outside"]},
        {"environment_template": _LOCAL, "model_binding": {"endpoint": "https://unsupported"}},
    ],
)
def test_invalid_or_unsupported_configuration_is_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentTargetConfiguration.model_validate(fields)
