# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.agent.environment import ExecutionSpec
from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.harness import resolve_credential_values, validate_harness_authentication
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry
from tests.unit.agent.example_adapters import OPENCODE_COMMAND
from tests.unit.agent.test_inference_relay import CustomInferenceTarget


@pytest.mark.parametrize("name", ["COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "github_token"])
def test_copilot_native_model_credentials_rejected_under_byok(name: str) -> None:
    harness = HarnessProfile(credential_env=(name,))
    validate_harness_authentication(harness=harness, target_backed=False)
    with pytest.raises(ValueError, match="native model credentials"):
        validate_harness_authentication(harness=harness, target_backed=True)


@pytest.mark.usefixtures("opencode_adapter")
@pytest.mark.parametrize("name", ["OPENCODE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"])
def test_registered_adapter_native_credentials_rejected_under_byok(name: str) -> None:
    with pytest.raises(ValueError, match="native model credentials"):
        validate_harness_authentication(
            harness=HarnessProfile(harness_kind="opencode", command=OPENCODE_COMMAND, credential_env=(name,)),
            target_backed=True,
        )


@pytest.mark.parametrize("name", ["HOME", "PATH", "PYRIT_RELAY_TOKEN", "COPILOT_PROVIDER_API_KEY", "copilot_home"])
@pytest.mark.parametrize("target_backed", [False, True])
def test_credential_refs_cannot_override_execution_settings(name: str, target_backed: bool) -> None:
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(harness=HarnessProfile(credential_env=(name,)), target_backed=target_backed)


@pytest.mark.usefixtures("opencode_adapter")
def test_reserved_names_follow_the_selected_adapter() -> None:
    validate_harness_authentication(
        harness=HarnessProfile(credential_env=("OPENCODE_CONFIG_CONTENT",)), target_backed=False
    )
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(
            harness=HarnessProfile(
                harness_kind="opencode", command=OPENCODE_COMMAND, credential_env=("OPENCODE_CONFIG_CONTENT",)
            ),
            target_backed=False,
        )


def test_agent_framework_default_agent_requires_a_model_binding() -> None:
    default = HarnessProfile(harness_kind="agent_framework")
    with pytest.raises(ValueError, match="model binding"):
        validate_harness_authentication(harness=default, target_backed=False)
    validate_harness_authentication(harness=default, target_backed=True)
    custom = HarnessProfile(
        harness_kind="agent_framework",
        command=("python", "-m", "pyrit.agent.hosts.agent_framework_acp", "--agent", "team.agents:build"),
        credential_env=("AZURE_OPENAI_API_KEY",),
    )
    validate_harness_authentication(harness=custom, target_backed=False)
    with pytest.raises(ValueError, match="native model credentials"):
        validate_harness_authentication(harness=custom, target_backed=True)
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(
            harness=custom.model_copy(update={"credential_env": ("OPENAI_API_KEY",)}), target_backed=False
        )


def test_authentication_method_is_not_silently_dropped() -> None:
    harness = HarnessProfile(authentication_method="copilot-login")
    validate_harness_authentication(harness=harness, target_backed=False)
    with pytest.raises(ValueError, match="remove it explicitly"):
        validate_harness_authentication(harness=harness, target_backed=True)


def test_missing_credential_reference_fails_before_provisioning() -> None:
    with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="COPILOT_GITHUB_TOKEN"):
        resolve_credential_values(credential_env=("COPILOT_GITHUB_TOKEN",))


@pytest.mark.usefixtures("patch_central_database")
def test_python_target_applies_auth_validation_before_source_resolution() -> None:
    with pytest.raises(ValueError, match="native model credentials"):
        AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(target_registry_name="source"),
                harness_profile=HarnessProfile(credential_env=("GITHUB_TOKEN",)),
                environment_template=EnvironmentTemplate(environment="docker", image="test"),
            )
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_tool_credentials_survive_byok_without_provider_credential_export_async(tmp_path: Path) -> None:
    source = CustomInferenceTarget()
    with patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=source):
        target = AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(target_registry_name="source", model="model"),
                harness_profile=HarnessProfile(credential_env=("TEST_REPO_CREDENTIAL",)),
                environment_template=EnvironmentTemplate(environment="docker", image="test"),
            )
        )
    record = AgentExecution(owner_id="test", target_id="target", conversation_id="one", profile=target.profile)
    session = DockerSession(execution=record, directory=tmp_path)
    with patch.dict("os.environ", {"TEST_REPO_CREDENTIAL": "sentinel-tool-secret"}):
        credentials = resolve_credential_values(credential_env=target.profile.credential_env)
    assert credentials == {"TEST_REPO_CREDENTIAL": "sentinel-tool-secret"}
    docker = AsyncMock(side_effect=['[{"Id":"sha256:test"}]', "id"])
    with patch.object(session, "_docker_async", docker), patch.object(session, "_spawn_async", AsyncMock()):
        await session.launch_async(
            ExecutionSpec(command=target.profile.command, environment={}, credentials=credentials)
        )
    create = docker.call_args_list[1]
    assert "TEST_REPO_CREDENTIAL" in create.args
    assert "sentinel-tool-secret" not in str(create.args)
    assert "sentinel-tool-secret" not in target.get_identifier().model_dump_json()


async def test_manager_rejects_unresolved_binding_without_provisioning_async(tmp_path: Path) -> None:
    manager = AgentExecutionManager(root=tmp_path)
    with (
        patch.object(manager, "start_async", new_callable=AsyncMock) as start,
        pytest.raises(ValueError, match="resolved"),
    ):
        await manager.send_async(
            configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(target_registry_name="model"),
                environment_template=EnvironmentTemplate(environment="docker", image="test"),
            ),
            conversation_id="one",
            target_id="target",
            request_id="request",
            prompt="hello",
            has_history=False,
        )
    start.assert_not_awaited()
