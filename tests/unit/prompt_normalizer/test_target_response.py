# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.memory import SQLiteMemory
from pyrit.models import Message, MessagePiece
from pyrit.models.target_response import TargetOutcome, TargetResponse
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_normalizer.target_response_unavailable import TargetResponseUnavailableError


def _reply(text: str) -> Message:
    return MessagePiece(role="assistant", original_value=text).to_message()


async def _send(*, target: MockPromptTarget, response: object) -> Message:
    with patch.object(target, "_send_prompt_to_target_async", AsyncMock(return_value=response)):
        return await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="hello", role="user"), target=target
        )


async def test_completed_outcome_with_text_behaves_like_a_message_list(sqlite_instance: SQLiteMemory) -> None:
    target = MockPromptTarget()
    outcome = TargetResponse(messages=[_reply("done")], outcome=TargetOutcome.COMPLETED, metadata={"run": "r1"})

    reply = await _send(target=target, response=outcome)

    assert reply.get_piece().converted_value == "done"
    conversation_id = reply.get_piece().conversation_id
    stored = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    assert [piece.role for piece in (message.get_piece() for message in stored)] == ["user", "assistant"]
    request = stored[0].get_piece()
    assert request.prompt_metadata["target_outcome"] == "completed"
    assert request.prompt_metadata["run"] == "r1"
    assert not stored[-1].get_piece().has_error()


@pytest.mark.parametrize("status", [TargetOutcome.COMPLETED, TargetOutcome.FAILED, TargetOutcome.UNKNOWN])
async def test_outcome_without_messages_is_recorded_and_raised(
    sqlite_instance: SQLiteMemory, status: TargetOutcome
) -> None:
    target = MockPromptTarget()
    outcome = TargetResponse(outcome=status, metadata={"run": "r2"})

    with pytest.raises(TargetResponseUnavailableError) as caught:
        await _send(target=target, response=outcome)

    assert caught.value.outcome is outcome
    assert status.value in str(caught.value)
    stored = await sqlite_instance.get_message_pieces_async(role="user")
    assert len(stored) == 1
    assert stored[0].prompt_metadata["target_outcome"] == status.value
    assert stored[0].prompt_metadata["run"] == "r2"
    assert not stored[0].has_error()
    assert await sqlite_instance.get_message_pieces_async(role="assistant") == []


async def test_cancelled_partial_reply_is_retained_but_not_returned(sqlite_instance: SQLiteMemory) -> None:
    target = MockPromptTarget()
    outcome = TargetResponse(messages=[_reply("partial")], outcome=TargetOutcome.CANCELLED)

    with pytest.raises(TargetResponseUnavailableError):
        await _send(target=target, response=outcome)

    [partial] = await sqlite_instance.get_message_pieces_async(role="assistant")
    assert partial.converted_value == "partial"
    assert partial.prompt_metadata["target_outcome"] == "cancelled"
    assert not partial.has_error()
    [request] = await sqlite_instance.get_message_pieces_async(role="user")
    assert partial.conversation_id == request.conversation_id


async def test_plain_message_lists_are_unchanged(sqlite_instance: SQLiteMemory) -> None:
    target = MockPromptTarget()

    reply = await _send(target=target, response=[_reply("legacy")])

    assert reply.get_piece().converted_value == "legacy"
    [request] = await sqlite_instance.get_message_pieces_async(role="user")
    assert "target_outcome" not in request.prompt_metadata


async def test_base_target_returns_the_outcome_object(patch_central_database: None) -> None:
    target = MockPromptTarget()
    outcome = TargetResponse(messages=[_reply("done")], outcome=TargetOutcome.COMPLETED, metadata={"run": "r3"})

    with patch.object(target, "_send_prompt_to_target_async", AsyncMock(return_value=outcome)):
        response = await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))

    assert isinstance(response, TargetResponse)
    assert response.outcome is TargetOutcome.COMPLETED
    assert response.metadata == {"run": "r3"}
    assert [message.get_piece().converted_value for message in response] == ["done"]
