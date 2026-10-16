# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Helpers for driving execution sessions through the provider contract without real resources."""

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pyrit.agent.environment import ExecutionSpec
from pyrit.agent.environments.docker import DockerSession
from pyrit.models.agent_execution import AgentExecution, AgentProfile

DOCKER_CREATE_RESPONSES = ['[{"Id":"sha256:pinned"}]', "id", ""]


def execution(profile: AgentProfile, *, conversation_id: str = "one") -> AgentExecution:
    return AgentExecution(owner_id="test", conversation_id=conversation_id, target_id="target", profile=profile)


def spec_for(profile: AgentProfile, *, environment: dict[str, str] | None = None) -> ExecutionSpec:
    return ExecutionSpec(command=profile.command, environment=environment or {}, credentials={})


async def create_container_async(
    session: DockerSession,
    *,
    docker_responses: Sequence[str] = DOCKER_CREATE_RESPONSES,
    environment: dict[str, str] | None = None,
) -> AsyncMock:
    """Run the Docker launch path with the CLI and attach mocked; return the docker CLI mock."""
    docker = AsyncMock(side_effect=list(docker_responses))
    with (
        patch.object(session, "_docker_async", docker),
        patch.object(session, "_spawn_async", AsyncMock()),
    ):
        await session.launch_async(spec_for(session.execution.profile, environment=environment))
    return docker


def session_directory(tmp_path: Path, name: str = "session") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory
