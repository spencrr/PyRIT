# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in actual Docker/ACP checkpoint lifecycle; all inference is synthetic."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

pytest.importorskip("acp")

from build_scripts.agent_checkpoint_demo import CheckpointOracle
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.model_binding import resolve_model_binding
from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import (
    AgentExecutionState,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry

pytestmark = [
    pytest.mark.run_only_if_all_tests,
    pytest.mark.skipif(
        not os.getenv("PYRIT_CHECKPOINT_IMAGE"), reason="Requires an explicitly selected Docker fixture image"
    ),
]


@pytest.mark.parametrize("workspace_change", ["deleted", "file-to-directory"])
async def test_actual_save_close_restart_restore_and_continue_async(tmp_path: Path, workspace_change: str) -> None:
    harness = os.environ["PYRIT_CHECKPOINT_HARNESS"]
    image = os.environ["PYRIT_CHECKPOINT_IMAGE"]
    memory = CentralMemory.get_memory_instance()
    source = CheckpointOracle(history_token=f"native-history-{uuid4()}")
    registry = TargetRegistry.get_registry_singleton()
    source_name = f"checkpoint_model_{uuid4().hex}"
    registry.instances.register(source, name=source_name)
    target = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            model_binding=ModelBinding(model="checkpoint-model", target_registry_name=source_name),
            harness_profile=HarnessProfile(harness_kind=harness, permission_policy="allow_once"),
            environment_template=EnvironmentTemplate(environment="docker", image=image),
            artifact_paths=("receipt.json", "effects.log"),
        )
    )
    conversation = str(uuid4())
    normalizer = PromptNormalizer()
    with patch.dict(
        os.environ,
        {
            "PYRIT_INFERENCE_RELAY_HOST": "0.0.0.0",
            "PYRIT_INFERENCE_RELAY_ADVERTISED_HOST": "host.docker.internal",
        },
    ):
        try:
            async with AgentExecutionManager(root=tmp_path / "executions") as manager:
                target.bind_execution_manager(manager)
                seed = await normalizer.send_prompt_async(
                    target=target,
                    conversation_id=conversation,
                    message=MessagePiece(
                        role="user",
                        original_value="SEED_CHECKPOINT: create the disposable receipt.",
                        conversation_id=conversation,
                    ).to_message(),
                )
                assert seed.get_piece().original_value == source.history_token
                record = manager.latest_execution(conversation_id=conversation)
                assert record is not None
                script = "const fs=require('fs');fs.unlinkSync('/workspace/orders.json');"
                if workspace_change == "file-to-directory":
                    script += (
                        "fs.mkdirSync('/workspace/orders.json');"
                        "fs.writeFileSync('/workspace/orders.json/child','saved');"
                    )
                await manager._live[record.id].environment._docker_async(
                    "exec", str(record.provider_id), "node", "-e", script
                )
                checkpoint = await manager.save_checkpoint_async(
                    execution_id=record.id, memory=memory, actor="integration"
                )
                assert record.state == AgentExecutionState.CLOSED and not manager.has_live_executions
                before = record.model_dump_json()
                original_journal = await asyncio.to_thread(
                    (manager.store.directory(record.id) / "events.jsonl").read_bytes
                )
                assert (await manager.checkpoint_status_async(record.id)).restore_available
            # Independent runtime owner reloads source evidence and private checkpoint from disk.
            async with AgentExecutionManager(root=tmp_path / "executions") as restarted:
                target = AgentTarget(agent_configuration=target.agent_configuration)
                target.bind_execution_manager(restarted)
                binding = resolve_model_binding(target.agent_configuration)
                assert binding is not None
                source.phase = "paired"
                calls_before = len(source.calls)
                restored = await restarted.restore_checkpoint_async(
                    checkpoint_id=checkpoint.id,
                    profile=target.profile,
                    target_id=target.get_identifier().hash,
                    memory=memory,
                    model_binding=binding,
                )
                assert len(source.calls) == calls_before
                assert restored.id != record.id and restored.session_id == checkpoint.session_id
                assert restored.turns == []
                verify = (
                    "if(require('fs').existsSync('/workspace/orders.json'))process.exit(2)"
                    if workspace_change == "deleted"
                    else (
                        "if(require('fs').readFileSync('/workspace/orders.json/child','utf8')!=='saved')process.exit(2)"
                    )
                )
                await restarted._live[restored.id].environment._docker_async(
                    "exec", str(restored.provider_id), "node", "-e", verify
                )
                prompt = MessagePiece(
                    role="user",
                    original_value="CONTINUE_CHECKPOINT: advance the previous receipt once.",
                    conversation_id=conversation,
                )
                reply = await normalizer.send_prompt_async(
                    target=target, message=prompt.to_message(), conversation_id=conversation
                )
                assert reply.get_piece().original_value == "CONTINUATION_VERIFIED"
                assert restarted.latest_execution(conversation_id=conversation).id == restored.id
                next_checkpoint = await restarted.save_checkpoint_async(
                    execution_id=restored.id,
                    memory=memory,
                    actor="integration-second-save",
                )
                assert next_checkpoint.source_execution_id == restored.id
                for name, expected in (
                    ("receipt.json", '{"total":36,"writes":2}'),
                    ("effects.log", "seed\ncontinued\n"),
                ):
                    actual = await asyncio.to_thread(
                        (restarted.store.directory(restored.id) / "artifacts" / name).read_text
                    )
                    assert actual == expected
                assert restarted.records[record.id].model_dump_json() == before
                assert (
                    await asyncio.to_thread((restarted.store.directory(record.id) / "events.jsonl").read_bytes)
                    == original_journal
                )
                page = await restarted.store.events_async(execution_id=restored.id, limit=500)
                replayed = [event for event in page.events if event.direction == "restore"]
                assert replayed and all(event.turn_id is None for event in replayed)
                outgoing = [event.payload for event in page.events if event.direction == "outgoing"]
                assert not any(event.get("method") == "session/new" for event in outgoing)
                assert len([event for event in outgoing if event.get("method") == "session/prompt"]) == 1
                with pytest.raises(ValueError, match="latest saved"):
                    await restarted.restore_checkpoint_async(
                        checkpoint_id=checkpoint.id,
                        profile=target.profile,
                        target_id=target.get_identifier().hash,
                        memory=memory,
                        model_binding=binding,
                    )
                await restarted.delete_checkpoint_async(checkpoint.id)
                await restarted.delete_checkpoint_async(next_checkpoint.id)
                assert not (await restarted.checkpoint_status_async(record.id)).restore_available
                assert not restarted.has_live_executions
                await asyncio.to_thread(
                    (tmp_path / "runtime-proof.json").write_text,
                    json.dumps(
                        {
                            "harness": harness,
                            "image_id": checkpoint.image_id,
                            "source": str(record.id),
                            "restored": str(restored.id),
                            "replayed_notifications": len(replayed),
                            "native_history_and_files_verified": True,
                            "source_evidence_unchanged": True,
                            "deleted_checkpoint": True,
                        },
                        indent=2,
                    ),
                )
        finally:
            registry.instances.unregister(source_name)
            await source.cleanup_target_async()
