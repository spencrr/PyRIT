# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import json
import tarfile
from pathlib import Path

import pytest

from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.harness import inference_environment, launch_environment, validate_inference_adapter
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentHarnessKind,
    AgentProfile,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.models.model_inference import InferenceWireApi
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry
from tests.unit.agent.example_adapters import OPENCODE_COMMAND, OpenCodeAdapter
from tests.unit.agent.session_helpers import create_container_async


@pytest.mark.parametrize("model", [AgentProfile, HarnessProfile])
def test_harness_kind_is_explicit_and_only_copilot_has_a_default_command(
    model: type[AgentProfile] | type[HarnessProfile],
) -> None:
    extra = {"environment": "local", "local_execution_acknowledged": True} if model is AgentProfile else {}
    # A custom command never changes the adapter on its own.
    assert model(**extra, command=OPENCODE_COMMAND).harness_kind == AgentHarnessKind.COPILOT
    explicit = model(**extra, harness_kind="opencode", command=OPENCODE_COMMAND)
    assert explicit.command == OPENCODE_COMMAND
    assert explicit.harness_kind == "opencode"
    assert model(**extra).command == ("copilot", "--acp", "--stdio")
    for kind in ("acp", "opencode"):
        with pytest.raises(ValueError, match="explicit command"):
            model(**extra, harness_kind=kind)


@pytest.mark.parametrize("directory", ["/host", "../escape", "C:\\host", "foo\\bar"])
def test_working_directory_is_workspace_relative(directory: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        EnvironmentTemplate(environment="local", working_directory=directory)


def test_profile_flattening_keeps_groups_disjoint() -> None:
    configuration = AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="team_v1", command=("renamed-agent", "acp")),
        environment_template=EnvironmentTemplate(
            environment="local", local_execution_acknowledged=True, working_directory="repo"
        ),
    )
    profile = configuration.to_profile()
    assert profile.harness_kind == "team_v1" and profile.command == ("renamed-agent", "acp")
    assert profile.environment == "local" and profile.working_directory == "repo"
    assert not (ModelBinding.model_fields.keys() & HarnessProfile.model_fields.keys())
    assert not (EnvironmentTemplate.model_fields.keys() & HarnessProfile.model_fields.keys())


@pytest.mark.usefixtures("patch_central_database")
def test_identity_ignores_defaults_and_execution_policy() -> None:
    template = EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
    harness = HarnessProfile(command=("python", "scripted-agent.py"))
    terse = AgentTarget(
        agent_configuration=AgentTargetConfiguration(environment_template=template, harness_profile=harness)
    )
    explicit = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            environment_template=template.model_copy(update={"working_directory": "."}),
            harness_profile=harness.model_copy(update={"harness_kind": "copilot", "permission_policy": "deny"}),
            turn_timeout_seconds=5,
            max_evidence_bytes=4096,
        )
    )
    assert explicit.get_identifier().hash == terse.get_identifier().hash
    different = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            environment_template=template.model_copy(update={"working_directory": "repo"}), harness_profile=harness
        )
    )
    assert different.get_identifier().hash != terse.get_identifier().hash
    assert "turn_timeout_seconds" not in str(terse.get_identifier().params)


@pytest.mark.usefixtures("patch_central_database")
def test_harness_profile_is_validated_at_construction() -> None:
    template = EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
    with pytest.raises(ValueError, match="not registered|unavailable|adapter"):
        AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                environment_template=template,
                harness_profile=HarnessProfile(harness_kind="not_installed", command=("agent", "acp")),
            )
        )
    with pytest.raises(ValueError, match="cannot override"):
        AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                environment_template=template, harness_profile=HarnessProfile(credential_env=("HOME",))
            )
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_pinned_byok_identity_is_stable_async() -> None:
    source = TargetRegistry.get_registry_singleton().create_named_instance(
        name="baseline-model",
        type_name="OpenAIChatTarget",
        params={
            "endpoint": "https://example.test/v1",
            "model_name": "test-deployment",
            "api_key": "synthetic-test-key",
        },
    )
    try:
        configuration = AgentTargetConfiguration(
            model_binding=ModelBinding(target_registry_name="baseline-model"),
            environment_template=EnvironmentTemplate(environment="docker", image="receipt:latest"),
        )
        target = AgentTarget(agent_configuration=configuration)
        again = AgentTarget(agent_configuration=configuration)
        # The identity pins the resolved source target, not just its registry name.
        assert again.get_identifier().hash == target.get_identifier().hash
        binding = target.get_identifier().params["configuration"]["model_binding"]
        assert binding["target_identifier_hash"] == source.get_identifier().hash
        assert binding["target_registry_name"] == "baseline-model"
    finally:
        await source.cleanup_target_async()


@pytest.mark.usefixtures("opencode_adapter")
def test_registered_adapter_configuration_has_no_provider_key_or_workspace_file() -> None:
    environment = inference_environment(
        kind="opencode",
        model="test-model",
        wire_api=InferenceWireApi.CHAT_COMPLETIONS,
        base_url="http://127.0.0.1:3456/v1",
        token="scoped-relay-test-token",
    )
    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == config["small_model"] == "pyrit/test-model"
    assert config["enabled_providers"] == ["pyrit"]
    assert config["permission"] == "ask"
    assert config["provider"]["pyrit"]["options"] == {
        "baseURL": "http://127.0.0.1:3456/v1",
        "apiKey": "{env:PYRIT_RELAY_TOKEN}",
    }
    assert "scoped-relay-test-token" not in environment["OPENCODE_CONFIG_CONTENT"]
    assert environment["PYRIT_RELAY_TOKEN"] == "scoped-relay-test-token"
    assert not any(name.startswith("COPILOT") for name in environment)


@pytest.mark.usefixtures("opencode_adapter")
@pytest.mark.parametrize("kind", [AgentHarnessKind.ACP, "opencode"])
def test_unsupported_provider_adapters_fail_before_provisioning(kind: str) -> None:
    with pytest.raises(ValueError, match="adapter|Chat Completions"):
        validate_inference_adapter(kind=kind, wire_api=InferenceWireApi.RESPONSES)


def test_adapter_reserved_names_are_declared_not_hardcoded() -> None:
    adapter = OpenCodeAdapter()
    emitted = set(adapter.launch_environment(home="/private")) | set(
        adapter.inference_environment(
            model="m", wire_api=InferenceWireApi.CHAT_COMPLETIONS, base_url="http://relay/v1", token="t"
        )
    )
    reserved = set(adapter.info.reserved_environment) | adapter.EXECUTION_RESERVED_ENVIRONMENT
    assert emitted <= reserved
    assert not any("OPENCODE" in name for name in adapter.EXECUTION_RESERVED_ENVIRONMENT)


def test_generic_launch_does_not_receive_copilot_settings() -> None:
    assert launch_environment(kind=AgentHarnessKind.ACP, home="private") == {}
    assert launch_environment(kind=AgentHarnessKind.COPILOT, home="private") == {"COPILOT_HOME": "private/.copilot"}


@pytest.mark.usefixtures("opencode_adapter")
@pytest.mark.parametrize("base", ["empty", "image"])
async def test_docker_workspace_is_not_implicitly_overlaid_async(tmp_path: Path, base: str) -> None:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(
            environment="docker", image="test", harness_kind="opencode", command=OPENCODE_COMMAND, workspace_base=base
        ),
    )
    session = DockerSession(execution=record, directory=tmp_path)
    docker = await create_container_async(session, environment=launch_environment(kind="opencode", home="/home/agent"))
    assert docker.await_count == (3 if base == "empty" else 2)
    command = docker.call_args_list[1].args
    assert "opencode" in command
    assert not any("COPILOT" in arg for arg in command)
    assert "--mount" not in command and "--volume" not in command
    if base == "empty":
        assert str(record.id) in session.agent_cwd
        assert docker.call_args.args[:3] == ("cp", "--archive", "-")
        with tarfile.open(fileobj=io.BytesIO(docker.call_args.kwargs["input_data"])) as archive:
            assert all(member.isdir() for member in archive.getmembers())
    else:
        assert session.agent_cwd == "/workspace"
