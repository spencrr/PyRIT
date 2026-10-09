# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, patch

from pyrit.agent.environment import ExecutionEnvironment
from pyrit.models.agent_execution import AgentExecution, AgentProfile


async def test_docker_passes_only_scoped_runtime_env_names(tmp_path: Path) -> None:
    execution = AgentExecution(
        owner_id="test", conversation_id="one", target_id="target", profile=AgentProfile(image="image")
    )
    environment = ExecutionEnvironment(execution=execution, directory=tmp_path)
    environment.runtime_environment = {
        "COPILOT_PROVIDER_BASE_URL": "http://host.docker.internal:1234/v1",
        "COPILOT_PROVIDER_API_KEY": "execution-credential",
    }
    with patch.object(
        environment, "_docker_async", AsyncMock(side_effect=['[{"Id":"sha256:pinned"}]', "id", ""])
    ) as docker:
        await environment.prepare_async()
    args = docker.call_args_list[1].args
    assert "COPILOT_PROVIDER_API_KEY" in args
    assert "execution-credential" not in args
    assert "OPENAI_CHAT_KEY" not in args
