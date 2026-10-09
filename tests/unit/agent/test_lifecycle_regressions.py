# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

pytest.importorskip("acp")

from pyrit.agent.environment import ExecutionEnvironment
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models.agent_execution import AgentExecution, AgentExecutionState, AgentProfile


def make_manager(tmp_path: Path) -> tuple[AgentExecutionManager, AgentExecution]:
    manager = AgentExecutionManager(root=tmp_path)
    record = AgentExecution(
        owner_id="test",
        conversation_id="conversation",
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True),
    )
    manager.records[record.id] = record
    return manager, record


async def test_cancelled_close_waits_for_startup(tmp_path: Path) -> None:
    manager, record = make_manager(tmp_path)
    environment = MagicMock(spec=ExecutionEnvironment)
    environment.close_async = AsyncMock()
    with patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment):
        live = manager._make_live(record)
    manager._live[record.id] = live
    startup_done = asyncio.Event()
    live.startup = asyncio.create_task(startup_done.wait())
    close = asyncio.create_task(manager.close_execution_async(execution_id=record.id))
    await asyncio.sleep(0.05)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    environment.close_async.assert_not_awaited()
    startup_done.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    environment.close_async.assert_awaited_once()
    assert record.state == AgentExecutionState.CLOSED


async def test_persistence_failure_still_releases_resources(tmp_path: Path) -> None:
    manager, record = make_manager(tmp_path)
    environment = MagicMock(spec=ExecutionEnvironment)
    environment.close_async = AsyncMock()
    with patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment):
        manager._live[record.id] = manager._make_live(record)
    with patch.object(manager.store, "save_async", AsyncMock(side_effect=OSError("disk full"))):
        with pytest.raises(ExceptionGroup, match="persistence failed"):
            await manager.close_execution_async(execution_id=record.id)
    environment.close_async.assert_awaited_once()
    assert not manager.has_live_executions


async def test_cancelled_append_keeps_sequence_accounting(tmp_path: Path) -> None:
    manager, record = make_manager(tmp_path)
    await manager.store.save_async(record)
    live = manager._make_live(record)
    entered = threading.Event()
    release = threading.Event()
    original = manager.store._append

    def delayed_append(*, execution_id: UUID, data: bytes) -> None:
        entered.set()
        release.wait(timeout=3)
        original(execution_id=execution_id, data=data)

    with patch.object(manager.store, "_append", side_effect=delayed_append):
        write = asyncio.create_task(manager._record_async(live, direction="incoming", payload={"value": 1}))
        await asyncio.to_thread(entered.wait, 3)
        write.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await write
    await manager._record_async(live, direction="incoming", payload={"value": 2})
    page = await manager.store.events_async(execution_id=record.id)
    assert [event.sequence for event in page.events] == [1, 2]
    assert record.event_count == 2


async def test_stderr_redacts_secrets_split_across_reads(tmp_path: Path) -> None:
    manager, record = make_manager(tmp_path)
    await manager.store.save_async(record)
    live = manager._make_live(record)
    live.secrets = ("synthetic-test-credential",)
    reader = MagicMock(spec=asyncio.StreamReader)
    reader.read = AsyncMock(side_effect=[b"prefix synthetic-test-", b"credential suffix", b""])
    await manager._stderr_async(live, reader)
    page = await manager.store.events_async(execution_id=record.id)
    combined = "".join(str(event.payload["text"]) for event in page.events)
    assert "synthetic-test-credential" not in combined
    assert "[REDACTED]" in combined


async def test_concurrent_start_close_releases_owner_lock(tmp_path: Path) -> None:
    manager = AgentExecutionManager(root=tmp_path)
    started = asyncio.create_task(manager.start_async())
    closed = asyncio.create_task(manager.close_async())
    await asyncio.gather(started, closed)
    assert manager._file_lock is None
    assert manager._reaper is None or manager._reaper.done()
    async with AgentExecutionManager(root=tmp_path):
        pass


async def test_recorded_evidence_is_bounded(tmp_path: Path) -> None:
    manager, record = make_manager(tmp_path)
    record.profile = record.profile.model_copy(update={"max_evidence_bytes": 4096})
    await manager.store.save_async(record)
    live = manager._make_live(record)
    with pytest.raises(RuntimeError, match="budget exceeded"):
        await manager._record_async(live, direction="incoming", payload={"text": "x" * 4097})
    assert record.capture_error is not None
    assert record.event_count == 0
    assert json.loads((tmp_path / str(record.id) / "record.json").read_text())["state"] == "starting"
