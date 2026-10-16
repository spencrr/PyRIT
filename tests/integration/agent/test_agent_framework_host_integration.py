# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A real Agent Framework agent, hosted over ACP, driven by synthetic BYOK inference through the relay."""

import json
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("acp")
pytest.importorskip("agent_framework")

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import (
    AgentTargetConfiguration,
    AgentTurnStatus,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.models.messages.tool_content import FunctionCallContent
from pyrit.models.model_inference import InferenceCapabilities, InferenceRequirements, InferenceWireApi
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget, TextTarget
from pyrit.prompt_target.common.model_inference import InferenceResponse
from pyrit.registry import TargetRegistry

pytestmark = [pytest.mark.run_only_if_all_tests, pytest.mark.usefixtures("patch_central_database")]


class WriteFileOracle(TextTarget):
    """Synthetic Chat Completions model: ask the agent's write_file tool for a receipt, then confirm it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    @property
    def inference_capabilities(self) -> InferenceCapabilities:
        return InferenceCapabilities(
            wire_apis=frozenset({InferenceWireApi.CHAT_COMPLETIONS}),
            streaming=True,
            tool_calls=True,
            input_modalities=frozenset({"text"}),
        )

    def _reply(self, body: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        messages = body.get("messages", [])
        tools = {tool.get("function", {}).get("name") for tool in body.get("tools", [])}
        if "write_file" not in tools:
            raise ValueError(f"Agent did not advertise write_file; saw {sorted(filter(None, tools))}")
        if any(message.get("role") == "tool" for message in messages):
            return [{"role": "assistant", "content": "Receipt total: 31"}], "stop"
        # Stream the call the way real providers do: name with empty arguments, then argument fragments.
        arguments = json.dumps({"path": "receipt.json", "content": json.dumps({"total": 31})})
        first = {
            "index": 0,
            "id": "call-receipt",
            "type": "function",
            "function": {"name": "write_file", "arguments": ""},
        }
        deltas: list[dict[str, Any]] = [{"role": "assistant", "tool_calls": [first]}]
        deltas.extend(
            {"tool_calls": [{"index": 0, "function": {"arguments": arguments[start : start + 9]}}]}
            for start in range(0, len(arguments), 9)
        )
        return deltas, "tool_calls"

    @asynccontextmanager
    async def open_inference_async(
        self, *, body: dict[str, Any], requirements: InferenceRequirements, request_id: str
    ) -> AsyncGenerator[InferenceResponse, None]:
        self.calls.append(body)
        deltas, finish = self._reply(body)

        async def chunks_async() -> AsyncIterator[bytes]:
            for content, reason in (*((delta, None) for delta in deltas), ({}, finish)):
                payload = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "oracle",
                    "choices": [{"index": 0, "delta": content, "finish_reason": reason}],
                }
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        yield InferenceResponse(status_code=200, headers={"content-type": "text/event-stream"}, body=chunks_async())


async def test_default_agent_framework_agent_runs_a_tool_through_the_relay_async(tmp_path: Path) -> None:
    oracle = WriteFileOracle()
    source_name = f"oracle_{uuid4().hex}"
    TargetRegistry.get_registry_singleton().instances.register(oracle, name=source_name)
    target = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            model_binding=ModelBinding(model="oracle-model", target_registry_name=source_name),
            harness_profile=HarnessProfile(
                harness_kind="agent_framework",
                command=(sys.executable, "-m", "pyrit.agent.hosts.agent_framework_acp"),
                permission_policy="allow_once",
            ),
            environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True),
            artifact_paths=("receipt.json",),
        )
    )
    conversation = str(uuid4())
    try:
        async with AgentExecutionManager(root=tmp_path / "executions") as manager:
            target.bind_execution_manager(manager)
            response = await PromptNormalizer().send_prompt_async(
                target=target,
                conversation_id=conversation,
                message=MessagePiece(
                    role="user", original_value="Create the receipt.", conversation_id=conversation
                ).to_message(),
            )
            assert response.get_piece().original_value == "Receipt total: 31"
            record = manager.latest_execution(conversation_id=conversation)
            assert record is not None
            turn = record.turns[-1]
            assert turn.status == AgentTurnStatus.COMPLETED and turn.tool_evidence_messages == 2
            projection = await manager.tool_evidence_async(execution_id=record.id, turn_id=turn.id)
            call = FunctionCallContent.model_validate_json(projection.messages[0].get_piece().original_value)
            assert call.name == "write_file"
            assert json.loads(call.arguments) == {"path": "receipt.json", "content": json.dumps({"total": 31})}
            assert len(oracle.calls) == 2 and all(body["stream"] is True for body in oracle.calls)
            lease = manager._live[record.id].inference_lease
            assert lease is not None
            await manager.close_execution_async(execution_id=record.id)
            receipt = (manager.store.directory(record.id) / "artifacts" / "receipt.json").read_text()
            assert json.loads(receipt) == {"total": 31}
            directory = manager.store.directory(record.id)
            for name in ("events.jsonl", "record.json"):
                assert lease.token not in (directory / name).read_text(encoding="utf-8")
    finally:
        await oracle.cleanup_target_async()
        CentralMemory.get_memory_instance()
