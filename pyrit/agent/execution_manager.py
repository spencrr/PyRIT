# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-runtime ownership of fresh agent executions and their evidence."""

import asyncio
import codecs
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from filelock import BaseFileLock, FileLock

from pyrit.agent.environment import (
    AcpByteReader,
    ExecutionSession,
    ExecutionSpec,
    create_execution_session,
    validate_environment_template,
)
from pyrit.agent.execution_store import AgentExecutionStore
from pyrit.agent.harness import launch_environment, resolve_credential_values, validate_harness_authentication
from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_execution import (
    AgentConnectionState,
    AgentExecution,
    AgentExecutionEvent,
    AgentExecutionState,
    AgentTargetConfiguration,
    AgentTurn,
    AgentTurnStatus,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.agent.acp_connection import AcpConnection


@dataclass
class _LiveExecution:
    record: AgentExecution
    environment: ExecutionSession
    connection: "AcpConnection | None" = None
    startup: asyncio.Task[None] | None = None
    operation: asyncio.Task[tuple[str, str]] | None = None
    stderr_task: asyncio.Task[None] | None = None
    event_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    secrets: tuple[str, ...] = ()
    close_requested: bool = False
    cancel_requested: bool = False
    close_task: asyncio.Task[None] | None = None


class AgentExecutionManager:
    """Own resources, not attack decisions. One manager exclusively owns its storage directory."""

    MAX_ERROR_CHARS = 2048
    # Shorter values cannot be redacted without destroying the journal; they are refused as credentials.
    MIN_SECRET_CHARS = 8

    def __init__(self, *, root: Path, max_executions: int = 4) -> None:
        """
        Configure storage and capacity without acquiring resources.

        Raises:
            ValueError: Capacity is not positive.
        """
        if max_executions < 1:
            raise ValueError("max_executions must be positive")
        self.store = AgentExecutionStore(root=root)
        self.max_executions = max_executions
        self.records: dict[UUID, AgentExecution] = {}
        self._live: dict[UUID, _LiveExecution] = {}
        self._admission = asyncio.Lock()
        self._capacity = asyncio.Condition(self._admission)
        self._started = False
        self._closed = False
        self._file_lock: BaseFileLock | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        """
        Start an explicitly owned standalone runtime.

        Returns:
            Self: The owned manager.
        """
        await self.start_async()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Release all executions at the end of a standalone runtime."""
        await self.close_async()

    @property
    def has_live_executions(self) -> bool:
        """Whether allocations still own runtime work."""
        return bool(self._live)

    @property
    def is_closed(self) -> bool:
        """Whether this owner has stopped admitting work."""
        return self._closed

    def latest_execution(self, *, conversation_id: str, target_id: str | None = None) -> AgentExecution | None:
        """
        Find the latest allocation, including failed restore attempts; never silently rewind.

        Returns:
            AgentExecution | None: The most recent allocation for this conversation.
        """
        candidates = [
            record
            for record in self.records.values()
            if record.conversation_id == conversation_id and (target_id is None or record.target_id == target_id)
        ]
        return max(candidates, key=lambda record: (record.created_at, str(record.id))) if candidates else None

    async def start_async(self) -> None:
        """
        Acquire exclusive ownership and reconcile interrupted executions.

        Raises:
            RuntimeError: The manager has closed.
        """
        async with self._admission:
            if self._closed:
                raise RuntimeError("Agent execution manager is closed")
            if self._started:
                return
            await asyncio.to_thread(self.store.root.mkdir, parents=True, exist_ok=True)
            lock = FileLock(self.store.root / "owner.lock", timeout=0, thread_local=False)
            try:

                async def acquire_async() -> None:
                    await asyncio.to_thread(lock.acquire)
                    self._file_lock = lock

                await await_task_completion_async(asyncio.create_task(acquire_async()))
                records = await self.store.load_all_async()
                for record in records:
                    self.records[record.id] = record
                    if record.state != AgentExecutionState.CLOSED:
                        await self._reconcile_interrupted_async(record)
            except BaseException:
                await await_task_completion_async(asyncio.create_task(asyncio.to_thread(lock.release)))
                self._file_lock = None
                raise
            self._started = True
            self._reaper = asyncio.create_task(self._expire_async())

    async def _reconcile_interrupted_async(self, record: AgentExecution) -> None:
        for turn in record.turns:
            if turn.status == AgentTurnStatus.RUNNING:
                turn.status = AgentTurnStatus.UNKNOWN
                turn.error = "Owning runtime stopped before the turn was finalized"
        try:
            self._live[record.id] = self._make_live(record)
        except ValueError as error:
            # Without its provider the resources cannot be released; say so rather than claim closure.
            record.state = AgentExecutionState.CLEANUP_FAILED
            record.close_reason = "interrupted_runtime"
            record.cleanup_error = f"{error}; resources may remain allocated"
            record.connection_state = AgentConnectionState.DISCONNECTED
            logger.error("Cannot reconcile agent execution %s: %s", record.id, error)
            await self.store.save_async(record)
            return
        await self.close_execution_async(execution_id=record.id, reason="interrupted_runtime")

    async def send_async(
        self,
        *,
        configuration: AgentTargetConfiguration,
        target_id: str,
        conversation_id: str,
        request_id: str,
        prompt: str,
        has_history: bool,
    ) -> tuple[AgentExecution, AgentTurn]:
        """
        Execute a new turn once. Closed sessions cannot be resurrected by replay.

        Returns:
            tuple[AgentExecution, AgentTurn]: Execution and explicit turn result.

        Raises:
            RuntimeError: Capacity, lifecycle, or request identity prevents sending.
            ValueError: History cannot be restored, or a credential value cannot be redacted.
            asyncio.CancelledError: The caller cancelled.
        """
        validate_harness_authentication(harness=configuration.harness_profile)
        validate_environment_template(configuration.environment_template)
        profile = configuration.to_profile()
        await self.start_async()
        async with self._admission:
            if self._closed:
                raise RuntimeError("Agent execution manager is closing")
            existing = self.latest_execution(target_id=target_id, conversation_id=conversation_id)
            if existing:
                if existing.state == AgentExecutionState.CLEANUP_FAILED and existing.id not in self._live:
                    raise RuntimeError(
                        f"Execution cleanup failed and cannot be retried here ({existing.cleanup_error}); "
                        "start a new conversation"
                    )
                if existing.state != AgentExecutionState.IDLE:
                    raise RuntimeError(f"Execution is {existing.state.value}; close or start a new conversation")
                if existing.session_id and existing.connection_state in (
                    AgentConnectionState.DISCONNECTED,
                    AgentConnectionState.FAILED,
                ):
                    raise RuntimeError("Agent connection is unavailable; close this execution and start fresh")
                live = self._live[existing.id]
                if any(
                    turn.request_id == request_id
                    for ancestor in self.records.values()
                    if ancestor.conversation_id == conversation_id
                    for turn in ancestor.turns
                ):
                    raise RuntimeError("Refusing to resend an already dispatched agent request")
            else:
                if has_history:
                    raise ValueError("A new agent cannot restore conversation history; start a fresh conversation")
                await self._wait_for_capacity_async(timeout=profile.startup_timeout_seconds)
                record = AgentExecution(
                    owner_id=self.store.root.resolve().as_posix(),
                    conversation_id=conversation_id,
                    target_id=target_id,
                    profile=profile,
                )
                live = self._make_live(record)
                live.secrets = self._redactable_secrets(
                    resolve_credential_values(credential_env=profile.credential_env)
                )
                self.records[record.id] = record
                self._live[record.id] = live
            record = live.record
            live.cancel_requested = False
            turn = AgentTurn(
                request_id=request_id,
                prompt=self._redact(prompt, live.secrets),
                event_start_cursor=record.event_count,
            )
            record.turns.append(turn)
            record.state = AgentExecutionState.WORKING if live.connection else AgentExecutionState.STARTING
            record.last_activity_at = datetime.now(UTC)
            try:
                await self.store.save_async(record)
            except BaseException:
                turn.status = AgentTurnStatus.UNKNOWN
                turn.error = "Unable to persist prepared input; prompt was not dispatched"
                await self.close_execution_async(execution_id=record.id, reason="intent_write_failed")
                raise
        try:
            if live.connection is None:
                if live.close_requested:
                    raise RuntimeError("Execution closed before startup")
                live.startup = asyncio.create_task(self._launch_async(live))
                await asyncio.shield(live.startup)
            if live.close_requested:
                raise RuntimeError("Execution closed during startup")
            record.state = AgentExecutionState.WORKING
            await self.store.save_async(record)
            assert live.connection is not None

            async def prompt_async() -> tuple[str, str]:
                if live.cancel_requested:
                    return "cancelled", ""
                assert live.connection is not None
                return await live.connection.prompt_async(prompt)

            live.operation = asyncio.create_task(prompt_async())
            deadline_exceeded = False
            try:
                stop_reason, text = await self._wait_for_turn_async(live)
            except TimeoutError:
                # A confirmed cancellation is an explicit outcome; an unconfirmed one raises and closes.
                await self.cancel_async(record.id)
                stop_reason, text = await live.operation
                deadline_exceeded = True
            turn.stop_reason = stop_reason
            turn.response_text = self._redact(text, live.secrets)
            status = (
                AgentTurnStatus.CANCELLED
                if stop_reason == "cancelled"
                else AgentTurnStatus.COMPLETED
                if stop_reason == "end_turn"
                else AgentTurnStatus.FAILED
            )
            if deadline_exceeded:
                turn.error = "Turn deadline exceeded"
            await self._record_async(live, direction="lifecycle", payload={"type": "turn_finished", "status": status})
            turn.status = status
            turn.capture_complete = record.capture_error is None
            turn.event_end_cursor = record.event_count
            if deadline_exceeded:
                await self.close_execution_async(execution_id=record.id, reason="turn_timeout")
            return record, turn
        except asyncio.CancelledError:
            try:
                if live.connection is None:
                    # Startup is shielded and still running; nothing can be cancelled in place, so release it.
                    await asyncio.shield(self.close_execution_async(execution_id=record.id, reason="caller_cancelled"))
                else:
                    await asyncio.shield(self.cancel_async(record.id))
                turn.status = AgentTurnStatus.CANCELLED
            except Exception:
                turn.status = AgentTurnStatus.UNKNOWN
                logger.exception("Could not confirm agent cancellation")
                await asyncio.shield(self.close_execution_async(execution_id=record.id, reason="caller_cancelled"))
            turn.error = "Caller cancelled the operation"
            raise
        except Exception as error:
            if turn.status == AgentTurnStatus.RUNNING:
                turn.status = AgentTurnStatus.UNKNOWN
            record.connection_state = AgentConnectionState.FAILED
            turn.error = self._redact(self._describe_error(error), live.secrets)
            await self.close_execution_async(execution_id=record.id, reason="operation_failed")
            raise
        finally:
            live.operation = None
            record.last_activity_at = datetime.now(UTC)
            if record.state == AgentExecutionState.WORKING:
                record.state = AgentExecutionState.IDLE
            await asyncio.shield(self.store.save_async(record))

    def _capacity_used(self) -> int:
        # Executions whose release failed are retried at shutdown; they no longer hold an admission slot.
        return sum(1 for live in self._live.values() if live.record.state != AgentExecutionState.CLEANUP_FAILED)

    async def _wait_for_capacity_async(self, *, timeout: float) -> None:
        # Called with the admission lock held; waiting releases it so closers can free a slot.
        try:
            async with asyncio.timeout(timeout):
                while self._capacity_used() >= self.max_executions:
                    await self._capacity.wait()
                    if self._closed:
                        raise RuntimeError("Agent execution manager is closing")
        except TimeoutError:
            raise RuntimeError(
                f"Agent execution capacity ({self.max_executions}) stayed full for {timeout:g}s; "
                "close idle executions, reduce concurrency, or raise max_executions"
            ) from None

    def _release_capacity(self) -> None:
        async def notify_async() -> None:
            async with self._admission:
                self._capacity.notify_all()

        asyncio.create_task(notify_async())

    @classmethod
    def _redactable_secrets(cls, credentials: dict[str, str]) -> tuple[str, ...]:
        """
        Keep only values the journal redactor can remove without corrupting unrelated content.

        Returns:
            tuple[str, ...]: The credential values to redact from every journal frame.

        Raises:
            ValueError: A referenced credential value is too short to redact.
        """
        short = sorted(name for name, value in credentials.items() if len(value) < cls.MIN_SECRET_CHARS)
        if short:
            raise ValueError(
                f"Credential values shorter than {cls.MIN_SECRET_CHARS} characters cannot be redacted from the "
                f"journal: {', '.join(short)}"
            )
        return tuple(credentials.values())

    async def _wait_for_turn_async(self, live: _LiveExecution) -> tuple[str, str]:
        started = monotonic()
        assert live.operation is not None
        while not live.operation.done():
            remaining = live.record.profile.turn_timeout_seconds - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Agent active-work deadline exceeded")
            await asyncio.wait((live.operation,), timeout=min(remaining, 0.1))
        return live.operation.result()

    def _make_live(self, record: AgentExecution) -> _LiveExecution:
        return _LiveExecution(
            record=record,
            environment=create_execution_session(execution=record, directory=self.store.directory(record.id)),
        )

    async def _launch_async(self, live: _LiveExecution) -> None:
        from pyrit.agent.acp_connection import AcpConnection, RecordingTransport

        profile = live.record.profile
        async with asyncio.timeout(profile.startup_timeout_seconds):
            credentials = resolve_credential_values(credential_env=profile.credential_env)
            live.secrets = self._redactable_secrets(credentials)
            live.environment.reserve()
            await self.store.save_async(live.record)
            spec = ExecutionSpec(
                command=profile.command,
                environment=launch_environment(kind=profile.harness_kind, home=live.environment.harness_home),
                credentials=credentials,
            )
            try:
                await live.environment.launch_async(spec)
            finally:
                await self.store.save_async(live.record)

            async def record_async(direction: str, payload: dict[str, Any]) -> None:
                await self._record_async(live, direction=direction, payload=payload)

            transport = RecordingTransport(
                reader=live.environment.acp_reader, writer=live.environment.acp_writer, record=record_async
            )

            async def status_changed_async(state: AgentConnectionState) -> None:
                live.record.connection_state = state
                await self._record_async(
                    live,
                    direction="lifecycle",
                    payload={
                        "type": "connection.state",
                        "state": state.value,
                    },
                )
                await self.store.save_async(live.record)

            await status_changed_async(AgentConnectionState.CONNECTING)
            live.connection = AcpConnection(
                transport=transport,
                permission_policy=live.record.profile.permission_policy,
                status_changed=status_changed_async,
            )
            live.stderr_task = asyncio.create_task(self._stderr_async(live, live.environment.stderr))
            live.record.agent_info = await live.connection.initialize_async(
                cwd=live.environment.agent_cwd,
                model=live.record.profile.model,
                authentication_method=live.record.profile.authentication_method,
            )
            live.record.session_id = live.connection.session_id
            live.record.connection_state = AgentConnectionState.READY

    async def _record_async(self, live: _LiveExecution, *, direction: str, payload: dict[str, Any]) -> None:
        async with live.event_lock:
            record = live.record
            if record.capture_error:
                raise RuntimeError(record.capture_error)
            try:
                sanitized = self._redact_payload(payload, live.secrets)
                event = AgentExecutionEvent(
                    execution_id=record.id,
                    sequence=record.event_count + 1,
                    turn_id=record.turns[-1].id
                    if record.turns and record.turns[-1].status == AgentTurnStatus.RUNNING
                    else None,
                    direction=direction,
                    payload=sanitized,
                )
                size = len(event.model_dump_json().encode("utf-8")) + 1
                if record.evidence_bytes + size > record.profile.max_evidence_bytes:
                    raise RuntimeError("Execution evidence budget exceeded; capture is incomplete")

                async def append_and_count_async() -> None:
                    record.evidence_bytes += await self.store.append_async(event)
                    record.event_count += 1
                    record.last_event_at = event.timestamp
                    if payload.get("type") == "connection.closed":
                        record.connection_state = AgentConnectionState.DISCONNECTED
                    self.store.notify()

                await await_task_completion_async(asyncio.create_task(append_and_count_async()))
            except Exception as error:
                record.capture_error = f"Evidence capture failed: {type(error).__name__}"
                logger.exception("Agent evidence capture failed for %s", record.id)
                raise

    @classmethod
    def _describe_error(cls, error: Exception) -> str:
        # JSON-RPC errors carry their specifics in `data`; keep them with the recorded failure, bounded,
        # because the text is harness-controlled.
        data = getattr(error, "data", None)
        details = data.get("details") if isinstance(data, dict) else None
        text = f"{error}: {details}" if isinstance(details, str) and details else str(error)
        return text[: cls.MAX_ERROR_CHARS]

    @staticmethod
    def _redact(text: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    @classmethod
    def _redact_payload(cls, value: Any, secrets: tuple[str, ...]) -> Any:
        # Redact string leaves before serialization: escaping would otherwise hide secrets from the comparison.
        if isinstance(value, str):
            return cls._redact(value, secrets)
        if isinstance(value, dict):
            return {
                cls._redact_payload(key, secrets): cls._redact_payload(item, secrets) for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._redact_payload(item, secrets) for item in value]
        return value

    async def _stderr_async(self, live: _LiveExecution, reader: AcpByteReader) -> None:
        pending = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        overlap = max((len(secret) for secret in live.secrets), default=1) - 1
        try:
            while chunk := await reader.read(4096):
                pending += decoder.decode(chunk)
                # Replace complete secrets before retaining a suffix that may start the next one.
                pending = self._redact(pending, live.secrets)
                boundary = max(0, len(pending) - overlap)
                if boundary:
                    await self._record_async(live, direction="stderr", payload={"text": pending[:boundary]})
                    pending = pending[boundary:]
            pending += decoder.decode(b"", final=True)
            if pending:
                await self._record_async(
                    live, direction="stderr", payload={"text": self._redact(pending, live.secrets)}
                )
        except Exception:
            logger.exception("Agent stderr capture failed for %s", live.record.id)
            if live.connection:
                await live.connection.close_async()

    async def cancel_async(self, execution_id: UUID) -> None:
        """
        Cancel an active turn, preserving the session only when cancellation settles.

        Raises:
            RuntimeError: Startup or missing cancellation acknowledgement prevents continuation.
        """
        live = self._live.get(execution_id)
        if not live:
            return
        async with live.cancel_lock:
            live.cancel_requested = True
            if live.operation is None and live.record.state in (
                AgentExecutionState.STARTING,
                AgentExecutionState.WORKING,
            ):
                return
            if live.connection is None:
                raise RuntimeError("Execution is still starting; close it after startup or wait for its deadline")
            if live.operation is None or live.operation.done():
                return
            await live.connection.cancel_async()
            try:
                await asyncio.wait_for(
                    asyncio.shield(live.operation), timeout=live.record.profile.cancellation_grace_seconds
                )
            except (TimeoutError, ConnectionError):
                await self.close_execution_async(execution_id=execution_id, reason="cancellation_unconfirmed")
                raise RuntimeError("Agent did not confirm cancellation; its execution was closed") from None

    async def close_execution_async(self, *, execution_id: UUID, reason: str = "closed_by_user") -> None:
        """Idempotently release resources, recording failures rather than pretending success."""
        live = self._live.get(execution_id)
        if live is None:
            return
        if live.close_task is None or live.close_task.done():
            live.close_task = asyncio.create_task(self._close_execution_async(live, reason=reason))
        await await_task_completion_async(live.close_task)

    async def _close_execution_async(self, live: _LiveExecution, *, reason: str) -> None:
        async with live.close_lock:
            record = live.record
            execution_id = record.id
            live.close_requested = True
            record.state = AgentExecutionState.CLOSING
            record.close_reason = reason
            persistence_errors: list[Exception] = []
            try:
                await self.store.save_async(record)
            except Exception as error:
                persistence_errors.append(error)
                logger.exception("Could not persist close intent; resource cleanup will still proceed")
            errors: list[str] = []
            if live.startup and not live.startup.done():
                try:
                    await asyncio.shield(live.startup)
                except Exception:
                    logger.exception("Agent startup did not complete before close")
            if live.connection:
                try:
                    await live.connection.cancel_async()
                    if live.operation and not live.operation.done():
                        await asyncio.wait_for(
                            asyncio.shield(live.operation), timeout=record.profile.cancellation_grace_seconds
                        )
                except Exception as error:
                    logger.warning("Agent did not settle before close: %s", error)
            try:
                await live.environment.close_async()
            except Exception as error:
                errors.append(self._redact(str(error), live.secrets))
                logger.exception("Failed to release agent execution %s", execution_id)
            try:
                if live.connection:
                    await live.connection.close_async()
                if live.stderr_task:
                    await asyncio.wait_for(asyncio.shield(live.stderr_task), timeout=5)
            except Exception as error:
                errors.append(self._redact(str(error), live.secrets))
                logger.exception("Failed to close agent evidence streams")
            record.cleanup_error = "; ".join(errors) if errors else None
            record.state = AgentExecutionState.CLEANUP_FAILED if errors else AgentExecutionState.CLOSED
            record.connection_state = AgentConnectionState.DISCONNECTED
            try:
                await self.store.save_async(record)
            except Exception as error:
                persistence_errors.append(error)
                logger.exception("Could not persist cleanup outcome for %s", execution_id)
            if not errors:
                self._live.pop(execution_id, None)
            self._release_capacity()
            if persistence_errors:
                raise ExceptionGroup("Execution evidence persistence failed during cleanup", persistence_errors)

    async def close_conversation_async(self, *, target_id: str, conversation_id: str) -> None:
        """Release the execution associated with an attack-owned conversation."""
        for record in tuple(self.records.values()):
            if record.target_id == target_id and record.conversation_id == conversation_id:
                await self.close_execution_async(execution_id=record.id, reason="conversation_finished")

    async def _expire_async(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = datetime.now(UTC)
            for live in tuple(self._live.values()):
                record = live.record
                expired = (now - record.created_at).total_seconds() >= record.profile.lifetime_seconds
                idle = (
                    record.state == AgentExecutionState.IDLE
                    and (now - record.last_activity_at).total_seconds() >= record.profile.idle_timeout_seconds
                )
                if (expired or idle) and record.state not in (
                    AgentExecutionState.CLOSING,
                    AgentExecutionState.CLEANUP_FAILED,
                ):
                    try:
                        await self.close_execution_async(execution_id=record.id, reason="expired")
                    except Exception:
                        logger.exception("Failed to expire agent execution %s", record.id)

    async def close_async(self) -> None:
        """
        Stop admission, release owned executions, and relinquish the storage lock.

        Raises:
            ExceptionGroup: One or more resources could not be released.
        """
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._close_async())
        await await_task_completion_async(self._shutdown_task)

    async def _close_async(self) -> None:
        async with self._admission:
            self._closed = True
            self._capacity.notify_all()
            self.store.notify()
        if self._reaper:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        errors: list[Exception] = []
        try:
            for execution_id in tuple(self._live):
                try:
                    await self.close_execution_async(execution_id=execution_id, reason="runtime_shutdown")
                    if self.records[execution_id].cleanup_error:
                        errors.append(RuntimeError(self.records[execution_id].cleanup_error))
                except Exception as error:
                    errors.append(error)
        finally:
            if self._file_lock:
                await asyncio.to_thread(self._file_lock.release)
                self._file_lock = None
        if errors:
            raise ExceptionGroup("Agent execution cleanup failed", errors)
