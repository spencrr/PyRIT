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


def test_adapter_reserved_names_are_declared_not_hardcoded() -> None:
    adapter = OpenCodeAdapter()
    emitted = set(adapter.launch_environment(home="/private"))
    reserved = set(adapter.info.reserved_environment) | adapter.EXECUTION_RESERVED_ENVIRONMENT
    assert emitted <= reserved
    assert not any("OPENCODE" in name for name in adapter.EXECUTION_RESERVED_ENVIRONMENT)


def test_generic_launch_does_not_receive_copilot_settings() -> None:
    assert launch_environment(kind=AgentHarnessKind.ACP, home="private") == {}
    assert launch_environment(kind=AgentHarnessKind.COPILOT, home="private") == {"COPILOT_HOME": "private/.copilot"}
