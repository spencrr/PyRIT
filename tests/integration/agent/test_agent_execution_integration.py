# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("acp")

from pyrit.agent.evidence import AgentEvidenceExporter
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.harness import HarnessAdapter, HarnessAdapterRegistry
from pyrit.executor.attack import AttackExecutor, AttackScoringConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    ScoringExpectation,
    SeedObjective,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.models.agent_execution import AgentExecutionState, AgentProfile, AgentTargetConfiguration, AgentTurnStatus
from pyrit.models.agent_setup import AgentTargetComposition
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.prompt_target import AgentTarget
from pyrit.registry import AgentConfigurationRegistry, InitializerRegistry, TargetRegistry
from pyrit.scenario import AtomicAttack, AttackTechnique
from pyrit.score import MessageToolCallScorer
from pyrit.setup.agent_initialization import register_agent_target


@pytest.mark.parametrize("harness_kind", ["acp", "receipt_v1"])
async def test_initializer_composed_target_uses_the_existing_execution_and_scorer_async(
    tmp_path: Path,
    harness_kind: str,
) -> None:
    from unittest.mock import patch

    from pyrit.registry import Registry

    launches: list[str] = []

    class ReceiptAdapter(HarnessAdapter):
        @property
        def info(self) -> HarnessAdapterInfo:
            return HarnessAdapterInfo(name="receipt_v1", display_name="Receipt peer")

        def launch_environment(self, *, home: str) -> dict[str, str]:
            launches.append(home)
            return {"RECEIPT_ADAPTER": "v1"}

    with (
        patch.dict(Registry._singletons, clear=True),
        patch.object(AgentConfigurationRegistry, "_singleton", None),
        patch.object(HarnessAdapterRegistry, "_singleton", None),
    ):
        HarnessAdapterRegistry.get_registry_singleton().register(ReceiptAdapter())
        registry = InitializerRegistry.get_registry_singleton()
        profile = scripted_profile()
        for name, params in [
            ("harness_profile", {"name": "scripted", "harness_kind": harness_kind, "command": list(profile.command)}),
            (
                "environment_template",
                {"name": "fixture", "environment": "local", "fixture_directory": profile.fixture_directory},
            ),
        ]:
            await registry.create_and_configure(name, initializer_params=params).initialize_async()
        target = register_agent_target(
            AgentTargetComposition(
                name="receipt_test",
                harness_profile="scripted",
                environment_template="fixture",
                local_execution_acknowledged=True,
                artifact_paths=("receipt.json",),
            )
        )
        assert target is TargetRegistry.get_registry_singleton().instances.get("receipt_test")
        assert target is not None
        async with AgentExecutionManager(root=tmp_path) as manager:
            target.bind_execution_manager(manager)
            result = await PromptSendingAttack(
                objective_target=target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
            ).execute_async(
                objective="tool-only",
                expectation=ScoringExpectation(
                    conditions=(ToolsCalled(tools=(ToolCallRequirement(name="read_orders"),)),)
                ),
            )
            assert result.outcome is AttackOutcome.SUCCESS
        record = next(iter(manager.records.values()))
        assert record.state is AgentExecutionState.CLOSED
        assert record.artifacts == ["receipt.json"]
        receipt = await asyncio.to_thread(
            (manager.store.directory(record.id) / "artifacts" / "receipt.json").read_text,
        )
        assert json.loads(receipt) == {"total": 31}
        assert bool(launches) == (harness_kind == "receipt_v1")
        exporter = AgentEvidenceExporter(manager=manager, memory=CentralMemory.get_memory_instance())
        package = await exporter.archive_async(record.id)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["event_cursor"] == record.event_count
            artifact = archive.read("artifacts/receipt.json")
            assert json.loads(artifact) == {"total": 31}
            assert (
                next(item for item in manifest["files"] if item["path"] == "artifacts/receipt.json")["sha256"]
                == hashlib.sha256(artifact).hexdigest()
            )
            assert any(score["score_value"] == "true" for score in json.loads(archive.read("scores.json")))
            assert any(
                piece["original_value_data_type"] == "function_call"
                for message in json.loads(archive.read("messages.json"))
                for piece in message["message_pieces"]
            )


async def test_tool_only_subprocess_completion_is_scorable(tmp_path: Path) -> None:
    async with AgentExecutionManager(root=tmp_path) as manager:
        target = AgentTarget(agent_configuration=AgentTargetConfiguration.from_profile(scripted_profile()))
        target.bind_execution_manager(manager)
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
        )
        result = await attack.execute_async(
            objective="tool-only",
            expectation=ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="read_orders"),)),)),
        )
        assert result.outcome is AttackOutcome.SUCCESS
        assert result.last_response.role == "tool"
        record = next(iter(manager.records.values()))
        assert record.turns[0].tool_call_messages == 2
        assert not record.turns[0].response_text


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
        target = AgentTarget(agent_configuration=AgentTargetConfiguration.from_profile(scripted_profile()))
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


async def test_interactive_permission_wait_pauses_work_clock_and_preserves_order(tmp_path: Path) -> None:
    from pyrit.models.agent_execution import AgentPermissionPolicy

    profile = scripted_profile().model_copy(
        update={
            "permission_policy": AgentPermissionPolicy.ASK,
            "turn_timeout_seconds": 0.3,
        }
    )
    async with AgentExecutionManager(root=tmp_path) as manager:
        send = asyncio.create_task(
            manager.send_async(
                profile=profile,
                target_id="test",
                conversation_id="one",
                request_id="one",
                prompt="approve",
                has_history=False,
                interactive=True,
            )
        )
        async with asyncio.timeout(10):
            while not manager.records or not next(iter(manager.records.values())).approvals:
                await asyncio.sleep(0.01)
            record = next(iter(manager.records.values()))
            await asyncio.sleep(0.4)
            assert not send.done()
            await manager.decide_permission_async(
                execution_id=record.id,
                approval_id=record.approvals[0].id,
                allow=True,
                actor="test-operator",
            )
            _, turn = await send
        assert turn.status == AgentTurnStatus.COMPLETED
        assert record.state == AgentExecutionState.IDLE
        page = await manager.store.events_async(execution_id=record.id)
        updates = [
            event.payload.get("params", {}).get("update", {})
            for event in page.events
            if event.payload.get("method") == "session/update"
        ]
        order = [update.get("sessionUpdate") for update in updates]
        assert order == [
            "agent_message_chunk",
            "tool_call",
            "tool_call_update",
            "agent_message_chunk",
            "tool_call",
            "tool_call_update",
            "agent_message_chunk",
        ]
        assert turn.response_text == "I will read the order.Now I will write the receipt.Receipt total: 31"
