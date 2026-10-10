# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import io
import json
import tarfile
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
    record = AgentExecution(
        owner_id="test", conversation_id="one", target_id="target", profile=AgentProfile(image="test")
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with patch.object(
        environment,
        "_docker_archive_async",
        AsyncMock(
            side_effect=[
                account_archive("runner:x:1001:42:Runner:/home/runner:/bin/sh\n"),
                account_archive("staff:x:43:\n"),
            ]
        ),
    ) as read:
        assert await environment._docker_user_async(user) == (uid, gid)
    assert read.await_count == reads


async def test_unknown_image_user_fails_before_launch_async(tmp_path: Path) -> None:
    record = AgentExecution(
        owner_id="test", conversation_id="one", target_id="target", profile=AgentProfile(image="test")
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with (
        patch.object(environment, "_docker_archive_async", AsyncMock(return_value=account_archive(""))),
        pytest.raises(ValueError, match="not found"),
    ):
        await environment._docker_user_async("missing")


@pytest.mark.parametrize("base", ["empty", "image"])
async def test_workspace_and_overlay_are_owned_by_image_user_async(tmp_path: Path, base: str) -> None:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(
            image="test",
            workspace_base=base,
            working_directory="sub/work",
            fixture_directory=str(Path(__file__).resolve().parents[3] / "assets" / "agent_receipt"),
        ),
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    with patch.object(
        environment,
        "_docker_async",
        AsyncMock(
            side_effect=[
                json.dumps([{"Id": "sha256:test", "Config": {"User": "1001:1002"}}]),
                "id",
                "",
            ]
        ),
    ) as docker:
        await environment.prepare_async()
    with tarfile.open(fileobj=io.BytesIO(docker.call_args.kwargs["input_data"])) as archive:
        entries = archive.getmembers()
        assert all((entry.uid, entry.gid) == (1001, 1002) for entry in entries)
        names = {entry.name for entry in entries}
        root = environment.container_workspace.lstrip("/")
        assert f"{root}/orders.json" in names
        assert f"{root}/sub/work" in names and f"{root}/sub" in names
        assert "home/agent" in names
        assert (root in names) == (base == "empty")
    command = docker.call_args_list[1].args
    assert "--user" not in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command


async def test_archive_preparation_budget_is_enforced_async(tmp_path: Path) -> None:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(image="test", workspace_base="empty"),
    )
    environment = ExecutionEnvironment(execution=record, directory=tmp_path)
    await asyncio.to_thread(environment.workspace.mkdir)
    with patch.object(environment, "_MAX_WORKSPACE_ARCHIVE_BYTES", 1024), pytest.raises(ValueError, match="budget"):
        await asyncio.to_thread(environment._workspace_archive, uid=1001, gid=1002)
