# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.agent.environment import ExecutionEnvironment
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.harness import validate_harness_authentication
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentProfile,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry
from tests.unit.agent.test_inference_relay import CustomInferenceTarget


@pytest.mark.parametrize("name", ["COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "github_token"])
def test_copilot_native_model_credentials_rejected_under_byok(name: str) -> None:
    harness = HarnessProfile(credential_env=(name,))
    validate_harness_authentication(harness=harness, target_backed=False)
    with pytest.raises(ValueError, match="native model credentials"):
        validate_harness_authentication(harness=harness, target_backed=True)


@pytest.mark.parametrize("name", ["OPENCODE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"])
def test_opencode_native_credentials_rejected_under_byok(name: str) -> None:
    with pytest.raises(ValueError, match="native model credentials"):
        validate_harness_authentication(
            harness=HarnessProfile(harness_kind="opencode", credential_env=(name,)), target_backed=True
        )


@pytest.mark.parametrize(
    "name", ["HOME", "PATH", "PYRIT_RELAY_TOKEN", "COPILOT_PROVIDER_API_KEY", "OPENCODE_CONFIG_CONTENT"]
)
@pytest.mark.parametrize("target_backed", [False, True])
def test_credential_refs_cannot_override_execution_settings(name: str, target_backed: bool) -> None:
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(harness=HarnessProfile(credential_env=(name,)), target_backed=target_backed)


def test_authentication_method_is_not_silently_dropped() -> None:
    harness = HarnessProfile(authentication_method="copilot-login")
    validate_harness_authentication(harness=harness, target_backed=False)
    with pytest.raises(ValueError, match="remove it explicitly"):
        validate_harness_authentication(harness=harness, target_backed=True)


@pytest.mark.usefixtures("patch_central_database")
def test_python_target_applies_auth_validation_before_source_resolution() -> None:
    with pytest.raises(ValueError, match="native model credentials"):
        AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(target_registry_name="source"),
                harness_profile=HarnessProfile(credential_env=("GITHUB_TOKEN",)),
                environment_template=EnvironmentTemplate(image="test"),
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
                environment_template=EnvironmentTemplate(image="test"),
            )
        )
    record = AgentExecution(owner_id="test", target_id="target", conversation_id="one", profile=target.profile)
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with (
        patch.dict("os.environ", {"TEST_REPO_CREDENTIAL": "sentinel-tool-secret"}),
        patch.object(environment, "_docker_async", AsyncMock(side_effect=['[{"Id":"sha256:test"}]', "id"])) as docker,
    ):
        assert environment.credential_values() == {"TEST_REPO_CREDENTIAL": "sentinel-tool-secret"}
        await environment.prepare_async()
    assert "TEST_REPO_CREDENTIAL" in docker.call_args.args
    assert "sentinel-tool-secret" not in str(docker.call_args)
    assert "sentinel-tool-secret" not in target.get_identifier().model_dump_json()


async def test_manager_rejects_unresolved_binding_without_provisioning_async(tmp_path: Path) -> None:
    manager = AgentExecutionManager(root=tmp_path)
    with (
        patch.object(manager, "start_async", new_callable=AsyncMock) as start,
        pytest.raises(ValueError, match="resolved"),
    ):
        await manager.send_async(
            profile=AgentProfile(image="test", target_registry_name="model"),
            conversation_id="one",
            target_id="target",
            request_id="request",
            prompt="hello",
            has_history=False,
        )
    start.assert_not_awaited()
