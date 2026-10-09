# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("acp")

from pyrit.agent.acp_connection import AcpConnection
from pyrit.agent.environment import ExecutionEnvironment
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models.agent_execution import AgentExecutionState, AgentProfile, AgentTurnStatus


@pytest.fixture
def profile() -> AgentProfile:
    return AgentProfile(environment="local", local_execution_acknowledged=True)


@pytest.fixture
def connection() -> MagicMock:
    value = MagicMock(spec=AcpConnection)
    value.session_id = "native-session"
    value.initialize_async = AsyncMock(return_value={"protocolVersion": 1})
    value.prompt_async = AsyncMock(return_value=("end_turn", "Receipt total: 31"))
    value.cancel_async = AsyncMock()
    value.close_async = AsyncMock()
    return value


@pytest.fixture(name="environment")
async def environment_async() -> MagicMock:
    value = MagicMock(spec=ExecutionEnvironment)
    value.prepare_async = AsyncMock()
    value.close_async = AsyncMock()
    value.credential_values.return_value = {}
    value.agent_cwd = "workspace"
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.stdout = asyncio.StreamReader()
    process.stdin = MagicMock(spec=asyncio.StreamWriter)
    process.stderr = asyncio.StreamReader()
    process.stderr.feed_eof()
    value.launch_async = AsyncMock(return_value=process)
    return value


async def test_fresh_execution_multiturn_and_no_implicit_resurrection(
    tmp_path: Path, profile: AgentProfile, connection: MagicMock, environment: MagicMock
) -> None:
    with (
        patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            first, result = await manager.send_async(
                profile=profile,
                target_id="target",
                conversation_id="one",
                request_id="1",
                prompt="Create receipt",
                has_history=False,
            )
            assert result.status == AgentTurnStatus.COMPLETED
            assert result.capture_complete
            second, _ = await manager.send_async(
                profile=profile,
                target_id="target",
                conversation_id="one",
                request_id="2",
                prompt="Update receipt",
                has_history=True,
            )
            assert second.id == first.id
            assert connection.initialize_async.await_count == 1
            third, _ = await manager.send_async(
                profile=profile,
                target_id="target",
                conversation_id="two",
                request_id="3",
                prompt="Fresh receipt",
                has_history=False,
            )
            assert third.id != first.id
            await manager.close_execution_async(execution_id=first.id)
            with pytest.raises(RuntimeError, match="closed"):
                await manager.send_async(
                    profile=profile,
                    target_id="target",
                    conversation_id="one",
                    request_id="4",
                    prompt="Do not replay",
                    has_history=True,
                )
        assert environment.close_async.await_count == 2
        retained = await manager.store.load_all_async()
        assert all(record.state == AgentExecutionState.CLOSED for record in retained)
        assert retained[0].turns[0].prompt


async def test_startup_failure_is_retained_and_cleaned(
    tmp_path: Path, profile: AgentProfile, environment: MagicMock
) -> None:
    environment.prepare_async.side_effect = RuntimeError("image missing")
    with patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment):
        async with AgentExecutionManager(root=tmp_path) as manager:
            with pytest.raises(RuntimeError, match="image missing"):
                await manager.send_async(
                    profile=profile,
                    target_id="target",
                    conversation_id="one",
                    request_id="1",
                    prompt="Create receipt",
                    has_history=False,
                )
            record = next(iter(manager.records.values()))
            assert record.state == AgentExecutionState.CLOSED
            assert record.turns[0].status == AgentTurnStatus.UNKNOWN
            assert not record.turns[0].capture_complete
            environment.close_async.assert_awaited_once()


async def test_cancel_settles_turn_without_closing_session(
    tmp_path: Path, profile: AgentProfile, connection: MagicMock, environment: MagicMock
) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def prompt_async(prompt: str) -> tuple[str, str]:
        entered.set()
        await cancelled.wait()
        return "cancelled", ""

    async def cancel_async() -> None:
        cancelled.set()

    connection.prompt_async.side_effect = prompt_async
    connection.cancel_async.side_effect = cancel_async
    with (
        patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            send = asyncio.create_task(
                manager.send_async(
                    profile=profile,
                    target_id="target",
                    conversation_id="one",
                    request_id="1",
                    prompt="Wait",
                    has_history=False,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=3)
            record = next(iter(manager.records.values()))
            await manager.cancel_async(record.id)
            _, turn = await send
            assert turn.status == AgentTurnStatus.CANCELLED
            assert record.state == AgentExecutionState.IDLE
            environment.close_async.assert_not_awaited()


async def test_store_ownership_and_interrupted_reconciliation(
    tmp_path: Path, profile: AgentProfile, environment: MagicMock
) -> None:
    from filelock import Timeout

    from pyrit.models.agent_execution import AgentExecution, AgentTurn

    manager = AgentExecutionManager(root=tmp_path)
    record = AgentExecution(
        owner_id=str(tmp_path),
        conversation_id="one",
        target_id="target",
        profile=profile,
        turns=[AgentTurn(request_id="1", prompt="Prepared before dispatch")],
    )
    await manager.store.save_async(record)
    with patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment):
        async with manager:
            assert manager.records[record.id].turns[0].status == AgentTurnStatus.UNKNOWN
            assert manager.records[record.id].state == AgentExecutionState.CLOSED
            with pytest.raises(Timeout):
                await AgentExecutionManager(root=tmp_path).start_async()


async def test_cancel_during_provisioning_does_not_dispatch_and_next_turn_can_continue(
    tmp_path: Path, profile: AgentProfile, connection: MagicMock, environment: MagicMock
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def prepare_async() -> None:
        entered.set()
        await release.wait()

    environment.prepare_async.side_effect = prepare_async
    with (
        patch("pyrit.agent.execution_manager.ExecutionEnvironment", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            send = asyncio.create_task(
                manager.send_async(
                    profile=profile,
                    target_id="target",
                    conversation_id="one",
                    request_id="1",
                    prompt="Do not dispatch",
                    has_history=False,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=3)
            record = next(iter(manager.records.values()))
            await manager.cancel_async(record.id)
            release.set()
            _, turn = await send
            assert turn.status == AgentTurnStatus.CANCELLED
            connection.prompt_async.assert_not_awaited()
            _, next_turn = await manager.send_async(
                profile=profile,
                target_id="target",
                conversation_id="one",
                request_id="2",
                prompt="Continue",
                has_history=True,
            )
            assert next_turn.status == AgentTurnStatus.COMPLETED
            connection.prompt_async.assert_awaited_once()
