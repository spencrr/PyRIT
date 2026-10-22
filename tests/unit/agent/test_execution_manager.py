# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("acp")

from pyrit.agent.acp_connection import AcpConnection
from pyrit.agent.environment import EnvironmentProviderRegistry, ExecutionSession
from pyrit.agent.environments.local import LocalProvider
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models.agent_environment import EnvironmentCapabilities, EnvironmentProviderInfo
from pyrit.models.agent_execution import (
    AgentExecutionState,
    AgentProfile,
    AgentTargetConfiguration,
    AgentTurnStatus,
    EnvironmentTemplate,
    HarnessProfile,
)


@pytest.fixture
def configuration() -> AgentTargetConfiguration:
    return AgentTargetConfiguration(
        environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
    )


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
    value = MagicMock(spec=ExecutionSession)
    value.launch_async = AsyncMock()
    value.close_async = AsyncMock()
    value.agent_cwd = "workspace"
    value.harness_home = "home"
    value.acp_reader = asyncio.StreamReader()
    value.acp_writer = MagicMock(spec=asyncio.StreamWriter)
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    value.stderr = stderr
    return value


async def test_fresh_execution_multiturn_and_no_implicit_resurrection(
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
) -> None:
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            first, result = await manager.send_async(
                configuration=configuration,
                target_id="target",
                conversation_id="one",
                request_id="1",
                prompt="Create receipt",
                has_history=False,
            )
            assert result.status == AgentTurnStatus.COMPLETED
            assert result.capture_complete
            second, _ = await manager.send_async(
                configuration=configuration,
                target_id="target",
                conversation_id="one",
                request_id="2",
                prompt="Update receipt",
                has_history=True,
            )
            assert second.id == first.id
            assert connection.initialize_async.await_count == 1
            with pytest.raises(ValueError, match="interaction mode cannot change"):
                await manager.send_async(
                    configuration=configuration,
                    target_id="target",
                    conversation_id="one",
                    request_id="mode-change",
                    prompt="Do not change mode",
                    has_history=True,
                    interactive=True,
                )
            assert len(first.turns) == 2
            third, _ = await manager.send_async(
                configuration=configuration,
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
                    configuration=configuration,
                    target_id="target",
                    conversation_id="one",
                    request_id="4",
                    prompt="Do not replay",
                    has_history=True,
                    interactive=True,
                )
        assert environment.close_async.await_count == 2
        retained = await manager.store.load_all_async()
        assert all(record.state == AgentExecutionState.CLOSED for record in retained)
        assert retained[0].turns[0].prompt


async def test_startup_failure_is_retained_and_cleaned(
    tmp_path: Path, configuration: AgentTargetConfiguration, environment: MagicMock
) -> None:
    environment.launch_async.side_effect = RuntimeError("image missing")
    with patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment):
        async with AgentExecutionManager(root=tmp_path) as manager:
            with pytest.raises(RuntimeError, match="image missing"):
                await manager.send_async(
                    configuration=configuration,
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
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
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
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            send = asyncio.create_task(
                manager.send_async(
                    configuration=configuration,
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
    tmp_path: Path, configuration: AgentTargetConfiguration, environment: MagicMock
) -> None:
    from filelock import Timeout

    from pyrit.models.agent_execution import AgentExecution, AgentTurn

    manager = AgentExecutionManager(root=tmp_path)
    record = AgentExecution(
        owner_id=str(tmp_path),
        conversation_id="one",
        target_id="target",
        profile=configuration.to_profile(),
        turns=[AgentTurn(request_id="1", prompt="Prepared before dispatch")],
    )
    await manager.store.save_async(record)
    with patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment):
        async with manager:
            assert manager.records[record.id].turns[0].status == AgentTurnStatus.UNKNOWN
            assert manager.records[record.id].state == AgentExecutionState.CLOSED
            with pytest.raises(Timeout):
                await AgentExecutionManager(root=tmp_path).start_async()


async def test_missing_provider_is_reported_not_claimed_released(tmp_path: Path) -> None:
    from pyrit.models.agent_execution import AgentExecution

    record = AgentExecution(
        owner_id=str(tmp_path),
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(environment="team_pool", environment_settings={"pool": "blue"}),
        state=AgentExecutionState.IDLE,
    )
    manager = AgentExecutionManager(root=tmp_path)
    await manager.store.save_async(record)
    registry = EnvironmentProviderRegistry()
    with patch.object(EnvironmentProviderRegistry, "_singleton", registry):
        async with manager:
            reloaded = manager.records[record.id]
            assert reloaded.state == AgentExecutionState.CLEANUP_FAILED
            assert reloaded.cleanup_error and "team_pool" in reloaded.cleanup_error
            assert "resources may remain allocated" in reloaded.cleanup_error
            assert not manager.has_live_executions
            await manager.close_execution_async(execution_id=record.id)
            send: dict[str, Any] = {
                "configuration": AgentTargetConfiguration(
                    environment_template=EnvironmentTemplate(
                        environment="team_pool", environment_settings={"pool": "blue"}
                    )
                ),
                "target_id": "target",
                "conversation_id": "one",
                "request_id": "2",
                "prompt": "again",
            }
            with pytest.raises(ValueError, match="unavailable"):
                await manager.send_async(**send, has_history=True)

            class TeamPool(LocalProvider):
                @property
                def info(self) -> EnvironmentProviderInfo:
                    return EnvironmentProviderInfo(
                        name="team_pool",
                        display_name="Team pool",
                        capabilities=EnvironmentCapabilities(isolated=True),
                    )

                def validate_template(self, template: EnvironmentTemplate) -> None:
                    return

            # Installing the provider later does not resurrect a record whose cleanup already failed.
            registry.register(TeamPool())
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await manager.send_async(**send, has_history=True)


def test_recorded_errors_keep_json_rpc_details_within_a_bound() -> None:
    class RpcError(Exception):
        data = {"details": "x" * 10_000}

    described = AgentExecutionManager._describe_error(RpcError("Internal error"))
    assert described.startswith("Internal error: xxx")
    assert len(described) == AgentExecutionManager.MAX_ERROR_CHARS
    assert AgentExecutionManager._describe_error(ValueError("plain")) == "plain"


async def test_cancel_during_provisioning_does_not_dispatch_and_next_turn_can_continue(
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def launch_async(spec: object) -> None:
        entered.set()
        await release.wait()

    environment.launch_async.side_effect = launch_async
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            send = asyncio.create_task(
                manager.send_async(
                    configuration=configuration,
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
                configuration=configuration,
                target_id="target",
                conversation_id="one",
                request_id="2",
                prompt="Continue",
                has_history=True,
            )
            assert next_turn.status == AgentTurnStatus.COMPLETED
            connection.prompt_async.assert_awaited_once()


@pytest.mark.parametrize("interactive", [True, False])
async def test_deadline_holds_interactive_but_closes_automated(
    tmp_path: Path,
    configuration: AgentTargetConfiguration,
    connection: MagicMock,
    environment: MagicMock,
    interactive: bool,
) -> None:
    cancelled = asyncio.Event()

    async def prompt_async(prompt: str) -> tuple[str, str]:
        await cancelled.wait()
        return "cancelled", "Partial"

    async def cancel_async() -> None:
        cancelled.set()

    connection.prompt_async.side_effect = prompt_async
    connection.cancel_async.side_effect = cancel_async
    configuration = configuration.model_copy(update={"turn_timeout_seconds": 0.03, "interactive_hold_seconds": 1})
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            arguments = {
                "configuration": configuration,
                "target_id": "target",
                "conversation_id": "one",
                "request_id": "first",
                "prompt": "Wait",
                "has_history": False,
                "interactive": interactive,
            }
            record, turn = await manager.send_async(**arguments)
            assert turn.status == AgentTurnStatus.CANCELLED
            assert turn.response_text == "Partial"
            if interactive:
                assert record.state == AgentExecutionState.HELD
                assert record.held_until is not None
                environment.close_async.assert_not_awaited()
                await manager.control_held_async(execution_id=record.id, action="extend", actor="operator")
                await manager.control_held_async(execution_id=record.id, action="continue", actor="operator")
                assert record.state == AgentExecutionState.IDLE
            else:
                # Automated runs get an explicit cancelled outcome and the execution is released.
                assert turn.error == "Turn deadline exceeded"
                assert record.state == AgentExecutionState.CLOSED
                assert record.close_reason == "turn_timeout"
                environment.close_async.assert_awaited()


async def test_automation_rejects_ask_before_provisioning(
    tmp_path: Path, configuration: AgentTargetConfiguration
) -> None:
    from pyrit.models.agent_execution import AgentPermissionPolicy

    manager = AgentExecutionManager(root=tmp_path)
    with pytest.raises(ValueError, match="Automated"):
        await manager.send_async(
            configuration=configuration.model_copy(
                update={"harness_profile": HarnessProfile(permission_policy=AgentPermissionPolicy.ASK)}
            ),
            target_id="target",
            conversation_id="one",
            request_id="one",
            prompt="Hi",
            has_history=False,
        )
    assert not manager.records


async def test_unconfirmed_deadline_cancellation_raises_and_closes(
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
) -> None:
    never = asyncio.Event()

    async def prompt_async(prompt: str) -> tuple[str, str]:
        await never.wait()
        return "end_turn", ""

    connection.prompt_async.side_effect = prompt_async
    configuration = configuration.model_copy(update={"turn_timeout_seconds": 0.03, "cancellation_grace_seconds": 0.05})
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            with pytest.raises(RuntimeError, match="did not confirm cancellation"):
                await manager.send_async(
                    configuration=configuration,
                    target_id="target",
                    conversation_id="one",
                    request_id="first",
                    prompt="Wait",
                    has_history=False,
                )
            record = next(iter(manager.records.values()))
            assert record.turns[0].status == AgentTurnStatus.UNKNOWN
            assert record.state == AgentExecutionState.CLOSED
            assert record.close_reason == "cancellation_unconfirmed"
            never.set()


async def test_caller_cancellation_during_startup_releases_the_execution(
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def launch_async(spec: object) -> None:
        entered.set()
        await release.wait()

    environment.launch_async.side_effect = launch_async
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path, max_executions=1) as manager:
            send = asyncio.create_task(
                manager.send_async(
                    configuration=configuration,
                    target_id="target",
                    conversation_id="one",
                    request_id="1",
                    prompt="Interrupted",
                    has_history=False,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=3)
            send.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await send
            record = next(iter(manager.records.values()))
            assert record.state == AgentExecutionState.CLOSED
            assert record.close_reason == "caller_cancelled"
            assert record.turns[0].status == AgentTurnStatus.CANCELLED
            assert not manager.has_live_executions
            connection.prompt_async.assert_not_awaited()
            environment.launch_async.side_effect = None
            # The slot is free again: a new conversation starts without waiting for the lifetime to lapse.
            fresh, turn = await manager.send_async(
                configuration=configuration,
                target_id="target",
                conversation_id="two",
                request_id="2",
                prompt="Fresh",
                has_history=False,
            )
            assert fresh.id != record.id
            assert turn.status == AgentTurnStatus.COMPLETED


async def test_admission_waits_for_a_slot_then_fails_explicitly(
    tmp_path: Path, configuration: AgentTargetConfiguration, connection: MagicMock, environment: MagicMock
) -> None:
    with (
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
        patch("pyrit.agent.acp_connection.AcpConnection", return_value=connection),
    ):
        async with AgentExecutionManager(root=tmp_path, max_executions=1) as manager:
            first, _ = await manager.send_async(
                configuration=configuration,
                target_id="target",
                conversation_id="one",
                request_id="1",
                prompt="Hold the slot",
                has_history=False,
            )
            impatient = configuration.model_copy(update={"startup_timeout_seconds": 0.05})
            with pytest.raises(RuntimeError, match="capacity"):
                await manager.send_async(
                    configuration=impatient,
                    target_id="target",
                    conversation_id="two",
                    request_id="2",
                    prompt="Cannot wait",
                    has_history=False,
                )
            waiting = asyncio.create_task(
                manager.send_async(
                    configuration=configuration,
                    target_id="target",
                    conversation_id="three",
                    request_id="3",
                    prompt="Will wait",
                    has_history=False,
                )
            )
            await asyncio.sleep(0.05)
            assert not waiting.done()
            await manager.close_execution_async(execution_id=first.id)
            third, turn = await asyncio.wait_for(waiting, timeout=3)
            assert third.id != first.id
            assert turn.status == AgentTurnStatus.COMPLETED


async def test_journal_redaction_covers_escaped_and_non_ascii_secrets(
    tmp_path: Path, configuration: AgentTargetConfiguration, environment: MagicMock
) -> None:
    from pyrit.models.agent_execution import AgentExecution

    secrets = ('quote"inside', "back\\slash", "tab\there", "pässwörd")
    record = AgentExecution(
        owner_id=str(tmp_path), conversation_id="one", target_id="target", profile=configuration.to_profile()
    )
    with patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment):
        async with AgentExecutionManager(root=tmp_path) as manager:
            manager.records[record.id] = record
            await manager.store.save_async(record)
            live = manager._make_live(record)
            live.secrets = secrets
            payload = {
                "method": "session/new",
                "params": {"env": [f"TOKEN={secret}" for secret in secrets], 'quote"inside': ["pässwörd"]},
            }
            await manager._record_async(live, direction="outgoing", payload=payload)
            page = await manager.store.events_async(execution_id=record.id, after=0, limit=10)
            serialized = page.events[0].model_dump_json()
            assert all(secret not in serialized for secret in secrets)
            assert serialized.count("[REDACTED]") == 6
            assert record.capture_error is None


async def test_short_credential_values_are_refused_before_launch(
    tmp_path: Path, configuration: AgentTargetConfiguration, environment: MagicMock
) -> None:
    configuration = configuration.model_copy(
        update={"harness_profile": HarnessProfile(credential_env=("AGENT_TEST_SHORT_SECRET",))}
    )
    with (
        patch.dict("os.environ", {"AGENT_TEST_SHORT_SECRET": "1"}),
        patch("pyrit.agent.execution_manager.create_execution_session", return_value=environment),
    ):
        async with AgentExecutionManager(root=tmp_path) as manager:
            with pytest.raises(ValueError, match="cannot be redacted"):
                await manager.send_async(
                    configuration=configuration,
                    target_id="target",
                    conversation_id="one",
                    request_id="1",
                    prompt="Hi",
                    has_history=False,
                )
            assert not manager.records
            environment.launch_async.assert_not_awaited()
