# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A real ACP harness as a PyRIT target, without replacing its internal tool loop."""

from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.models import ComponentIdentifier, Message, construct_response_from_request
from pyrit.models.agent_execution import AgentTargetConfiguration, AgentWorkspaceBase, legacy_harness_kind
from pyrit.models.target_response import TargetOutcome, TargetResponse
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

if TYPE_CHECKING:
    from pyrit.agent.execution_manager import AgentExecutionManager


class AgentTarget(PromptTarget):
    """Send each new user turn to a fresh-per-conversation ACP execution."""

    requires_admin_creation: ClassVar[bool] = True

    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            output_modalities=frozenset(
                {
                    frozenset({"text"}),
                    frozenset({"function_call"}),
                    frozenset({"function_call_output"}),
                }
            ),
        )
    )

    def __init__(
        self,
        *,
        agent_configuration: AgentTargetConfiguration | dict[str, Any],
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Validate a recipe without launching a process or container.

        Raises:
            ValueError: The configured capabilities cannot be supported by ACP.
        """
        self.agent_configuration = (
            agent_configuration
            if isinstance(agent_configuration, AgentTargetConfiguration)
            else AgentTargetConfiguration.model_validate(agent_configuration)
        )
        # The flattened profile is the persisted execution shape; identity derives from it.
        self.profile = self.agent_configuration.to_profile()
        from pyrit.agent.environment import validate_environment_template

        validate_environment_template(self.agent_configuration.environment_template)
        super().__init__(model_name=self.profile.model, custom_configuration=custom_configuration)
        if self.capabilities.supports_editable_history or self.capabilities.supports_system_prompt:
            raise ValueError("ACP AgentTarget does not support editable history or system prompts")
        self._execution_manager: AgentExecutionManager | None = None

    def bind_execution_manager(self, manager: "AgentExecutionManager") -> None:
        """
        Use an explicitly owned manager before any sends.

        Raises:
            RuntimeError: A manager is already bound.
        """
        if self._execution_manager is not None:
            raise RuntimeError("Execution manager is already bound")
        self._execution_manager = manager

    def _manager(self) -> "AgentExecutionManager":
        if self._execution_manager is None:
            from pyrit.agent.runtime import get_agent_execution_manager

            self._execution_manager = get_agent_execution_manager()
        return self._execution_manager

    def _build_identifier(self) -> ComponentIdentifier:
        values = self.profile.model_dump(mode="json")
        if self.profile.harness_kind == legacy_harness_kind(self.profile.command):
            values.pop("harness_kind")
        if not self.profile.environment_settings:
            values.pop("environment_settings")
        original_base = AgentWorkspaceBase.IMAGE if self.profile.environment == "docker" else AgentWorkspaceBase.EMPTY
        if self.profile.workspace_base is None or self.profile.workspace_base == original_base:
            values.pop("workspace_base")
        if self.profile.working_directory == ".":
            values.pop("working_directory")
        return self._create_identifier(params={"profile": values})

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> TargetResponse:
        """
        Send only the current user message; never replay tool-producing history.

        Returns:
            TargetResponse: Explicit terminal outcome and any agent text.

        Raises:
            ValueError: The request is not user text.
        """
        request = normalized_conversation[-1].get_piece()
        if request.role != "user" or request.converted_value_data_type != "text":
            raise ValueError("ACP MVP accepts user text only")
        record, turn = await self._manager().send_async(
            profile=self.profile,
            target_id=self.get_identifier().hash,
            conversation_id=str(request.conversation_id),
            request_id=str(request.id),
            prompt=request.converted_value,
            has_history=len(normalized_conversation) > 1,
        )
        metadata = {
            "agent_execution_id": str(record.id),
            "agent_turn_id": str(turn.id),
            "agent_stop_reason": turn.stop_reason,
            "agent_capture_complete": turn.capture_complete,
        }
        messages: list[Message] = []
        if turn.response_text:
            response = construct_response_from_request(
                request=request, response_text_pieces=[turn.response_text], response_type="text"
            )
            response.get_piece().prompt_metadata.update(metadata)
            messages.append(response)
        return TargetResponse(messages=messages, outcome=TargetOutcome(turn.status.value), metadata=metadata)

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """Release attack-owned resources without constructing a manager during cleanup."""
        if self._execution_manager:
            await self._execution_manager.close_conversation_async(
                target_id=self.get_identifier().hash, conversation_id=conversation_id
            )

    async def cleanup_target_async(self) -> None:
        """Close only this target's executions, not other targets sharing the manager."""
        if self._execution_manager:
            for record in tuple(self._execution_manager.records.values()):
                if record.target_id == self.get_identifier().hash:
                    await self._execution_manager.close_execution_async(execution_id=record.id)
