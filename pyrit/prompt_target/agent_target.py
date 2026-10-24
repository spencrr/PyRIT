# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A real ACP harness as a PyRIT target, without replacing its internal tool loop."""

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.models import ComponentIdentifier, Message, construct_response_from_request
from pyrit.models.agent_execution import AgentTargetConfiguration
from pyrit.models.target.agent_tool_call import AgentToolCallProvenance
from pyrit.models.target_response import TargetOutcome, TargetResponse
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName, TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_send_context import TargetSendContext

if TYPE_CHECKING:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.message_normalizer import MessageListNormalizer


class AgentTarget(PromptTarget):
    """Send each new user turn to a fresh-per-conversation ACP execution."""

    requires_admin_creation: ClassVar[bool] = True
    # Over REST the server supplies this folder and every fixture directory must resolve inside it.
    upload_directory_parameter: ClassVar[str | None] = "allowed_fixture_directory"

    # Timeouts and evidence budgets are execution policy, not identity.
    _IDENTITY_EXCLUDED_FIELDS: ClassVar[set[str]] = {
        "startup_timeout_seconds",
        "turn_timeout_seconds",
        "idle_timeout_seconds",
        "lifetime_seconds",
        "cancellation_grace_seconds",
        "max_evidence_bytes",
        "max_artifact_bytes",
    }

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
        allowed_fixture_directory: str | Path | None = None,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Validate a configuration without launching a process or container.

        Args:
            agent_configuration (AgentTargetConfiguration | dict[str, Any]): The composed system under test.
            allowed_fixture_directory (str | Path | None): When set, ``fixture_directory`` must resolve inside
                this folder. The server supplies it for API-created targets; Python callers may omit it.
            custom_configuration (TargetConfiguration | None): Capability overrides, validated against ACP.

        Raises:
            ValueError: The harness profile, environment template, fixture directory, or capabilities are invalid.
        """
        configuration = (
            agent_configuration
            if isinstance(agent_configuration, AgentTargetConfiguration)
            else AgentTargetConfiguration.model_validate(agent_configuration)
        )
        from pyrit.agent.environment import validate_environment_template
        from pyrit.agent.harness import validate_harness_authentication

        validate_harness_authentication(harness=configuration.harness_profile)
        validate_environment_template(configuration.environment_template)
        if allowed_fixture_directory is not None:
            configuration = self._confine_fixture_directory(
                configuration=configuration, allowed_directory=Path(allowed_fixture_directory)
            )
        self.agent_configuration = configuration
        # The flattened profile is the shape persisted with each execution.
        self.profile = configuration.to_profile()
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

    @staticmethod
    def _confine_fixture_directory(
        *, configuration: AgentTargetConfiguration, allowed_directory: Path
    ) -> AgentTargetConfiguration:
        template = configuration.environment_template
        if template.fixture_directory is None:
            return configuration
        root = allowed_directory.resolve()
        if not root.is_dir():
            raise ValueError(f"Allowed fixture directory does not exist or is not a directory: {allowed_directory}")
        resolved = (root / template.fixture_directory).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"fixture_directory is outside the allowed directory: {template.fixture_directory}")
        return configuration.model_copy(
            update={"environment_template": template.model_copy(update={"fixture_directory": str(resolved)})}
        )

    def _build_identifier(self) -> ComponentIdentifier:
        # Defaulted fields are omitted so later optional settings never change existing identities.
        values = self.agent_configuration.model_dump(
            mode="json", exclude_defaults=True, exclude=self._IDENTITY_EXCLUDED_FIELDS
        )
        return self._create_identifier(params={"configuration": values})

    async def _get_normalized_conversation_async(
        self,
        *,
        message: Message,
        normalizer_overrides: Mapping[CapabilityName, "MessageListNormalizer[Message]"] | None = None,
        send_context: TargetSendContext | None = None,
    ) -> list[Message]:
        """
        Remove only verified native execution evidence from the target-facing history.

        Returns:
            list[Message]: Prepared user/history representation without replaying native tool effects.

        Raises:
            ValueError: Recorded tool calls were copied, simulated, or modified.
        """
        messages = await super()._get_normalized_conversation_async(
            message=message,
            normalizer_overrides=normalizer_overrides,
            send_context=send_context,
        )
        expected: dict[str, Message] = {}
        retained: list[Message] = []
        for historical in messages[:-1]:
            refs = [AgentToolCallProvenance.from_metadata(piece.prompt_metadata) for piece in historical.message_pieces]
            if not any(refs):
                retained.append(historical)
                continue
            if len(refs) != 1 or refs[0] is None:
                raise ValueError("Native tool calls must belong to the live agent execution")
            reference = refs[0]
            manager = self._manager()
            # Records are loaded on start; verifying before that would misreport a restart as copied history.
            await manager.start_async()
            record = manager.records.get(reference.execution_id)
            if (
                record is None
                or record.conversation_id != message.get_piece().conversation_id
                or record.target_id != self.get_identifier().hash
            ):
                raise ValueError("Copied tool history cannot restore an agent execution")
            key = str(historical.get_piece().id)
            if key not in expected:
                projection = await manager.tool_calls_async(
                    execution_id=reference.execution_id,
                    turn_id=reference.turn_id,
                )
                expected.update({str(item.get_piece().id): item for item in projection.messages})
            source = expected.get(key)
            piece = historical.get_piece()
            if source is None or (
                piece.role != source.get_piece().role
                or piece.original_value != source.get_piece().original_value
                or piece.original_value_data_type != source.get_piece().original_value_data_type
                or piece.converted_value != source.get_piece().original_value
                or piece.converted_value_data_type != source.get_piece().original_value_data_type
            ):
                raise ValueError("Native tool calls were modified; start a fresh conversation instead")
        return [*retained, messages[-1]]

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
            configuration=self.agent_configuration,
            target_id=self.get_identifier().hash,
            conversation_id=str(request.conversation_id),
            request_id=str(request.id),
            prompt=request.converted_value,
            has_history=len(normalized_conversation) > 1,
        )
        metadata: dict[str, Any] = {
            "agent_execution_id": str(record.id),
            "agent_turn_id": str(turn.id),
            "agent_stop_reason": turn.stop_reason,
            "agent_capture_complete": turn.capture_complete,
        }
        if turn.error:
            metadata["agent_turn_error"] = turn.error
        projection = await self._manager().tool_calls_async(execution_id=record.id, turn_id=turn.id)
        messages = projection.messages
        for item in messages:
            item.get_piece().prompt_metadata.update(metadata)
            item.get_piece().prompt_metadata[TargetResponse.OUTCOME_METADATA_KEY] = turn.status.value
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
