# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lazy runtime ownership without importing optional agent dependencies at backend startup."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.agent.execution_manager import AgentExecutionManager

_default_manager: "AgentExecutionManager | None" = None


def get_agent_execution_manager() -> "AgentExecutionManager":
    """
    Return the runtime-owned manager.

    Returns:
        AgentExecutionManager: Manager rooted in the active memory results directory.

    Raises:
        ValueError: Memory has no persistent results directory.
        ImportError: The optional agents dependencies are missing.
    """
    global _default_manager
    if _default_manager is None:
        try:
            from pyrit.agent.execution_manager import AgentExecutionManager
        except ImportError as error:
            raise ImportError("Agent execution requires installation of pyrit[agents]") from error
        from pyrit.memory import CentralMemory

        results = CentralMemory.get_memory_instance().results_path
        if not results:
            raise ValueError("Agent execution requires a memory results directory")
        _default_manager = AgentExecutionManager(root=Path(results) / "agent_executions")
    return _default_manager


def peek_agent_execution_manager() -> "AgentExecutionManager | None":
    """
    Inspect runtime ownership.

    Returns:
        AgentExecutionManager | None: Existing manager, without construction.
    """
    return _default_manager


async def close_agent_execution_manager_async() -> None:
    """Release the manager before memory/configuration replacement."""
    global _default_manager
    if _default_manager:
        manager = _default_manager
        try:
            await manager.close_async()
        finally:
            _default_manager = None


async def reconcile_agent_executions_async() -> None:
    """Reconcile persisted owned resources on backend startup, without creating an unused store."""
    from pyrit.memory import CentralMemory

    results = CentralMemory.get_memory_instance().results_path
    if results and await asyncio.to_thread((Path(results) / "agent_executions").exists):
        await get_agent_execution_manager().start_async()
