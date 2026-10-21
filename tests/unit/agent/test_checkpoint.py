# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import io
import tarfile
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from typing import IO, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from pyrit.agent.checkpoint import checkpoint_transcript_async, require_checkpoint_ready
from pyrit.agent.checkpoint_archive import CheckpointArchive
from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.execution_manager import AgentExecutionManager, _LiveExecution
from pyrit.agent.model_binding import ResolvedModelBinding
from pyrit.memory import SQLiteMemory
from pyrit.models import MessagePiece
from pyrit.models.agent_checkpoint import AgentCheckpoint
from pyrit.models.agent_execution import (
    AgentConnectionState,
    AgentEnvironment,
    AgentExecution,
    AgentExecutionState,
    AgentProfile,
    AgentTargetConfiguration,
    AgentTurn,
    AgentTurnStatus,
    AgentWorkspaceBase,
)
from pyrit.models.model_inference import InferenceRequirements
from pyrit.prompt_target import TextTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")

STATE_ROOT = "home/agent/.copilot/session-state"


def archive(*, root: str, data: bytes = b"checkpoint", kind: bytes = tarfile.REGTYPE) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as target:
        info = tarfile.TarInfo(root + "/data")
        info.size = len(data) if kind == tarfile.REGTYPE else 0
        info.type, info.uid, info.gid = kind, 1000, 1000
        target.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


@pytest.fixture
async def prepared_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> AsyncGenerator[tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding], None]:
    source = TextTarget()
    binding = ResolvedModelBinding(
        target=source,
        identifier_hash=source.get_identifier().hash,
        model="model",
        requirements=InferenceRequirements(),
    )
    async with AgentExecutionManager(root=tmp_path) as manager:
        request = MessagePiece(role="user", original_value="one", conversation_id=str(uuid4()))
        await sqlite_instance.add_message_to_memory_async(request=request.to_message())
        record = AgentExecution(
            owner_id="test",
            conversation_id=request.conversation_id,
            target_id="target",
            profile=AgentProfile(environment="docker", image="fixture", target_registry_name="source", model="model"),
            image_id="sha256:pinned",
            session_id="native",
            inference_target_hash=binding.identifier_hash,
            state=AgentExecutionState.IDLE,
            connection_state=AgentConnectionState.READY,
            turns=[
                AgentTurn(
                    request_id=str(request.id), prompt="one", status=AgentTurnStatus.COMPLETED, capture_complete=True
                )
            ],
            agent_info={"initialize": {"agentInfo": {"version": "1.0.93"}, "agentCapabilities": {"loadSession": True}}},
        )
        manager.records[record.id] = record
        manager._live[record.id] = manager._make_live(record)
        await manager.store.save_async(record)
        await manager._record_async(manager._live[record.id], direction="lifecycle", payload={"type": "turn_finished"})
        workspace = archive(root="workspace")
        state = archive(root=STATE_ROOT)
        with (
            patch.object(
                DockerSession,
                "capture_checkpoint_async",
                new_callable=AsyncMock,
                return_value=(workspace, state),
            ),
            patch.object(DockerSession, "close_async", new_callable=AsyncMock),
        ):
            yield manager, record, binding


async def save_async(
    *, manager: AgentExecutionManager, record: AgentExecution, memory: SQLiteMemory
) -> AgentCheckpoint:
    return await manager.save_checkpoint_async(execution_id=record.id, memory=memory, actor="test-operator")


async def test_checkpoint_survives_manager_restart_and_binds_exact_source_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    checkpoint = await save_async(manager=manager, record=record, memory=sqlite_instance)
    assert record.state == AgentExecutionState.CLOSED and not manager.has_live_executions
    assert checkpoint.source_execution_id == record.id
    assert checkpoint.transcript_sha256 == await checkpoint_transcript_async(
        memory=sqlite_instance,
        conversation_id=record.conversation_id,
    )
    await manager.close_async()
    async with AgentExecutionManager(root=manager.store.root) as restarted:
        loaded, workspace, state = await restarted.checkpoints.load_async(checkpoint.id)
        assert loaded == checkpoint
        assert restarted.checkpoints.describe(workspace) == checkpoint.workspace
        assert restarted.checkpoints.describe(state) == checkpoint.harness_state
        assert (await restarted.checkpoint_status_async(record.id)).restore_available
        await restarted.delete_checkpoint_async(checkpoint.id)
        assert not (await restarted.checkpoint_status_async(record.id)).restore_available
        assert await restarted.store.events_async(execution_id=record.id)


async def test_restore_creates_lineage_without_copying_old_turns_or_replaying_prompt_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    from pyrit.agent.acp_connection import AcpConnection

    manager, record, binding = prepared_async
    checkpoint = await save_async(manager=manager, record=record, memory=sqlite_instance)
    original = record.model_dump_json()
    connection = MagicMock(spec=AcpConnection)
    connection.prompt_async = AsyncMock(return_value=("end_turn", "new result"))
    connection.cancel_async = AsyncMock()
    connection.close_async = AsyncMock()

    async def launch_async(live: _LiveExecution) -> None:
        assert live.restoration[0] == checkpoint
        live.connection = connection
        live.record.session_id = checkpoint.session_id
        live.record.agent_info = record.agent_info
        live.record.connection_state = AgentConnectionState.READY
        await manager._record_async(
            live,
            direction="restore",
            payload={
                "method": "session/update",
                "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "old"}},
            },
        )

    with patch.object(manager, "_launch_async", side_effect=launch_async):
        restored = await manager.restore_checkpoint_async(
            checkpoint_id=checkpoint.id,
            profile=record.profile,
            target_id=record.target_id,
            memory=sqlite_instance,
            model_binding=binding,
        )
    connection.prompt_async.assert_not_awaited()
    assert restored.id != record.id and restored.parent_execution_id == record.id
    assert restored.restored_from_checkpoint_id == checkpoint.id
    assert restored.turns == [] and restored.approvals == []
    assert restored.state == AgentExecutionState.IDLE
    restored.created_at = record.created_at - timedelta(seconds=1)
    assert manager.latest_execution(conversation_id=record.conversation_id) is restored
    sent, turn = await manager.send_async(
        configuration=AgentTargetConfiguration.from_profile(record.profile),
        target_id=record.target_id,
        conversation_id=record.conversation_id,
        request_id="next-request",
        prompt="only this prompt",
        has_history=True,
        model_binding=binding,
    )
    assert sent.id == restored.id
    connection.prompt_async.assert_awaited_once_with("only this prompt")
    assert record.model_dump_json() == original
    assert (await manager.tool_calls_async(execution_id=restored.id, turn_id=turn.id)).messages == []
    with pytest.raises(ValueError, match="latest saved"):
        await manager.restore_checkpoint_async(
            checkpoint_id=checkpoint.id,
            profile=record.profile,
            target_id=record.target_id,
            memory=sqlite_instance,
            model_binding=binding,
        )


@pytest.mark.parametrize("changed", ["transcript", "profile", "model", "journal", "archive"])
async def test_stale_or_corrupt_checkpoint_never_allocates_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
    changed: str,
) -> None:
    manager, record, binding = prepared_async
    checkpoint = await save_async(manager=manager, record=record, memory=sqlite_instance)
    profile = record.profile
    if changed == "transcript":
        await sqlite_instance.add_message_to_memory_async(
            request=MessagePiece(
                role="user",
                original_value="extra",
                conversation_id=record.conversation_id,
            ).to_message()
        )
    elif changed == "profile":
        profile = profile.model_copy(update={"model": "different"})
    elif changed == "model":
        binding = ResolvedModelBinding(
            target=binding.target,
            identifier_hash="different",
            model="model",
            requirements=binding.requirements,
        )
    elif changed == "journal":
        await asyncio.to_thread((manager.store.directory(record.id) / "events.jsonl").write_bytes, b"changed")
    else:
        await asyncio.to_thread(
            (manager.checkpoints.root / str(checkpoint.id) / "workspace.tar").write_bytes, b"changed"
        )
    with patch.object(manager, "_launch_async", new_callable=AsyncMock) as launch:
        with pytest.raises(ValueError):
            await manager.restore_checkpoint_async(
                checkpoint_id=checkpoint.id,
                profile=profile,
                target_id=record.target_id,
                memory=sqlite_instance,
                model_binding=binding,
            )
    launch.assert_not_awaited()
    assert len(manager.records) == 1 and not manager.has_live_executions


async def test_capture_failure_closes_without_publishing_success_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    with patch.object(
        DockerSession, "capture_checkpoint_async", new_callable=AsyncMock, side_effect=ValueError("unsafe state")
    ):
        with pytest.raises(ValueError, match="unsafe state"):
            await save_async(manager=manager, record=record, memory=sqlite_instance)
    assert record.state == AgentExecutionState.CLOSED and record.close_reason == "checkpoint_failed"
    assert record.checkpoint_id is None and not manager.has_live_executions


async def test_capture_cancellation_keeps_ownership_until_capture_and_cleanup_finish_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    started, release = asyncio.Event(), asyncio.Event()

    async def capture_async(**kwargs: Any) -> tuple[bytes, bytes]:
        started.set()
        await release.wait()
        return archive(root="workspace"), archive(root=STATE_ROOT)

    with patch.object(DockerSession, "capture_checkpoint_async", side_effect=capture_async):
        task = asyncio.create_task(save_async(manager=manager, record=record, memory=sqlite_instance))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and manager.has_live_executions
        with pytest.raises(ValueError, match="idle"):
            await save_async(manager=manager, record=record, memory=sqlite_instance)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert record.state == AgentExecutionState.CLOSED and record.checkpoint_id is not None


async def test_shutdown_waits_for_checkpoint_publication_after_container_cleanup_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    entered, release = asyncio.Event(), asyncio.Event()
    original = manager.checkpoints.save_async

    async def persist_async(**kwargs: Any) -> None:
        entered.set()
        await release.wait()
        await original(**kwargs)

    with patch.object(manager.checkpoints, "save_async", side_effect=persist_async):
        saving = asyncio.create_task(save_async(manager=manager, record=record, memory=sqlite_instance))
        await entered.wait()
        assert record.state == AgentExecutionState.CLOSED
        assert manager.has_live_executions
        closing = asyncio.create_task(manager.close_async())
        await asyncio.sleep(0)
        assert not closing.done()
        assert manager._file_lock is not None
        release.set()
        await saving
        await closing
    assert manager._file_lock is None
    assert await manager.checkpoints.get_async(record.checkpoint_id)


async def test_failed_restore_remains_visible_and_cannot_silently_rewind_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, binding = prepared_async
    checkpoint = await save_async(manager=manager, record=record, memory=sqlite_instance)
    with patch.object(manager, "_launch_async", new_callable=AsyncMock, side_effect=RuntimeError("native load failed")):
        with pytest.raises(RuntimeError, match="native load failed"):
            await manager.restore_checkpoint_async(
                checkpoint_id=checkpoint.id,
                profile=record.profile,
                target_id=record.target_id,
                memory=sqlite_instance,
                model_binding=binding,
            )
    failed = manager.latest_execution(conversation_id=record.conversation_id)
    assert failed.id != record.id and failed.state == AgentExecutionState.CLOSED
    assert failed.close_reason == "checkpoint_restore_failed"
    assert not (await manager.checkpoint_status_async(record.id)).restore_available
    assert not manager.has_live_executions


@pytest.mark.parametrize(
    "change",
    [
        {"environment": AgentEnvironment.LOCAL, "local_execution_acknowledged": True},
        {"workspace_base": AgentWorkspaceBase.EMPTY},
        {"fixture_directory": "fixture"},
        {"credential_env": ("TOOL_TOKEN",)},
        {"authentication_method": "login"},
        {"target_registry_name": None},
    ],
)
async def test_unsupported_execution_is_not_closed_by_readiness_check_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    change: dict,
) -> None:
    manager, record, _ = prepared_async
    record.profile = record.profile.model_copy(update=change)
    with pytest.raises(ValueError):
        require_checkpoint_ready(record)
    assert not (await manager.checkpoint_status_async(record.id)).save_available
    assert record.state == AgentExecutionState.IDLE


@pytest.mark.parametrize("path", ["/outside", "workspace/../outside", "workspace\\outside", "unrelated/data"])
def test_checkpoint_archive_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        CheckpointArchive.normalize(raw=archive(root=path), root="/workspace")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_checkpoint_archive_rejects_links_and_devices(kind: bytes) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        CheckpointArchive.normalize(raw=archive(root="workspace", kind=kind), root="/workspace")


def test_checkpoint_archive_preserves_owner_and_rejects_runtime_credentials() -> None:
    raw = archive(root="workspace", data=b"synthetic-credential")
    with pytest.raises(ValueError, match="credential"):
        CheckpointArchive.normalize(raw=raw, root="/workspace", secrets=("synthetic-credential",))
    normalized = CheckpointArchive.normalize(raw=raw, root="/workspace")
    with tarfile.open(fileobj=io.BytesIO(normalized)) as source:
        assert source.getmembers()[0].uid == 1000
    with patch.object(CheckpointArchive, "MAX_BYTES", len(raw) - 1), pytest.raises(ValueError, match="limit"):
        CheckpointArchive.normalize(raw=raw, root="/workspace")


async def test_missing_final_response_is_rejected_before_stopping_execution_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    record.turns[0].response_text = "Not persisted yet"
    with pytest.raises(ValueError, match="not been persisted"):
        await save_async(manager=manager, record=record, memory=sqlite_instance)
    assert record.state == AgentExecutionState.IDLE and manager.has_live_executions


async def test_native_response_binding_preserves_normalizer_response_conversion_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    record.turns[0].response_text = "Native response"
    await sqlite_instance.add_message_to_memory_async(
        request=MessagePiece(
            role="assistant",
            original_value="Native response",
            converted_value="Converted response",
            conversation_id=record.conversation_id,
            prompt_metadata={"agent_turn_id": str(record.turns[0].id)},
        ).to_message()
    )
    assert await save_async(manager=manager, record=record, memory=sqlite_instance)


async def test_retention_capacity_rejects_before_stopping_execution_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    await asyncio.to_thread(manager.checkpoints.root.mkdir)
    with patch.object(manager.checkpoints, "MAX_CHECKPOINTS", 0):
        with pytest.raises(ValueError, match="retention capacity"):
            await save_async(manager=manager, record=record, memory=sqlite_instance)
    assert record.state == AgentExecutionState.IDLE


async def test_concurrent_saves_reserve_retention_before_either_execution_is_closed_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    request = MessagePiece(role="user", original_value="second", conversation_id=str(uuid4()))
    await sqlite_instance.add_message_to_memory_async(request=request.to_message())
    second = record.model_copy(
        deep=True,
        update={
            "id": uuid4(),
            "conversation_id": request.conversation_id,
            "turns": [
                AgentTurn(
                    request_id=str(request.id), prompt="second", status=AgentTurnStatus.COMPLETED, capture_complete=True
                )
            ],
        },
    )
    manager.records[second.id] = second
    manager._live[second.id] = manager._make_live(second)
    entered, release = asyncio.Event(), asyncio.Event()

    async def capture_async(**kwargs: Any) -> tuple[bytes, bytes]:
        entered.set()
        await release.wait()
        return archive(root="workspace"), archive(root=STATE_ROOT)

    with (
        patch.object(manager.checkpoints, "MAX_CHECKPOINTS", 1),
        patch.object(DockerSession, "capture_checkpoint_async", side_effect=capture_async),
    ):
        task = asyncio.create_task(save_async(manager=manager, record=record, memory=sqlite_instance))
        await entered.wait()
        try:
            with pytest.raises(ValueError, match="retention capacity"):
                await save_async(manager=manager, record=second, memory=sqlite_instance)
            assert second.state == AgentExecutionState.IDLE
        finally:
            release.set()
            await task
    assert not manager.checkpoints._reservations


async def test_interrupted_deletion_remains_discoverable_and_never_restorable_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    checkpoint = await save_async(manager=manager, record=record, memory=sqlite_instance)
    original = Path.unlink

    def remove(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name == "workspace.tar":
            raise PermissionError("locked checkpoint")
        original(path, *args, **kwargs)

    with patch.object(Path, "unlink", remove):
        with pytest.raises(PermissionError):
            await manager.delete_checkpoint_async(checkpoint.id)
    status = await manager.checkpoint_status_async(record.id)
    assert status.checkpoint == checkpoint and status.deletion_pending and not status.restore_available
    with pytest.raises(ValueError, match="deletion"):
        await manager.checkpoints.load_async(checkpoint.id)
    await manager.delete_checkpoint_async(checkpoint.id)
    assert not (await manager.checkpoint_status_async(record.id)).deletion_pending
    assert (manager.store.directory(record.id) / "events.jsonl").is_file()


async def test_partial_checkpoint_write_cleans_only_its_owned_files_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    manager, record, _ = prepared_async
    original = Path.open

    def open_file(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path.name == "harness-state.tar" and args and args[0] == "xb":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    with patch.object(Path, "open", open_file):
        with pytest.raises(OSError, match="disk full"):
            await save_async(manager=manager, record=record, memory=sqlite_instance)
    assert not list(manager.checkpoints.root.iterdir())
    assert (manager.store.directory(record.id) / "events.jsonl").is_file()


async def test_admin_checkpoint_routes_require_consent_and_share_conversation_admission_async(
    prepared_async: tuple[AgentExecutionManager, AgentExecution, ResolvedModelBinding],
    sqlite_instance: SQLiteMemory,
) -> None:
    from pyrit.backend.middleware.auth import require_admin
    from pyrit.backend.routes import executions
    from pyrit.backend.services.manual_send_scheduler import ManualSendScheduler

    manager, record, _ = prepared_async
    scheduler = ManualSendScheduler()
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[require_admin] = lambda: None
    route = f"/executions/{record.id}/checkpoint"
    with (
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
        patch.object(executions, "get_manual_send_scheduler", return_value=scheduler),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.get(route)).json()["save_available"]
            assert (await client.post(route, json={"acknowledge_sensitive_state": False})).status_code == 400
            assert (await client.post(route, json={"acknowledge_sensitive_state": "true"})).status_code == 422
            with scheduler.reserve(conversation_id=record.conversation_id):
                assert (await client.post(route, json={"acknowledge_sensitive_state": True})).status_code == 409
            assert record.state == AgentExecutionState.IDLE
            response = await client.post(route, json={"acknowledge_sensitive_state": True})
            assert response.status_code == 200 and response.json()["source_execution_id"] == str(record.id)
            assert (await client.get(route)).json()["restore_available"]
            assert (await client.delete(route)).status_code == 200
            assert not (await client.get(route)).json()["restore_available"]
            assert record.state == AgentExecutionState.CLOSED


async def test_all_checkpoint_routes_are_admin_only_async() -> None:
    from pyrit.backend.authentication_policy import AuthenticationPolicy
    from pyrit.backend.routes import executions

    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.router)
    route = f"/executions/{uuid4()}/checkpoint"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for method, suffix in (("GET", ""), ("POST", ""), ("POST", "/restore"), ("DELETE", "")):
            response = await client.request(method, route + suffix, json={"acknowledge_sensitive_state": True})
            assert response.status_code == 403
