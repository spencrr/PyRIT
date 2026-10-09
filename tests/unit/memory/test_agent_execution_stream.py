# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from pyrit.memory.agent_execution_store import AgentExecutionStore
from pyrit.models.agent_execution import AgentExecution, AgentExecutionEvent, AgentProfile


async def test_subscription_catchup_append_and_resume_use_durable_cursor(tmp_path: Path) -> None:
    store = AgentExecutionStore(root=tmp_path)
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True),
    )
    with store.subscribe() as changed:
        await store.save_async(record)
        assert changed.is_set()
        changed.clear()
        for sequence in range(1, 6):
            await store.append_async(
                AgentExecutionEvent(
                    execution_id=record.id,
                    sequence=sequence,
                    direction="incoming",
                    payload={"number": sequence},
                )
            )
        first = await store.events_async(execution_id=record.id, limit=2)
        assert [event.sequence for event in first.events] == [1, 2]
        second = await store.events_async(execution_id=record.id, after=first.next_cursor, limit=2)
        assert [event.sequence for event in second.events] == [3, 4]
        assert len(store._indexes[record.id].offsets) == 5
        await store.append_async(
            AgentExecutionEvent(execution_id=record.id, sequence=6, direction="incoming", payload={})
        )
        last = await store.events_async(execution_id=record.id, after=second.next_cursor)
        assert [event.sequence for event in last.events] == [5, 6]
    assert not store._listeners
