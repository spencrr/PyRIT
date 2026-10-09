# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentProfile,
    AgentTargetConfiguration,
    AgentTurn,
    AgentTurnStatus,
)
from pyrit.models.target_response import TargetResponse, TargetResponseStatus
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_normalizer.target_response_unavailable import TargetResponseUnavailableError
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry


@pytest.mark.usefixtures("patch_central_database")
class TestAgentTarget:
    def test_composed_configuration_preserves_identity_and_registry_projection(self) -> None:
        from pyrit.backend.mappers.target_mappers import target_object_to_instance

        legacy = AgentProfile(environment="local", local_execution_acknowledged=True, model="test-model")
        configuration = AgentTargetConfiguration.from_profile(legacy)
        old = AgentTarget(profile=legacy)
        new = TargetRegistry.get_registry_singleton().create_instance(
            "AgentTarget", agent_configuration=configuration.model_dump(mode="json")
        )
        assert new.get_identifier().hash == old.get_identifier().hash
        view = target_object_to_instance("composed", new)
        assert view.agent_configuration == configuration
        assert view.model_dump()["agent_configuration"]["model_binding"]["model"] == "test-model"

    def test_rejects_ambiguous_configuration(self) -> None:
        configuration = AgentTargetConfiguration.from_profile(
            AgentProfile(environment="local", local_execution_acknowledged=True)
        )
        with pytest.raises(ValueError, match="not both"):
            AgentTarget(agent_configuration=configuration, profile=configuration.to_profile())

    def test_registry_builds_profile_without_launch(self) -> None:
        target = TargetRegistry.get_registry_singleton().create_instance(
            "AgentTarget", profile={"environment": "local", "local_execution_acknowledged": True}
        )
        assert isinstance(target, AgentTarget)
        assert target.profile.environment == "local"
        assert not target.capabilities.supports_editable_history

    async def test_tool_only_result_is_not_write_only_success(self) -> None:
        target = AgentTarget(profile={"environment": "local", "local_execution_acknowledged": True})
        target._send_prompt_to_target_async = AsyncMock(
            return_value=TargetResponse(
                status=TargetResponseStatus.COMPLETED,
                metadata={"agent_execution_id": "execution"},
            )
        )
        request = MessagePiece(role="user", original_value="Create a file").to_message()
        with pytest.raises(TargetResponseUnavailableError, match="no completed scorable response"):
            await PromptNormalizer().send_prompt_async(message=request, target=target, conversation_id="one")
        messages = await CentralMemory.get_memory_instance().get_conversation_messages_async(conversation_id="one")
        assert len(messages) == 1
        assert messages[0].get_piece().prompt_metadata["target_response_status"] == "completed"
        assert not messages[0].get_piece().has_error()

    async def test_only_current_prompt_is_dispatched(self) -> None:
        from pyrit.agent.execution_manager import AgentExecutionManager

        profile = AgentProfile(environment="local", local_execution_acknowledged=True)
        manager = MagicMock(spec=AgentExecutionManager)
        manager.send_async = AsyncMock(
            return_value=(
                AgentExecution(owner_id="test", conversation_id="one", target_id="target", profile=profile),
                AgentTurn(request_id="1", prompt="next", response_text="done", status=AgentTurnStatus.COMPLETED),
            )
        )
        target = AgentTarget(profile=profile)
        target.bind_execution_manager(manager)
        await target._send_prompt_to_target_async(
            normalized_conversation=[
                MessagePiece(role="user", original_value="old", conversation_id="one").to_message(),
                MessagePiece(role="user", original_value="next", conversation_id="one").to_message(),
            ]
        )
        assert manager.send_async.call_args.kwargs["prompt"] == "next"
        assert manager.send_async.call_args.kwargs["has_history"] is True
