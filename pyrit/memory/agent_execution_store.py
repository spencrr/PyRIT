# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""File-backed execution evidence beneath the configured memory results directory."""

import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import UUID

from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_execution import AgentEventPage, AgentExecution, AgentExecutionEvent

logger = logging.getLogger(__name__)


class AgentExecutionStore:
    """Persist records atomically and protocol events in an append-only journal."""

    def __init__(self, *, root: Path) -> None:
        """Bind storage without creating directories or starting processes."""
        self.root = root
        self._save_lock = asyncio.Lock()

    def directory(self, execution_id: UUID) -> Path:
        """
        Resolve a typed execution identity under this store.

        Returns:
            Path: Execution directory.
        """
        return self.root / str(execution_id)

    async def save_async(self, execution: AgentExecution) -> None:
        """
        Atomically persist a detached record, including prepared turn inputs.

        Raises:
            asyncio.CancelledError: Cancellation propagates after the atomic write finishes.
        """
        async with self._save_lock:
            data = execution.model_dump_json()
            await await_task_completion_async(
                asyncio.create_task(self._save_with_retry_async(execution_id=execution.id, data=data))
            )

    async def _save_with_retry_async(self, *, execution_id: UUID, data: str) -> None:
        for attempt in range(10):
            try:
                await asyncio.to_thread(self._save, execution_id=execution_id, data=data)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (5, 32) or attempt == 9:
                    raise
                logger.warning("Retrying temporarily locked execution record %s", execution_id)
                await asyncio.sleep(0.05)

    def _save(self, *, execution_id: UUID, data: str) -> None:
        directory = self.directory(execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "record.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "record.json")

    async def load_all_async(self) -> list[AgentExecution]:
        """
        Load retained records; corrupt evidence fails explicitly.

        Returns:
            list[AgentExecution]: Persisted records.
        """
        return await asyncio.to_thread(self._load_all)

    def _load_all(self) -> list[AgentExecution]:
        records: list[AgentExecution] = []
        for path in sorted(self.root.glob("*/record.json")):
            record = AgentExecution.model_validate_json(path.read_text(encoding="utf-8"))
            records.append(record)
        return records

    async def append_async(self, event: AgentExecutionEvent) -> int:
        """
        Flush an event before allowing the protocol pump to proceed.

        Returns:
            int: Bytes written.
        """
        data = (event.model_dump_json() + "\n").encode("utf-8")
        await await_task_completion_async(
            asyncio.create_task(asyncio.to_thread(self._append, execution_id=event.execution_id, data=data))
        )
        return len(data)

    def _append(self, *, execution_id: UUID, data: bytes) -> None:
        with (self.directory(execution_id) / "events.jsonl").open("ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    async def events_async(self, *, execution_id: UUID, after: int = 0, limit: int = 100) -> AgentEventPage:
        """
        Read a bounded page, ignoring only an unfinished final journal line.

        Returns:
            AgentEventPage: Events and the next cursor.

        Raises:
            ValueError: Pagination bounds are invalid.
        """
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("Event cursor must be nonnegative and limit between 1 and 500")
        return await asyncio.to_thread(self._events, execution_id=execution_id, after=after, limit=limit)

    def _events(self, *, execution_id: UUID, after: int, limit: int) -> AgentEventPage:
        events: list[AgentExecutionEvent] = []
        path = self.directory(execution_id) / "events.jsonl"
        if path.exists():
            with path.open("rb") as stream:
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    event = AgentExecutionEvent.model_validate(json.loads(line))
                    if event.sequence > after:
                        events.append(event)
                        if len(events) >= limit:
                            break
        return AgentEventPage(events=events, next_cursor=events[-1].sequence if events else after)
