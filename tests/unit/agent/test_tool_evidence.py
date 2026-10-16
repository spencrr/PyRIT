# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.tool_evidence import TOOL_NAME_META_KEY, project_agent_tools
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    MessagePiece,
    MessageScorable,
    ScoreStatus,
    ScoringExpectation,
    ToolCallRequirement,
    ToolExecutionMetadata,
    ToolsCalled,
)
from pyrit.models.agent_execution import AgentExecution, AgentExecutionEvent, AgentProfile, AgentTurn, AgentTurnStatus
from pyrit.models.messages.tool_content import FunctionCallContent, validate_tool_conversation
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget
from pyrit.score import MessageToolCallScorer


def tool_events(
    *,
    execution_id: UUID,
    turn_id: UUID,
    name: str | None = "lookup",
    status: str = "completed",
    started: bool = True,
    output: bool = True,
) -> list[AgentExecutionEvent]:
    first: dict[str, Any] = {
        "sessionUpdate": "tool_call",
        "toolCallId": "call-1",
        "title": "lookup",
        "rawInput": {"q": "test"},
        "status": "in_progress" if started else "pending",
    }
    if name is not None:
        first["name"] = name
    last: dict[str, Any] = {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": status}
    if output:
        last["rawOutput"] = {"value": 31, "error": "actual tool error" if status == "failed" else None}
    return [
        AgentExecutionEvent(
            execution_id=execution_id,
            turn_id=turn_id,
            sequence=i + 1,
            direction="incoming",
            payload={"method": "session/update", "params": {"update": update}},
        )
        for i, update in enumerate([first, last])
    ]


@pytest.mark.parametrize(
    ("name", "status", "started", "output", "count", "reason"),
    [
        ("lookup", "completed", True, True, 2, None),
        ("lookup", "failed", True, True, 2, None),
        ("lookup", "failed", False, True, 1, "invocation_or_result_not_established"),
        ("lookup", "pending", False, False, 1, "invocation_or_result_not_established"),
        ("lookup", "completed", True, False, 1, "invocation_or_result_not_established"),
        (None, "completed", True, True, 0, "name_not_reported"),
    ],
)
def test_projects_only_supported_evidence(
    *,
    name: str | None,
    status: str,
    started: bool,
    output: bool,
    count: int,
    reason: str | None,
) -> None:
    execution_id, turn_id = uuid4(), uuid4()
    projection = project_agent_tools(
        events=tool_events(
            execution_id=execution_id, turn_id=turn_id, name=name, status=status, started=started, output=output
        ),
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert len(projection.messages) == count
    assert projection.omitted == ({"call-1": reason} if reason else {})
    validate_tool_conversation(projection.messages)
    if count == 2:
        piece = projection.messages[-1].get_piece()
        assert ToolExecutionMetadata.from_metadata(metadata=piece.prompt_metadata).invoked
        assert piece.prompt_metadata["agent_tool_evidence"]["event_sequence"] == 2


def test_arguments_reported_on_the_final_update_are_accepted() -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id)
    first, last = (event.payload["params"]["update"] for event in events)
    del first["rawInput"]
    last["rawInput"] = {"path": "receipt.json"}
    projection = project_agent_tools(events=events, execution_id=execution_id, turn_id=turn_id, conversation_id="one")
    assert not projection.omitted
    call = FunctionCallContent.model_validate_json(projection.messages[0].get_piece().original_value)
    assert json.loads(call.arguments) == {"path": "receipt.json"}


def test_meta_name_identifies_tools_without_inferring_from_title() -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id, name=None)
    first = events[0].payload["params"]["update"]
    first["title"] = "Friendly title that must not be trusted"
    first["_meta"] = {TOOL_NAME_META_KEY: "write_file"}
    projection = project_agent_tools(events=events, execution_id=execution_id, turn_id=turn_id, conversation_id="one")
    assert not projection.omitted
    call = FunctionCallContent.model_validate_json(projection.messages[0].get_piece().original_value)
    assert call.name == "write_file"
    for untrusted in ({"name": "write_file"}, {TOOL_NAME_META_KEY: 7}):
        first["_meta"] = untrusted
        projection = project_agent_tools(
            events=events, execution_id=execution_id, turn_id=turn_id, conversation_id="one"
        )
        assert projection.omitted == {"call-1": "name_not_reported"}


@pytest.mark.parametrize("policy", ["operator_deny", "timeout_deny", "cancelled"])
def test_permission_denial_is_not_invocation(policy: str) -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id)
    events.append(
        AgentExecutionEvent(
            execution_id=execution_id,
            turn_id=turn_id,
            sequence=3,
            direction="lifecycle",
            payload={"type": "permission.resolved", "approval": {"tool_call_id": "call-1", "decision": policy}},
        )
    )
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert not projection.messages
    assert projection.omitted == {"call-1": "permission_not_granted"}


def test_ambiguous_call_ids_and_missing_arguments_remain_explicit() -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id)
    events.append(events[0].model_copy(update={"sequence": 3}))
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert projection.omitted == {"call-1": "ambiguous_call_reports"}
    assert projection.messages == []
    with pytest.raises(ValueError, match="ordered within one"):
        project_agent_tools(events=events[::-1], execution_id=execution_id, turn_id=turn_id, conversation_id="one")
    with pytest.raises(ValueError, match="ordered within one"):
        project_agent_tools(events=events, execution_id=execution_id, turn_id=uuid4(), conversation_id="one")


@pytest.mark.parametrize("output", [None, False, 0, "", [], {}])
def test_explicit_empty_result_is_not_missing(output: Any) -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id, output=False)
    events[-1].payload["params"]["update"]["rawOutput"] = output
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert not projection.omitted
    assert len(projection.messages) == 2
    assert json.loads(projection.messages[-1].get_value())["output"] == output


@pytest.mark.parametrize("rpc_error", [False, True])
def test_automatic_denial_before_tool_report_is_retained(rpc_error: bool) -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = [
        AgentExecutionEvent(
            execution_id=execution_id,
            turn_id=turn_id,
            sequence=1,
            direction="incoming",
            payload={
                "id": 42,
                "method": "session/request_permission",
                "params": {
                    "toolCall": {"toolCallId": "call-1"},
                    "options": [{"optionId": "no", "kind": "reject_once"}],
                },
            },
        ),
        AgentExecutionEvent(
            execution_id=execution_id,
            turn_id=turn_id,
            sequence=2,
            direction="outgoing",
            payload={"id": 42, "result": {"outcome": {"outcome": "selected", "optionId": "no"}}},
        ),
        *[
            event.model_copy(update={"sequence": event.sequence + 2})
            for event in tool_events(execution_id=execution_id, turn_id=turn_id)
        ],
    ]
    if rpc_error:
        events[1].payload = {"id": 42, "error": {"code": -32602, "message": "Permission request rejected"}}
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert not projection.messages
    assert projection.omitted == {"call-1": "permission_not_granted"}


def test_execution_after_failure_does_not_retroactively_prove_invocation() -> None:
    execution_id, turn_id = uuid4(), uuid4()
    events = tool_events(execution_id=execution_id, turn_id=turn_id, status="failed", started=False)
    events.append(
        AgentExecutionEvent(
            execution_id=execution_id,
            turn_id=turn_id,
            sequence=3,
            direction="incoming",
            payload={
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call-1",
                        "status": "in_progress",
                    }
                },
            },
        )
    )
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert len(projection.messages) == 1
    assert projection.omitted == {"call-1": "invocation_or_result_not_established"}


@pytest.mark.parametrize("result_first", [False, True])
def test_status_and_output_can_be_reported_in_separate_updates(result_first: bool) -> None:
    execution_id, turn_id = uuid4(), uuid4()
    first = tool_events(execution_id=execution_id, turn_id=turn_id)[0]
    updates = [{"status": "completed"}, {"rawOutput": None}]
    if result_first:
        updates.reverse()
    events = [
        first,
        *[
            AgentExecutionEvent(
                execution_id=execution_id,
                turn_id=turn_id,
                sequence=i + 2,
                direction="incoming",
                payload={
                    "method": "session/update",
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-1",
                            **update,
                        }
                    },
                },
            )
            for i, update in enumerate(updates)
        ],
    ]
    projection = project_agent_tools(
        events=events,
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    assert len(projection.messages) == 2
    assert not projection.omitted
    assert projection.messages[-1].get_piece().prompt_metadata["agent_tool_evidence"]["event_sequence"] == 3


@pytest.mark.usefixtures("patch_central_database")
class TestAgentEvidenceIntegration:
    async def prepare_async(
        self,
        *,
        root: Path,
        conversation_id: str,
        name: str | None = "lookup",
    ) -> tuple[AgentExecutionManager, AgentExecution, AgentTurn, AgentTarget]:
        manager = AgentExecutionManager(root=root)
        profile = AgentProfile(environment="local", local_execution_acknowledged=True)
        turn = AgentTurn(request_id="one", prompt="lookup", status=AgentTurnStatus.COMPLETED)
        target = AgentTarget(profile=profile)
        record = AgentExecution(
            owner_id="test",
            conversation_id=conversation_id,
            target_id=target.get_identifier().hash,
            profile=profile,
            turns=[turn],
        )
        manager.records[record.id] = record
        await manager.store.save_async(record)
        for event in tool_events(execution_id=record.id, turn_id=turn.id, name=name):
            await manager.store.append_async(event)
        target.bind_execution_manager(manager)
        return manager, record, turn, target

    async def test_tool_only_completion_reaches_existing_scorer_async(
        self,
        tmp_path: Path,
        sqlite_instance: SQLiteMemory,
    ) -> None:
        manager, record, turn, target = await self.prepare_async(root=tmp_path, conversation_id="pending")

        async def send_async(**kwargs: Any) -> tuple[AgentExecution, AgentTurn]:
            record.conversation_id = kwargs["conversation_id"]
            return record, turn

        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
            max_attempts_on_failure=0,
        )
        expectation = ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="lookup"),)),))
        with patch.object(manager, "send_async", side_effect=send_async):
            result = await attack.execute_async(objective="look it up", expectation=expectation)
        assert result.outcome is AttackOutcome.SUCCESS
        assert result.last_response.role == "tool"
        assert result.automated_score.get_value() is True
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=record.conversation_id)
        assert [piece.original_value_data_type for piece in pieces] == ["text", "function_call", "function_call_output"]

    async def test_second_turn_keeps_evidence_without_replay_and_prefix_scores_async(
        self,
        tmp_path: Path,
        sqlite_instance: SQLiteMemory,
    ) -> None:
        conversation_id = str(uuid4())
        manager, record, turn, target = await self.prepare_async(root=tmp_path, conversation_id=conversation_id)
        turn.response_text = "first"
        normalizer = PromptNormalizer()
        with patch.object(manager, "send_async", new_callable=AsyncMock, return_value=(record, turn)) as send:
            first = await normalizer.send_prompt_async(
                message=MessagePiece(role="user", original_value="first").to_message(),
                target=target,
                conversation_id=conversation_id,
            )
            next_turn = AgentTurn(
                request_id="two", prompt="second", response_text="second", status=AgentTurnStatus.COMPLETED
            )
            record.turns.append(next_turn)
            for event in tool_events(execution_id=record.id, turn_id=next_turn.id, name="write"):
                await manager.store.append_async(event.model_copy(update={"sequence": event.sequence + 2}))
            send.return_value = (record, next_turn)
            await normalizer.send_prompt_async(
                message=MessagePiece(role="user", original_value="second").to_message(),
                target=target,
                conversation_id=conversation_id,
            )
            assert send.call_args.kwargs["prompt"] == "second"
            assert send.call_args.kwargs["has_history"]
        assert first is not None
        scores = await MessageToolCallScorer().score_async(
            scorable=MessageScorable.from_message(first),
            expectation=ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="write"),)),)),
        )
        assert scores[0].status is ScoreStatus.UNDETERMINED
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
        validate_tool_conversation(messages)
        ids = [
            json.loads(message.get_value())["call_id"]
            for message in messages
            if message.get_piece().original_value_data_type == "function_call"
        ]
        assert len(set(ids)) == 2

    @pytest.mark.parametrize("change", ["role", "value", "conversation"])
    async def test_native_evidence_cannot_be_modified_or_copied_async(
        self,
        tmp_path: Path,
        change: str,
    ) -> None:
        conversation_id = str(uuid4())
        manager, record, turn, target = await self.prepare_async(root=tmp_path, conversation_id=conversation_id)
        projection = await manager.tool_evidence_async(execution_id=record.id, turn_id=turn.id)
        messages = projection.messages
        piece = messages[0].get_piece()
        if change == "role":
            piece.role = "simulated_assistant"
        elif change == "value":
            piece.converted_value = "{}"
        else:
            record.conversation_id = str(uuid4())
        messages.append(MessagePiece(role="user", original_value="next", conversation_id=conversation_id).to_message())
        with (
            patch(
                "pyrit.prompt_target.common.prompt_target.PromptTarget._get_normalized_conversation_async",
                new_callable=AsyncMock,
                return_value=messages,
            ),
            pytest.raises(ValueError, match="modified|Copied"),
        ):
            await target._get_normalized_conversation_async(message=messages[-1])

    async def test_terminal_evidence_does_not_change_when_late_reports_arrive_async(self, tmp_path: Path) -> None:
        manager, record, turn, _ = await self.prepare_async(root=tmp_path, conversation_id="one")
        turn.event_end_cursor = 2
        before = await manager.tool_evidence_async(execution_id=record.id, turn_id=turn.id)
        for event in tool_events(execution_id=record.id, turn_id=turn.id, name="late_tool"):
            await manager.store.append_async(event.model_copy(update={"sequence": event.sequence + 2}))
        after = await manager.tool_evidence_async(execution_id=record.id, turn_id=turn.id)
        assert [message.get_value() for message in after.messages] == [
            message.get_value() for message in before.messages
        ]
        assert not after.omitted

    async def test_equivalent_registry_target_can_continue_an_existing_execution_async(
        self,
        tmp_path: Path,
        sqlite_instance: SQLiteMemory,
    ) -> None:
        conversation_id = str(uuid4())
        manager, record, turn, target = await self.prepare_async(root=tmp_path, conversation_id=conversation_id)
        normalizer = PromptNormalizer()
        with patch.object(manager, "send_async", new_callable=AsyncMock, return_value=(record, turn)):
            await normalizer.send_prompt_async(
                message=MessagePiece(role="user", original_value="first").to_message(),
                target=target,
                conversation_id=conversation_id,
            )
        replacement = AgentTarget(profile=target.profile)
        assert replacement.get_identifier().hash == target.get_identifier().hash
        next_turn = AgentTurn(
            request_id="two", prompt="second", response_text="continued", status=AgentTurnStatus.COMPLETED
        )
        record.turns.append(next_turn)
        with (
            patch("pyrit.agent.runtime.get_agent_execution_manager", return_value=manager),
            patch.object(manager, "send_async", new_callable=AsyncMock, return_value=(record, next_turn)) as send,
        ):
            response = await normalizer.send_prompt_async(
                message=MessagePiece(role="user", original_value="second").to_message(),
                target=replacement,
                conversation_id=conversation_id,
            )
        assert response.get_value() == "continued"
        assert send.call_args.kwargs["has_history"]
        assert replacement._execution_manager is manager
