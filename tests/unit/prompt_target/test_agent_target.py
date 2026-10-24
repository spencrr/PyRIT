# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.agent.tool_calls import AgentToolProjection
from pyrit.exceptions import TargetResponseUnavailableError
from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import (
    AgentExecution,
    AgentTargetConfiguration,
    AgentTurn,
    AgentTurnStatus,
    EnvironmentTemplate,
    ModelBinding,
)
from pyrit.models.target_response import TargetOutcome, TargetResponse
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry


@pytest.mark.usefixtures("patch_central_database")
class TestAgentTarget:
    def test_composed_configuration_preserves_identity_and_registry_projection(self) -> None:
        from pyrit.backend.mappers.target_mappers import target_object_to_instance

        configuration = AgentTargetConfiguration(
            model_binding=ModelBinding(model="test-model"),
            environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True),
        )
        old = AgentTarget(agent_configuration=configuration)
        new = TargetRegistry.get_registry_singleton().create_instance(
            "AgentTarget", agent_configuration=configuration.model_dump(mode="json")
        )
        assert new.get_identifier().hash == old.get_identifier().hash
        view = target_object_to_instance("composed", new)
        assert view.identifier.hash == old.get_identifier().hash
        assert view.capabilities.supports_multi_turn

    def test_registry_builds_configuration_without_launch(self) -> None:
        target = TargetRegistry.get_registry_singleton().create_instance_from_external_input(
            "AgentTarget",
            params={
                "agent_configuration": {
                    "environment_template": {"environment": "local", "local_execution_acknowledged": True}
                }
            },
        )
        assert isinstance(target, AgentTarget)
        assert target.profile.environment == "local"
        assert not target.capabilities.supports_editable_history

    async def test_tool_only_result_is_not_write_only_success(self) -> None:
        target = AgentTarget(
            agent_configuration={"environment_template": {"environment": "local", "local_execution_acknowledged": True}}
        )
        target._send_prompt_to_target_async = AsyncMock(
            return_value=TargetResponse(
                outcome=TargetOutcome.COMPLETED,
                metadata={"agent_execution_id": "execution"},
            )
        )
        request = MessagePiece(role="user", original_value="Create a file").to_message()
        with pytest.raises(TargetResponseUnavailableError, match="no completed scorable response"):
            await PromptNormalizer().send_prompt_async(message=request, target=target, conversation_id="one")
        messages = await CentralMemory.get_memory_instance().get_conversation_messages_async(conversation_id="one")
        assert len(messages) == 1
        assert messages[0].get_piece().prompt_metadata["target_outcome"] == "completed"
        assert not messages[0].get_piece().has_error()

    async def test_only_current_prompt_is_dispatched(self) -> None:
        from pyrit.agent.execution_manager import AgentExecutionManager

        configuration = AgentTargetConfiguration(
            environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
        )
        manager = MagicMock(spec=AgentExecutionManager)
        manager.tool_calls_async = AsyncMock(return_value=AgentToolProjection())
        manager.send_async = AsyncMock(
            return_value=(
                AgentExecution(
                    owner_id="test", conversation_id="one", target_id="target", profile=configuration.to_profile()
                ),
                AgentTurn(request_id="1", prompt="next", response_text="done", status=AgentTurnStatus.COMPLETED),
            )
        )
        target = AgentTarget(agent_configuration=configuration)
        target.bind_execution_manager(manager)
        await target._send_prompt_to_target_async(
            normalized_conversation=[
                MessagePiece(role="user", original_value="old", conversation_id="one").to_message(),
                MessagePiece(role="user", original_value="next", conversation_id="one").to_message(),
            ]
        )
        assert manager.send_async.call_args.kwargs["prompt"] == "next"
        assert manager.send_async.call_args.kwargs["has_history"] is True
        assert manager.send_async.call_args.kwargs["configuration"] == configuration

    async def test_turn_errors_travel_with_the_explicit_outcome(self) -> None:
        from pyrit.agent.execution_manager import AgentExecutionManager

        configuration = AgentTargetConfiguration(
            environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
        )
        manager = MagicMock(spec=AgentExecutionManager)
        manager.tool_calls_async = AsyncMock(return_value=AgentToolProjection())
        manager.send_async = AsyncMock(
            return_value=(
                AgentExecution(
                    owner_id="test", conversation_id="one", target_id="target", profile=configuration.to_profile()
                ),
                AgentTurn(
                    request_id="1",
                    prompt="slow",
                    response_text="Partial",
                    status=AgentTurnStatus.CANCELLED,
                    stop_reason="cancelled",
                    error="Turn deadline exceeded",
                ),
            )
        )
        target = AgentTarget(agent_configuration=configuration)
        target.bind_execution_manager(manager)
        response = await target._send_prompt_to_target_async(
            normalized_conversation=[
                MessagePiece(role="user", original_value="slow", conversation_id="one").to_message()
            ]
        )
        assert response.outcome is TargetOutcome.CANCELLED
        assert response.metadata["agent_turn_error"] == "Turn deadline exceeded"
        assert response[0].get_piece().converted_value == "Partial"

    def test_fixture_directory_is_confined_when_the_server_supplies_a_root(self, tmp_path: Path) -> None:
        (tmp_path / "fixtures" / "receipt").mkdir(parents=True)
        template = EnvironmentTemplate(
            environment="local", local_execution_acknowledged=True, fixture_directory="receipt"
        )
        target = AgentTarget(
            agent_configuration=AgentTargetConfiguration(environment_template=template),
            allowed_fixture_directory=tmp_path / "fixtures",
        )
        assert target.profile.fixture_directory == str((tmp_path / "fixtures" / "receipt").resolve())
        for escape in ("../secrets", str(tmp_path), "C:/Windows" if Path("C:/").exists() else "/etc"):
            with pytest.raises(ValueError, match="outside the allowed directory"):
                AgentTarget(
                    agent_configuration=AgentTargetConfiguration(
                        environment_template=template.model_copy(update={"fixture_directory": escape})
                    ),
                    allowed_fixture_directory=tmp_path / "fixtures",
                )
        with pytest.raises(ValueError, match="does not exist"):
            AgentTarget(
                agent_configuration=AgentTargetConfiguration(environment_template=template),
                allowed_fixture_directory=tmp_path / "missing",
            )
        # Python callers that supply no root keep any path, resolved by the provider at launch.
        unconfined = AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                environment_template=template.model_copy(update={"fixture_directory": str(tmp_path)})
            )
        )
        assert unconfined.profile.fixture_directory == str(tmp_path)
        assert AgentTarget.upload_directory_parameter == "allowed_fixture_directory"
