# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.agent.harness import launch_environment
from pyrit.models.agent_execution import (
    AgentHarnessKind,
    AgentProfile,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.prompt_target import AgentTarget
from tests.unit.agent.example_adapters import OPENCODE_COMMAND, OpenCodeAdapter


@pytest.mark.parametrize("model", [AgentProfile, HarnessProfile])
def test_explicit_harness_and_legacy_decode(model: type[AgentProfile] | type[HarnessProfile]) -> None:
    extra = {"image": "test"} if model is AgentProfile else {}
    assert model(**extra, command=OPENCODE_COMMAND).harness_kind == AgentHarnessKind.ACP
    explicit = model(**extra, harness_kind="opencode", command=OPENCODE_COMMAND)
    assert explicit.command == OPENCODE_COMMAND
    assert explicit.harness_kind == "opencode"
    assert model(**extra).harness_kind == AgentHarnessKind.COPILOT
    for kind in ("acp", "opencode"):
        with pytest.raises(ValueError, match="explicit command"):
            model(**extra, harness_kind=kind)


@pytest.mark.parametrize("directory", ["/host", "../escape", "C:\\host", "foo\\bar"])
def test_working_directory_is_workspace_relative(directory: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        EnvironmentTemplate(image="test", working_directory=directory)


def test_profile_roundtrip_keeps_new_fields_and_groups_disjoint() -> None:
    configuration = AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="team_v1", command=("renamed-agent", "acp")),
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
    ],
)
def test_pre_adapter_identity_is_stable(profile: dict, expected: str) -> None:
    target = AgentTarget(
        agent_configuration=AgentTargetConfiguration.from_profile(AgentProfile.model_validate(profile))
    )
    assert target.get_identifier().hash == expected
    original_base = "empty" if target.profile.environment == "local" else "image"
    explicit = AgentTarget(
        agent_configuration=AgentTargetConfiguration.from_profile(
            AgentProfile.model_validate({**profile, "workspace_base": original_base})
        )
    )
    assert explicit.get_identifier().hash == expected


def test_adapter_reserved_names_are_declared_not_hardcoded() -> None:
    adapter = OpenCodeAdapter()
    emitted = set(adapter.launch_environment(home="/private"))
    reserved = set(adapter.info.reserved_environment) | adapter.EXECUTION_RESERVED_ENVIRONMENT
    assert emitted <= reserved
    assert not any("OPENCODE" in name for name in adapter.EXECUTION_RESERVED_ENVIRONMENT)


def test_generic_launch_does_not_receive_copilot_settings() -> None:
    assert launch_environment(kind=AgentHarnessKind.ACP, home="private") == {}
    assert launch_environment(kind=AgentHarnessKind.COPILOT, home="private") == {"COPILOT_HOME": "private/.copilot"}
