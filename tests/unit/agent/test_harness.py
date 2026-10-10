# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.agent.environment import ExecutionEnvironment
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


@pytest.mark.parametrize("model", [AgentProfile, HarnessProfile])
def test_explicit_harness_and_legacy_decode(model: type[AgentProfile] | type[HarnessProfile]) -> None:
    extra = {"image": "test"} if model is AgentProfile else {}
    assert model(**extra, command=("opencode", "acp")).harness_kind == AgentHarnessKind.ACP
    explicit = model(**extra, harness_kind="opencode")
    assert explicit.command == ("opencode", "acp")
    assert explicit.harness_kind == AgentHarnessKind.OPENCODE
    assert model(**extra).harness_kind == AgentHarnessKind.COPILOT
    with pytest.raises(ValueError, match="explicit command"):
        model(**extra, harness_kind="acp")


@pytest.mark.parametrize("directory", ["/host", "../escape", "C:\\host", "foo\\bar"])
def test_working_directory_is_workspace_relative(directory: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        EnvironmentTemplate(image="test", working_directory=directory)


def test_profile_roundtrip_keeps_new_fields_and_groups_disjoint() -> None:
    configuration = AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="opencode", command=("renamed-opencode", "acp")),
        environment_template=EnvironmentTemplate(image="test", workspace_base="empty", working_directory="repo"),
    )
    assert AgentTargetConfiguration.from_profile(configuration.to_profile()) == configuration
    assert not (ModelBinding.model_fields.keys() & HarnessProfile.model_fields.keys())
    assert not (EnvironmentTemplate.model_fields.keys() & HarnessProfile.model_fields.keys())


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (
            {"environment": "local", "local_execution_acknowledged": True, "command": ("python", "scripted-agent.py")},
            "50113ed1071b8607e4e8e382ba28f40577ca7a13d4502782f0d7e06263f3ac08",
        ),
        ({"image": "receipt:latest"}, "5c877a0e2dd1e8024001219e60675a3cbd23b879f089e34616f30df68ddcf508"),
    ],
)
def test_pre_adapter_identity_is_stable(profile: dict, expected: str) -> None:
    target = AgentTarget(profile=profile)
    assert target.get_identifier().hash == expected
    original_base = "empty" if target.profile.environment == "local" else "image"
    explicit = AgentTarget(profile={**profile, "workspace_base": original_base})
    assert explicit.get_identifier().hash == expected


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
        target = AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(target_registry_name="baseline-model"),
                environment_template=EnvironmentTemplate(image="receipt:latest"),
            )
        )
        assert target.get_identifier().hash == "ca31715c4b76b7cc1038a7ecb929f4628264221bce1dd9cb409f7107992729c2"
    finally:
        await source.cleanup_target_async()


def test_opencode_provider_configuration_has_no_provider_key_or_workspace_file() -> None:
    environment = inference_environment(
        kind=AgentHarnessKind.OPENCODE,
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


@pytest.mark.parametrize("kind", [AgentHarnessKind.ACP, AgentHarnessKind.OPENCODE])
def test_unsupported_provider_adapters_fail_before_provisioning(kind: AgentHarnessKind) -> None:
    with pytest.raises(ValueError, match="adapter|Chat Completions"):
        validate_inference_adapter(kind=kind, wire_api=InferenceWireApi.RESPONSES)


def test_generic_launch_does_not_receive_copilot_settings() -> None:
    assert launch_environment(kind=AgentHarnessKind.ACP, home="private") == {}
    assert launch_environment(kind=AgentHarnessKind.COPILOT, home="private") == {"COPILOT_HOME": "private/.copilot"}


@pytest.mark.parametrize("base", ["empty", "image"])
async def test_docker_workspace_is_not_implicitly_overlaid_async(tmp_path: Path, base: str) -> None:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(image="test", harness_kind="opencode", workspace_base=base),
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with patch.object(
        environment, "_docker_async", AsyncMock(side_effect=['[{"Id":"sha256:pinned"}]', "id", ""])
    ) as docker:
        await environment.prepare_async()
    assert docker.await_count == (3 if base == "empty" else 2)
    command = docker.call_args_list[1].args
    assert "opencode" in command
    assert not any("COPILOT" in arg for arg in command)
    assert "--mount" not in command and "--volume" not in command
    if base == "empty":
        assert str(record.id) in environment.agent_cwd
        assert docker.call_args.args[:3] == ("cp", "--archive", "-")
        with tarfile.open(fileobj=io.BytesIO(docker.call_args.kwargs["input_data"])) as archive:
            assert all(member.isdir() for member in archive.getmembers())
    else:
        assert environment.agent_cwd == "/workspace"
