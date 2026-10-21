# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lazy runtime ownership without importing optional agent dependencies at backend startup."""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.agent.execution_manager import AgentExecutionManager

_default_manager: "AgentExecutionManager | None" = None

MAX_EXECUTIONS_ENV = "PYRIT_AGENT_MAX_EXECUTIONS"
DEFAULT_MAX_EXECUTIONS = 4


def agent_execution_root() -> Path:
    """
    Return the directory the runtime-owned manager stores executions in.

    The journal store is file-backed, so a remote memory results location (such as an Azure Storage
    URL) falls back to the local database data directory.

    Returns:
        Path: The local ``agent_executions`` directory.
    """
    from pyrit.common.path import DB_DATA_PATH
    from pyrit.memory import CentralMemory

    results = CentralMemory.get_memory_instance().results_path
    if results and "://" not in results:
        return Path(results) / "agent_executions"
    return Path(DB_DATA_PATH) / "agent_executions"


def get_agent_execution_manager() -> "AgentExecutionManager":
    """
    Return the runtime-owned manager.

    Capacity comes from ``PYRIT_AGENT_MAX_EXECUTIONS`` (default 4).

    Returns:
        AgentExecutionManager: Manager rooted in the local agent execution directory.

    Raises:
        ImportError: The optional agents dependencies are missing.
        ValueError: The configured capacity is not a positive integer.
    """
    global _default_manager
    if _default_manager is None:
        try:
            from pyrit.agent.execution_manager import AgentExecutionManager
        except ImportError as error:
            raise ImportError("Agent execution requires installation of pyrit[agents]") from error
        configured = os.environ.get(MAX_EXECUTIONS_ENV, "")
        try:
            max_executions = int(configured) if configured else DEFAULT_MAX_EXECUTIONS
        except ValueError:
            raise ValueError(f"{MAX_EXECUTIONS_ENV} must be a positive integer, not {configured!r}") from None
        _default_manager = AgentExecutionManager(root=agent_execution_root(), max_executions=max_executions)
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
    if await asyncio.to_thread(agent_execution_root().exists):
        await get_agent_execution_manager().start_async()
