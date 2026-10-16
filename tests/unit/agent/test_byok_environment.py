# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from pyrit.agent.environments.docker import DockerSession
from pyrit.models.agent_execution import AgentProfile
from tests.unit.agent.session_helpers import create_container_async, execution


async def test_docker_passes_only_scoped_runtime_env_names(tmp_path: Path) -> None:
    session = DockerSession(execution=execution(AgentProfile(image="image")), directory=tmp_path)
    docker = await create_container_async(
        session,
        environment={
            "COPILOT_PROVIDER_BASE_URL": "http://host.docker.internal:1234/v1",
            "COPILOT_PROVIDER_API_KEY": "execution-credential",
        },
    )
    create = docker.call_args_list[1]
    assert "COPILOT_PROVIDER_API_KEY" in create.args
    assert "execution-credential" not in create.args
    assert "OPENAI_CHAT_KEY" not in create.args
    assert create.kwargs["environment"]["COPILOT_PROVIDER_API_KEY"] == "execution-credential"
