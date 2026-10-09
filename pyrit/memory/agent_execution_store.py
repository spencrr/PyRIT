# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""File-backed execution evidence beneath the configured memory results directory."""

import asyncio
import json
import logging
import os
from bisect import bisect_right
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from uuid import UUID

from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_execution import AgentApproval, AgentEventPage, AgentExecution, AgentExecutionEvent

logger = logging.getLogger(__name__)


@dataclass
class _JournalIndex:
    offsets: list[int] = field(default_factory=list)
    sequences: list[int] = field(default_factory=list)
    end: int = 0


class AgentExecutionStore:
    """Persist records atomically and protocol events in an append-only journal."""

    def __init__(self, *, root: Path) -> None:
        """Bind storage without creating directories or starting processes."""
        self.root = root
        self._save_lock = asyncio.Lock()
        self._listeners: set[asyncio.Event] = set()
        self._indexes: dict[UUID, _JournalIndex] = {}
        self._index_lock = Lock()

    def notify(self) -> None:
        """Wake observers after a durable state change; slow observers do not buffer events in memory."""
        for listener in self._listeners:
            listener.set()

    @contextmanager
    def subscribe(self) -> Generator[asyncio.Event, None, None]:
        """
        Subscribe to bounded wakeups; the journal remains the source of replay.

        Yields:
            asyncio.Event: A coalesced change notification.

        Raises:
            RuntimeError: Observer admission is exhausted.
        """
        if len(self._listeners) >= 64:
            raise RuntimeError("Execution observer capacity reached")
        event = asyncio.Event()
        self._listeners.add(event)
        try:
            yield event
        finally:
            self._listeners.discard(event)

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
            self.notify()

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
            self._recover_journal_state(record)
            records.append(record)
        return records

    def _recover_journal_state(self, record: AgentExecution) -> None:
        approvals = {approval.id: approval for approval in record.approvals}
        cursor = 0
        while True:
            page = self._events(execution_id=record.id, after=cursor, limit=500)
            if not page.events:
                break
            for event in page.events:
                if event.execution_id != record.id:
                    raise ValueError("Execution journal identity does not match its record")
                if event.direction == "lifecycle" and event.payload.get("type") in (
                    "permission.pending",
                    "permission.resolved",
                ):
                    approval = AgentApproval.model_validate(event.payload["approval"])
                    approvals[approval.id] = approval
            cursor = page.next_cursor
            record.last_event_at = page.events[-1].timestamp
        record.approvals = list(approvals.values())
        if cursor:
            record.event_count = cursor
            with self._index_lock:
                record.evidence_bytes = self._indexes[record.id].end

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
            with self._index_lock, path.open("rb") as stream:
                index = self._indexes.setdefault(execution_id, _JournalIndex())
                stream.seek(index.end)
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break
                    sequence = AgentExecutionEvent.model_validate(json.loads(line)).sequence
                    if index.sequences and sequence <= index.sequences[-1]:
                        raise ValueError("Execution journal sequence is not strictly increasing")
                    index.offsets.append(index.end)
                    index.sequences.append(sequence)
                    index.end = stream.tell()
                start = bisect_right(index.sequences, after)
                for offset in index.offsets[start : start + limit]:
                    stream.seek(offset)
                    events.append(AgentExecutionEvent.model_validate(json.loads(stream.readline())))
        return AgentEventPage(events=events, next_cursor=events[-1].sequence if events else after)
