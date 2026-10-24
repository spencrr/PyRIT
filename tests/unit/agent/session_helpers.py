# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Helpers for driving execution sessions through the provider contract without real resources."""

from pathlib import Path

from pyrit.agent.environment import ExecutionSpec
from pyrit.models.agent_execution import AgentExecution, AgentProfile


def execution(profile: AgentProfile, *, conversation_id: str = "one") -> AgentExecution:
    return AgentExecution(owner_id="test", conversation_id=conversation_id, target_id="target", profile=profile)


def spec_for(profile: AgentProfile, *, environment: dict[str, str] | None = None) -> ExecutionSpec:
    return ExecutionSpec(command=profile.command, environment=environment or {}, credentials={})


def session_directory(tmp_path: Path, name: str = "session") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory
