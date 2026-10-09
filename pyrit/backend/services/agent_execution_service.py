# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Conversation-scoped access to agent evidence, using existing attack membership rules."""

from uuid import UUID

from pyrit.agent.runtime import get_agent_execution_manager
from pyrit.memory import CentralMemory
from pyrit.models.agent_execution import AgentExecution


async def resolve_conversation_execution_async(
    *, attack_result_id: str, conversation_id: str, execution_id: UUID | None = None
) -> AgentExecution | None:
    """
    Resolve only an execution belonging to a conversation in the requested attack.

    Returns:
        AgentExecution | None: The associated execution, if provisioning has started.

    Raises:
        ValueError: The attack/conversation/execution association is invalid.
    """
    memory = CentralMemory.get_memory_instance()
    attacks = await memory.get_attack_results_async(attack_result_ids=[attack_result_id])
    if not attacks or conversation_id not in attacks[0].get_active_conversation_ids():
        raise ValueError("Conversation does not belong to this attack")
    manager = get_agent_execution_manager()
    await manager.start_async()
    record = next((r for r in manager.records.values() if r.conversation_id == conversation_id), None)
    if execution_id is not None and (record is None or record.id != execution_id):
        raise ValueError("Execution does not belong to this conversation")
    return record
