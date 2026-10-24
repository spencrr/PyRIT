# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest

from pyrit.agent.execution_store import AgentExecutionStore
from pyrit.models.agent_execution import AgentExecution, AgentProfile


def make_record(conversation_id: str) -> AgentExecution:
    return AgentExecution(
        owner_id="test",
        conversation_id=conversation_id,
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True),
    )


async def test_observers_wake_only_for_their_own_conversation(tmp_path: Path) -> None:
    store = AgentExecutionStore(root=tmp_path)
    with store.subscribe("one") as one, store.subscribe("two") as two:
        await store.save_async(make_record("one"))
        assert one.is_set() and not two.is_set()
        one.clear()
        store.notify("unobserved")
        assert not one.is_set() and not two.is_set()
        store.notify_all()
        assert one.is_set() and two.is_set()
    assert not store._listeners


async def test_observer_capacity_is_shared_across_conversations(tmp_path: Path) -> None:
    store = AgentExecutionStore(root=tmp_path)
    store.MAX_OBSERVERS = 2
    with store.subscribe("one"), store.subscribe("two"):
        with pytest.raises(RuntimeError, match="capacity"):
            with store.subscribe("three"):
                pass
    with store.subscribe("three") as event:
        store.notify("three")
        assert event.is_set()
