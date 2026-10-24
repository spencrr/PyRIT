# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("acp")

from pyrit.agent import runtime
from pyrit.agent.execution_manager import AgentExecutionManager


@pytest.fixture(autouse=True)
def fresh_runtime(tmp_path: Path):
    memory = MagicMock(results_path=str(tmp_path / "results"))
    with (
        patch.object(runtime, "_default_manager", None),
        patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=memory),
    ):
        yield memory


def test_runtime_manager_capacity_comes_from_the_environment(tmp_path: Path) -> None:
    with patch.dict("os.environ", {runtime.MAX_EXECUTIONS_ENV: "9"}):
        manager = runtime.get_agent_execution_manager()
    assert isinstance(manager, AgentExecutionManager)
    assert manager.max_executions == 9
    assert manager.store.root == tmp_path / "results" / "agent_executions"
    assert runtime.get_agent_execution_manager() is manager


def test_runtime_manager_rejects_a_malformed_capacity() -> None:
    with patch.dict("os.environ", {runtime.MAX_EXECUTIONS_ENV: "many"}), pytest.raises(ValueError, match="positive"):
        runtime.get_agent_execution_manager()
    with patch.dict("os.environ", {runtime.MAX_EXECUTIONS_ENV: "0"}), pytest.raises(ValueError, match="positive"):
        runtime.get_agent_execution_manager()


def test_remote_results_locations_fall_back_to_local_storage(fresh_runtime: MagicMock) -> None:
    from pyrit.common.path import DB_DATA_PATH

    fresh_runtime.results_path = "https://account.blob.core.windows.net/results"
    assert runtime.agent_execution_root() == Path(DB_DATA_PATH) / "agent_executions"
    fresh_runtime.results_path = None
    assert runtime.agent_execution_root() == Path(DB_DATA_PATH) / "agent_executions"


async def test_reconcile_only_starts_a_manager_for_existing_state(tmp_path: Path) -> None:
    await runtime.reconcile_agent_executions_async()
    assert runtime.peek_agent_execution_manager() is None
    (tmp_path / "results" / "agent_executions").mkdir(parents=True)
    await runtime.reconcile_agent_executions_async()
    manager = runtime.peek_agent_execution_manager()
    assert manager is not None and not manager.is_closed
    await runtime.close_agent_execution_manager_async()
    assert manager.is_closed
    assert runtime.peek_agent_execution_manager() is None
