# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.executor.attack import AttackExecutor, PromptSendingAttack
from pyrit.models import AttackSeedGroup, SeedObjective
from pyrit.models.agent_execution import AgentExecutionState, AgentProfile, AgentTurnStatus
from pyrit.prompt_target import AgentTarget
from pyrit.scenario import AtomicAttack, AttackTechnique

pytestmark = pytest.mark.run_only_if_all_tests


def scripted_profile() -> AgentProfile:
    root = Path(__file__).resolve().parents[3]
    return AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        command=(sys.executable, str(Path(__file__).with_name("scripted_acp_agent.py"))),
        fixture_directory=str(root / "assets" / "agent_receipt"),
        artifact_paths=("receipt.json",),
    )


async def test_atomic_attack_uses_isolated_agent_executions(tmp_path: Path) -> None:
    async with AgentExecutionManager(root=tmp_path) as manager:
        target = AgentTarget(profile=scripted_profile())
        target.bind_execution_manager(manager)
        atomic = AtomicAttack(
            atomic_attack_name="receipt-integration",
            attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
            seed_groups=[
                AttackSeedGroup(seeds=[SeedObjective(value="Create the first receipt")]),
                AttackSeedGroup(seeds=[SeedObjective(value="Create the second receipt")]),
            ],
        )
        result = await atomic.run_async(executor=AttackExecutor(max_concurrency=2))
        assert len(result.completed_results) == 2
        assert not result.has_incomplete
        assert len(manager.records) == 2
        for record in manager.records.values():
            assert record.state == AgentExecutionState.CLOSED
            assert len(record.turns) == 1
            receipt = await asyncio.to_thread(
                (manager.store.directory(record.id) / "artifacts" / "receipt.json").read_text
            )
            assert json.loads(receipt) == {"total": 31}
            assert not (manager.store.directory(record.id) / "workspace").exists()


async def test_real_transport_cancellation_and_expiry(tmp_path: Path) -> None:
    profile = scripted_profile().model_copy(update={"idle_timeout_seconds": 0.1})
    async with AgentExecutionManager(root=tmp_path) as manager:
        send = asyncio.create_task(
            manager.send_async(
                profile=profile,
                target_id="test",
                conversation_id="one",
                request_id="one",
                prompt="wait",
                has_history=False,
            )
        )
        async with asyncio.timeout(10):
            while not manager.records or next(iter(manager.records.values())).state != AgentExecutionState.WORKING:
                await asyncio.sleep(0.01)
            record = next(iter(manager.records.values()))
            while not (await manager.store.events_async(execution_id=record.id)).events:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            await manager.cancel_async(record.id)
            _, turn = await send
            assert turn.status == AgentTurnStatus.CANCELLED
            while record.state != AgentExecutionState.CLOSED:
                await asyncio.sleep(0.02)
        assert record.close_reason == "expired"
        assert not record.cleanup_error
