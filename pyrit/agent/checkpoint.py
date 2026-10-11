# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared checkpoint eligibility and transcript binding, without attack/scoring decisions."""

import hashlib
import json
from typing import TYPE_CHECKING

from pyrit.agent.harness import HarnessAdapterRegistry
from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_execution import AgentConnectionState, AgentExecution, AgentExecutionState, AgentTurnStatus

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


def checkpoint_layout(record: AgentExecution) -> HarnessCheckpointLayout:
    """
    Require a tested stopped-filesystem layout and explicitly compatible execution.

    Returns:
        HarnessCheckpointLayout: The installed adapter's exact persistence layout.

    Raises:
        ValueError: The execution or adapter has no supported persistence contract.
    """
    profile = record.profile
    if profile.environment != "docker" or profile.workspace_base == "empty" or profile.working_directory != ".":
        raise ValueError("Checkpoints currently require a Docker image workspace at /workspace")
    if profile.fixture_directory:
        raise ValueError("Checkpoint restoration does not yet support workspace overlays")
    if not profile.target_registry_name or profile.credential_env or profile.authentication_method:
        raise ValueError("Checkpoints currently require BYOK without forwarded credential variables or ACP login")
    adapter = HarnessAdapterRegistry.get_registry_singleton().get(profile.harness_kind)
    layout = adapter.checkpoint_layout
    version = checkpoint_agent_version(record)
    if layout is None or version not in layout.agent_versions:
        raise ValueError("This harness version has no validated checkpoint layout")
    initialize = record.agent_info.get("initialize")
    capabilities = initialize.get("agentCapabilities") if isinstance(initialize, dict) else None
    if not isinstance(capabilities, dict) or capabilities.get("loadSession") is not True:
        raise ValueError("This harness does not advertise ACP session loading")
    if not record.session_id or not record.image_id:
        raise ValueError("Execution has no pinned native session/image identity")
    return layout


def checkpoint_agent_version(record: AgentExecution) -> str:
    """
    Read the advertised version without interpreting arbitrary retained extension data.

    Returns:
        str: The negotiated native agent version.

    Raises:
        ValueError: Version information is absent or malformed.
    """
    initialize = record.agent_info.get("initialize")
    info = initialize.get("agentInfo") if isinstance(initialize, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(version, str) or not version:
        raise ValueError("Execution has no recorded harness version")
    return version


def require_checkpoint_ready(record: AgentExecution) -> HarnessCheckpointLayout:
    """
    Require a completed, fully captured idle session; never checkpoint interrupted work.

    Returns:
        HarnessCheckpointLayout: The supported native-state layout.

    Raises:
        ValueError: The session is not settled and fully captured.
    """
    layout = checkpoint_layout(record)
    if record.state != AgentExecutionState.IDLE or record.connection_state != AgentConnectionState.READY:
        raise ValueError("Save and close requires an idle, connected execution")
    if not record.turns or any(
        turn.status != AgentTurnStatus.COMPLETED or not turn.capture_complete for turn in record.turns
    ):
        raise ValueError("Only fully captured completed turns can be checkpointed")
    if record.capture_error or record.cleanup_error or any(approval.decision is None for approval in record.approvals):
        raise ValueError("Capture errors or unresolved permissions prevent checkpointing")
    return layout


async def checkpoint_transcript_async(
    *,
    memory: "MemoryInterface",
    conversation_id: str,
    executions: list[AgentExecution] | None = None,
) -> str:
    """
    Bind persisted messages, not scores, to the native session checkpoint.

    Returns:
        str: Canonical hash of the exact stored conversation.

    Raises:
        ValueError: No conversation is stored or its binding exceeds the size limit.
    """
    messages = await memory.get_conversation_messages_async(conversation_id=conversation_id)
    if not messages:
        raise ValueError("Checkpointing requires a persisted conversation")
    if executions is not None:
        turns = {turn.request_id: turn for execution in executions for turn in execution.turns}
        requests = {
            str(piece.id): piece for message in messages for piece in message.message_pieces if piece.role == "user"
        }
        if set(turns) != set(requests) or any(
            requests[request_id].converted_value != turn.prompt for request_id, turn in turns.items()
        ):
            raise ValueError("Stored requests do not match the completed native session")
        for turn in turns.values():
            if turn.response_text and not any(
                piece.role == "assistant"
                and piece.original_value == turn.response_text
                and piece.prompt_metadata.get("agent_turn_id") == str(turn.id)
                for message in messages
                for piece in message.message_pieces
            ):
                raise ValueError("Native turn response has not been persisted unchanged")
    digest = hashlib.sha256()
    total = 0
    for message in messages:
        data = json.dumps(message.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        total += len(data)
        if total > 16 * 1024 * 1024:
            raise ValueError("Checkpoint transcript exceeds the 16 MiB binding limit")
        digest.update(data)
        digest.update(b"\n")
    return digest.hexdigest()
