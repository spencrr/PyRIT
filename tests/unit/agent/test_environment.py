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

from pyrit.agent.environment import (
    CheckpointRestore,
    EnvironmentProviderRegistry,
    ExecutionSpec,
    create_execution_session,
    validate_environment_template,
)
from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.environments.local import LocalSession
from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_execution import AgentProfile, EnvironmentTemplate, HarnessProfile
from tests.unit.agent.session_helpers import create_container_async, execution, session_directory, spec_for


async def test_recreated_profile_rejects_changed_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "orders.json").write_text('{"total": 31}')
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        fixture_directory=str(fixture),
        command=("python", "-c", "import sys; sys.stdin.read()"),
    )
    first = execution(profile)
    session = LocalSession(execution=first, directory=session_directory(tmp_path, "first"))
    await session.launch_async(spec_for(profile))
    await session.close_async()
    (fixture / "orders.json").write_text('{"total": 36}')
    second = execution(
        profile.model_copy(update={"expected_fixture_sha256": first.fixture_sha256}), conversation_id="two"
    )
    recreated = LocalSession(execution=second, directory=session_directory(tmp_path, "second"))
    with pytest.raises(ValueError, match="fixture changed"):
        await recreated.launch_async(spec_for(profile))
    await recreated.close_async()
    assert not (recreated.workspace).exists()


async def test_local_launch_uses_private_home_and_exposes_streams(tmp_path: Path) -> None:
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        command=(
            "python",
            "-c",
            "import os,sys; print(os.environ['HOME'], os.environ['TOKEN']); sys.stderr.write('warned')",
        ),
    )
    record = execution(profile)
    session = LocalSession(execution=record, directory=session_directory(tmp_path))
    with pytest.raises(RuntimeError, match="not been launched"):
        _ = session.acp_reader
    await session.launch_async(ExecutionSpec(command=profile.command, environment={}, credentials={"TOKEN": "secret"}))
    line = await asyncio.wait_for(session.acp_reader.readline(), timeout=10)
    assert line.decode().split() == [str(session.home), "secret"]
    assert (await asyncio.wait_for(session.stderr.read(), timeout=10)) == b"warned"
    assert record.process_id is not None and record.process_created_at is not None
    await session.close_async()


async def test_local_refuses_checkpoint_restore(tmp_path: Path) -> None:
    profile = AgentProfile(environment="local", local_execution_acknowledged=True)
    session = LocalSession(execution=execution(profile), directory=session_directory(tmp_path))
    layout = HarnessCheckpointLayout(version="v", agent_versions=("1",), state_root="/home/agent/.state")
    restore = CheckpointRestore(layout=layout, workspace=b"", state=b"")
    with pytest.raises(ValueError, match="no checkpoint capability"):
        await session.launch_async(
            ExecutionSpec(command=profile.command, environment={}, credentials={}, restore=restore)
        )
    with pytest.raises(NotImplementedError, match="no checkpoint capability"):
        await session.capture_checkpoint_async(layout=layout, secrets=())


async def test_docker_creation_pins_image_and_does_not_mount_host(tmp_path: Path) -> None:
    record = execution(AgentProfile(image="receipt:latest"))
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
    record = execution(AgentProfile(image="receipt:latest"))
    session = DockerSession(execution=record, directory=tmp_path)
    session.reserve()
    assert record.provider_id == f"pyrit-agent-{record.id}"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8021/v1", "http://localhost:8021/v1", "http://[::1]:8021/v1"])
async def test_docker_rejects_loopback_relay(tmp_path: Path, url: str) -> None:
    session = DockerSession(execution=execution(AgentProfile(image="test")), directory=tmp_path)
    with pytest.raises(ValueError, match="reachable"):
        await session.inference_endpoint_async(relay_url=url)
    assert await session.inference_endpoint_async(relay_url="http://host.docker.internal:8021/v1") == (
        "http://host.docker.internal:8021/v1"
    )


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
    session = DockerSession(execution=execution(AgentProfile(image="test")), directory=tmp_path)
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
    session = DockerSession(execution=execution(AgentProfile(image="test")), directory=tmp_path)
    with (
        patch.object(session, "_docker_archive_async", AsyncMock(return_value=account_archive(""))),
        pytest.raises(ValueError, match="not found"),
    ):
        await session._docker_user_async("missing")


@pytest.mark.parametrize("base", ["empty", "image"])
async def test_workspace_and_overlay_are_owned_by_image_user_async(tmp_path: Path, base: str) -> None:
    record = execution(
        AgentProfile(
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


async def test_archive_preparation_budget_is_enforced_async(tmp_path: Path) -> None:
    session = DockerSession(execution=execution(AgentProfile(image="test", workspace_base="empty")), directory=tmp_path)
    await asyncio.to_thread(session.workspace.mkdir)
    with patch.object(DockerSession, "MAX_WORKSPACE_ARCHIVE_BYTES", 1024), pytest.raises(ValueError, match="budget"):
        await asyncio.to_thread(session._workspace_archive, uid=1001, gid=1002)


def test_registry_lists_first_party_providers_and_rejects_unknown_names() -> None:
    registry = EnvironmentProviderRegistry()
    names = [info.name for info in registry.list_providers()]
    assert names == ["docker", "local"]
    docker, local = registry.get("docker").info, registry.get("local").info
    assert docker.capabilities.isolated and docker.capabilities.checkpoint
    assert not local.capabilities.isolated and not local.capabilities.checkpoint
    with pytest.raises(ValueError, match="unavailable"):
        registry.get("aca_sandbox")
    with pytest.raises(TypeError, match="ExecutionEnvironmentProvider"):
        registry.register(object())  # type: ignore[arg-type]


def test_template_validation_follows_provider_capabilities() -> None:
    validate_environment_template(EnvironmentTemplate(image="test"))
    validate_environment_template(EnvironmentTemplate(environment="local"), require_consent=False)
    with pytest.raises(ValueError, match="NOT sandboxed"):
        validate_environment_template(EnvironmentTemplate(environment="local"))
    with pytest.raises(ValueError, match="unavailable"):
        validate_environment_template(EnvironmentTemplate(environment="aca_sandbox", image="x"))


@pytest.mark.parametrize(
    "template",
    [
        EnvironmentTemplate(image="test", environment_settings={"pool": "blue"}),
        EnvironmentTemplate(environment="local", local_execution_acknowledged=True, environment_settings={"x": 1}),
    ],
)
def test_first_party_providers_reject_settings_they_would_ignore(template: EnvironmentTemplate) -> None:
    with pytest.raises(ValueError, match="environment_settings"):
        validate_environment_template(template)


async def test_local_close_is_effect_idempotent(tmp_path: Path) -> None:
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        artifact_paths=("missing.txt",),
        command=("python", "-c", "import sys; sys.stdin.read()"),
    )
    record = execution(profile)
    session = LocalSession(execution=record, directory=session_directory(tmp_path))
    await session.launch_async(spec_for(profile))
    await session.close_async()
    assert len(record.artifact_errors) == 1
    snapshot = record.model_dump(mode="json")
    await session.close_async()
    assert record.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("model", [AgentProfile, HarnessProfile])
def test_retired_profile_fields_still_decode(model: type[AgentProfile] | type[HarnessProfile]) -> None:
    extra = {"image": "test"} if model is AgentProfile else {}
    decoded = model.model_validate({**extra, "use_github_cli_login": True})
    assert not hasattr(decoded, "use_github_cli_login")
    assert decoded.harness_kind == "copilot"


def test_sessions_are_created_through_the_record_provider(tmp_path: Path) -> None:
    docker = create_execution_session(execution=execution(AgentProfile(image="test")), directory=tmp_path)
    local = create_execution_session(
        execution=execution(AgentProfile(environment="local", local_execution_acknowledged=True)), directory=tmp_path
    )
    assert isinstance(docker, DockerSession) and docker.harness_home == "/home/agent"
    assert isinstance(local, LocalSession) and local.harness_home == str(tmp_path / "home")
