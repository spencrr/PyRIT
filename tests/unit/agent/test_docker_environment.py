# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The Docker reference provider, driven through the provider contract with the Docker CLI mocked."""

import asyncio
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("psutil")

from pyrit.agent.environment import (
    EnvironmentProviderRegistry,
    ExecutionSpec,
    create_execution_session,
    validate_environment_template,
)
from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.harness import launch_environment, resolve_credential_values
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentExecutionState,
    AgentProfile,
    AgentTargetConfiguration,
    EnvironmentTemplate,
)
from pyrit.prompt_target import AgentTarget
from tests.unit.agent.example_adapters import OPENCODE_COMMAND
from tests.unit.agent.session_helpers import create_container_async, execution


def test_docker_is_the_first_party_isolated_provider() -> None:
    registry = EnvironmentProviderRegistry()
    assert [info.name for info in registry.list_providers()] == ["docker", "local"]
    assert registry.get("docker").info.capabilities.isolated
    validate_environment_template(EnvironmentTemplate(environment="docker", image="test"))
    with pytest.raises(ValueError, match="environment_settings"):
        validate_environment_template(
            EnvironmentTemplate(environment="docker", image="test", environment_settings={"pool": "blue"})
        )
    with pytest.raises(ValueError, match="image is required"):
        validate_environment_template(EnvironmentTemplate(environment="docker"))


@pytest.mark.parametrize(
    "settings",
    [{"image": "test"}, {"workspace_base": "image"}, {"docker_network": "none"}, {"docker_cpus": 2}],
)
def test_local_provider_refuses_docker_settings(settings: dict[str, object]) -> None:
    template = EnvironmentTemplate(environment="local", local_execution_acknowledged=True, **settings)
    with pytest.raises(ValueError, match="Docker environment"):
        validate_environment_template(template)


def test_docker_sessions_are_created_through_the_record_provider(tmp_path: Path) -> None:
    session = create_execution_session(
        execution=execution(AgentProfile(environment="docker", image="test")), directory=tmp_path
    )
    assert isinstance(session, DockerSession) and session.harness_home == "/home/agent"


@pytest.mark.usefixtures("patch_central_database")
def test_docker_identity_tracks_image_and_workspace() -> None:
    def target(**template: object) -> AgentTarget:
        return AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                environment_template=EnvironmentTemplate(environment="docker", image="receipt:latest", **template)
            )
        )

    assert target().get_identifier().hash == target().get_identifier().hash
    assert target(docker_network="none").get_identifier().hash != target().get_identifier().hash
    assert target(workspace_base="empty").get_identifier().hash != target().get_identifier().hash
    assert "docker" in str(target().get_identifier().params)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8021/v1", "http://localhost:8021/v1", "http://[::1]:8021/v1"])
async def test_docker_rejects_loopback_relay(tmp_path: Path, url: str) -> None:
    session = DockerSession(execution=execution(AgentProfile(environment="docker", image="test")), directory=tmp_path)
    with pytest.raises(ValueError, match="reachable"):
        await session.inference_endpoint_async(relay_url=url)
    assert await session.inference_endpoint_async(relay_url="http://host.docker.internal:8021/v1") == (
        "http://host.docker.internal:8021/v1"
    )


async def test_docker_creation_pins_image_and_does_not_mount_host(tmp_path: Path) -> None:
    record = execution(AgentProfile(environment="docker", image="receipt:latest"))
    session = DockerSession(execution=record, directory=tmp_path)
    docker = await create_container_async(session)
    command = docker.call_args_list[1].args
    assert "sha256:pinned" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--mount" not in command
    assert "--volume" not in command
    assert record.image_id == "sha256:pinned"
    assert record.provider_id == f"pyrit-agent-{record.id}"
    assert record.provenance["image"] == "receipt:latest"


async def test_docker_reserve_is_deterministic_before_any_resource(tmp_path: Path) -> None:
    record = execution(AgentProfile(environment="docker", image="receipt:latest"))
    session = DockerSession(execution=record, directory=tmp_path)
    session.reserve()
    assert record.provider_id == f"pyrit-agent-{record.id}"


async def test_credential_reference_reaches_docker_as_runtime_environment_not_argv_or_record_async(
    tmp_path: Path,
) -> None:
    profile = AgentProfile(environment="docker", image="test", credential_env=("COPILOT_GITHUB_TOKEN",))
    record = AgentExecution(owner_id="test", conversation_id="one", target_id="target", profile=profile)
    session = DockerSession(execution=record, directory=tmp_path)
    with patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "gho_synthetic"}):
        credentials = resolve_credential_values(credential_env=profile.credential_env)
    docker = AsyncMock(side_effect=['[{"Id":"test"}]', "id"])
    spawn = AsyncMock()
    with (
        patch.dict("os.environ", {"DOCKER_HOST": "ssh://builder", "DOCKER_CONTEXT": "remote"}),
        patch.object(session, "_docker_async", docker),
        patch.object(session, "_spawn_async", spawn),
    ):
        await session.launch_async(ExecutionSpec(command=profile.command, environment={}, credentials=credentials))
    create = docker.call_args_list[1]
    assert "COPILOT_GITHUB_TOKEN" in create.args
    assert "gho_synthetic" not in str(create.args)
    assert create.kwargs["environment"]["COPILOT_GITHUB_TOKEN"] == "gho_synthetic"
    assert "gho_synthetic" not in record.model_dump_json()
    # `docker start --attach` is the CLI talking to the daemon: it inherits the daemon/context settings,
    # while the credential itself travelled only through `docker create`.
    attach_environment = spawn.call_args.kwargs["environment"]
    assert attach_environment["DOCKER_HOST"] == "ssh://builder" and attach_environment["DOCKER_CONTEXT"] == "remote"


def account_archive(text: str) -> bytes:
    data = text.encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("accounts")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("user", "uid", "gid", "reads"),
    [
        ("", 0, 0, 0),
        ("1001:1002", 1001, 1002, 0),
        ("1001", 1001, 42, 1),
        ("runner", 1001, 42, 1),
        ("runner:staff", 1001, 43, 2),
        ("1001:staff", 1001, 43, 2),
    ],
)
async def test_image_user_resolution_async(tmp_path: Path, user: str, uid: int, gid: int, reads: int) -> None:
    session = DockerSession(execution=execution(AgentProfile(environment="docker", image="test")), directory=tmp_path)
    with patch.object(
        session,
        "_docker_archive_async",
        AsyncMock(
            side_effect=[
                account_archive("runner:x:1001:42:Runner:/home/runner:/bin/sh\n"),
                account_archive("staff:x:43:\n"),
            ]
        ),
    ) as read:
        assert await session._docker_user_async(user) == (uid, gid)
    assert read.await_count == reads


async def test_unknown_image_user_fails_before_launch_async(tmp_path: Path) -> None:
    session = DockerSession(execution=execution(AgentProfile(environment="docker", image="test")), directory=tmp_path)
    with (
        patch.object(session, "_docker_archive_async", AsyncMock(return_value=account_archive(""))),
        pytest.raises(ValueError, match="not found"),
    ):
        await session._docker_user_async("missing")


@pytest.mark.parametrize("base", ["empty", "image"])
async def test_workspace_and_overlay_are_owned_by_image_user_async(tmp_path: Path, base: str) -> None:
    record = execution(
        AgentProfile(
            environment="docker",
            image="test",
            workspace_base=base,
            working_directory="sub/work",
            fixture_directory=str(Path(__file__).resolve().parents[3] / "assets" / "agent_receipt"),
        )
    )
    session = DockerSession(execution=record, directory=tmp_path)
    docker = await create_container_async(
        session,
        docker_responses=[json.dumps([{"Id": "sha256:test", "Config": {"User": "1001:1002"}}]), "id", ""],
    )
    with tarfile.open(fileobj=io.BytesIO(docker.call_args.kwargs["input_data"])) as archive:
        entries = archive.getmembers()
        assert all((entry.uid, entry.gid) == (1001, 1002) for entry in entries)
        names = {entry.name for entry in entries}
        root = session.container_workspace.lstrip("/")
        assert f"{root}/orders.json" in names
        assert f"{root}/sub/work" in names and f"{root}/sub" in names
        assert "home/agent" in names
        assert (root in names) == (base == "empty")
    command = docker.call_args_list[1].args
    assert "--user" not in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command


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


async def test_archive_preparation_budget_is_enforced_async(tmp_path: Path) -> None:
    session = DockerSession(
        execution=execution(AgentProfile(environment="docker", image="test", workspace_base="empty")),
        directory=tmp_path,
    )
    await asyncio.to_thread(session.workspace.mkdir)
    with patch.object(DockerSession, "MAX_WORKSPACE_ARCHIVE_BYTES", 1024), pytest.raises(ValueError, match="budget"):
        await asyncio.to_thread(session._workspace_archive, uid=1001, gid=1002)


async def test_interrupted_container_is_released_without_its_harness_adapter_async(tmp_path: Path) -> None:
    record = AgentExecution(
        owner_id="previous",
        conversation_id="conversation",
        target_id="target",
        profile=AgentProfile(environment="docker", image="test", harness_kind="team_v1", command=("team-agent",)),
        state=AgentExecutionState.IDLE,
        provider_id="owned-container",
    )
    manager = AgentExecutionManager(root=tmp_path)
    await manager.store.save_async(record)
    info = {
        "Config": {"Labels": {"pyrit.execution": str(record.id), "pyrit.owner": record.owner_id}},
        "State": {"Running": True},
    }
    with patch.object(
        DockerSession,
        "_docker_async",
        new_callable=AsyncMock,
        side_effect=["container-id", json.dumps([info]), "", ""],
    ) as docker:
        await manager.start_async()
        try:
            assert manager.records[record.id].state == AgentExecutionState.CLOSED
            assert [call.args[:1] for call in docker.call_args_list] == [
                ("container",),
                ("inspect",),
                ("stop",),
                ("rm",),
            ]
            assert all(not call.kwargs.get("credentials") for call in docker.call_args_list)
        finally:
            await manager.close_async()
