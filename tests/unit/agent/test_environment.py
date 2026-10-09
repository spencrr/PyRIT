# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("psutil")

from pyrit.agent.environment import ExecutionEnvironment
from pyrit.models.agent_execution import AgentExecution, AgentProfile


async def test_recreated_profile_rejects_changed_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "orders.json").write_text('{"total": 31}')
    profile = AgentProfile(environment="local", local_execution_acknowledged=True, fixture_directory=str(fixture))
    first = AgentExecution(owner_id="test", conversation_id="one", target_id="target", profile=profile)
    first_directory = tmp_path / "first"
    first_directory.mkdir()
    environment = ExecutionEnvironment(execution=first, directory=first_directory)
    await environment.prepare_async()
    await environment.close_async()
    (fixture / "orders.json").write_text('{"total": 36}')
    second = AgentExecution(
        owner_id="test",
        conversation_id="two",
        target_id="target",
        profile=profile.model_copy(update={"expected_fixture_sha256": first.fixture_sha256}),
    )
    second_directory = tmp_path / "second"
    second_directory.mkdir()
    recreated = ExecutionEnvironment(execution=second, directory=second_directory)
    with pytest.raises(ValueError, match="fixture changed"):
        await recreated.prepare_async()
    await recreated.close_async()


async def test_docker_creation_pins_image_and_does_not_mount_host(tmp_path: Path) -> None:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(image="receipt:latest"),
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with patch.object(
        environment, "_docker_async", AsyncMock(side_effect=['[{"Id":"sha256:pinned"}]', "id", ""])
    ) as docker:
        await environment.prepare_async()
    command = docker.call_args_list[1].args
    assert "sha256:pinned" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--mount" not in command
    assert "--volume" not in command
    assert record.image_id == "sha256:pinned"
