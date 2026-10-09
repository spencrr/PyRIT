# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-runtime ownership of fresh agent executions and their evidence."""

import asyncio
import codecs
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from filelock import BaseFileLock, FileLock

from pyrit.agent.approvals import AgentApprovals
from pyrit.agent.environment import ExecutionEnvironment
from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.memory.agent_execution_store import AgentExecutionStore
from pyrit.models.agent_execution import (
    AgentApproval,
    AgentConnectionState,
    AgentExecution,
    AgentExecutionEvent,
    AgentExecutionState,
    AgentPermissionPolicy,
    AgentProfile,
    AgentTurn,
    AgentTurnStatus,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.agent.acp_connection import AcpConnection
    from pyrit.agent.inference_relay import InferenceLease, InferenceRelay
    from pyrit.agent.model_binding import ResolvedModelBinding


@dataclass
class _LiveExecution:
    record: AgentExecution
    environment: ExecutionEnvironment
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
    model_binding: "ResolvedModelBinding | None" = None
    inference_lease: "InferenceLease | None" = None
    approvals: AgentApprovals | None = None


class AgentExecutionManager:
    """Own resources, not attack decisions. One manager exclusively owns its storage directory."""

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
        self._started = False
        self._closed = False
        self._file_lock: BaseFileLock | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._relay: InferenceRelay | None = None
        self._relay_lock = asyncio.Lock()

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
        """Whether resources remain allocated, including idle chats and failed cleanup."""
        return bool(self._live)

    @property
    def is_closed(self) -> bool:
        """Whether this owner has stopped admitting work."""
        return self._closed

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
                        for approval in record.approvals:
                            if approval.decision is None:
                                approval.decision, approval.actor = "cancelled", "interrupted_runtime"
                        for turn in record.turns:
                            if turn.status == AgentTurnStatus.RUNNING:
                                turn.status = AgentTurnStatus.UNKNOWN
                                turn.error = "Owning runtime stopped before the turn was finalized"
                        self._live[record.id] = self._make_live(record)
                        await self.close_execution_async(execution_id=record.id, reason="interrupted_runtime")
            except BaseException:
                await await_task_completion_async(asyncio.create_task(asyncio.to_thread(lock.release)))
                self._file_lock = None
                raise
            self._started = True
            self._reaper = asyncio.create_task(self._expire_async())

    async def send_async(
        self,
        *,
        profile: AgentProfile,
        target_id: str,
        conversation_id: str,
        request_id: str,
        prompt: str,
        has_history: bool,
        model_binding: "ResolvedModelBinding | None" = None,
        interactive: bool = False,
    ) -> tuple[AgentExecution, AgentTurn]:
        """
        Execute a new turn once. Closed sessions cannot be resurrected by replay.

        Returns:
            tuple[AgentExecution, AgentTurn]: Execution and explicit turn result.

        Raises:
            RuntimeError: Capacity, lifecycle, or request identity prevents sending.
            ValueError: History cannot be restored.
            TimeoutError: The turn exceeded its deadline.
            asyncio.CancelledError: The caller cancelled.
        """
        if not interactive and profile.permission_policy == AgentPermissionPolicy.ASK:
            raise ValueError("Automated agent runs require an explicit allow_once or deny permission policy")
        await self.start_async()
        async with self._admission:
            if self._closed:
                raise RuntimeError("Agent execution manager is closing")
            existing = next(
                (r for r in self.records.values() if r.target_id == target_id and r.conversation_id == conversation_id),
                None,
            )
            if existing:
                if existing.interactive != interactive:
                    raise ValueError("Execution interaction mode cannot change within a conversation")
                if existing.state != AgentExecutionState.IDLE:
                    raise RuntimeError(f"Execution is {existing.state.value}; close or start a new conversation")
                if existing.session_id and existing.connection_state in (
                    AgentConnectionState.DISCONNECTED,
                    AgentConnectionState.FAILED,
                ):
                    raise RuntimeError("Agent connection is unavailable; close this execution and start fresh")
                live = self._live[existing.id]
                if any(turn.request_id == request_id for turn in existing.turns):
                    raise RuntimeError("Refusing to resend an already dispatched agent request")
            else:
                if has_history:
                    raise ValueError("A new agent cannot restore conversation history; start a fresh conversation")
                if len(self._live) >= self.max_executions:
                    raise RuntimeError(
                        "Agent execution capacity reached; close an idle execution or reduce concurrency"
                    )
                record = AgentExecution(
                    owner_id=self.store.root.resolve().as_posix(),
                    conversation_id=conversation_id,
                    target_id=target_id,
                    profile=profile,
                    interactive=interactive,
                )
                live = self._make_live(record)
                live.model_binding = model_binding
                live.secrets = tuple(live.environment.credential_values().values())
                self.records[record.id] = record
                self._live[record.id] = live
            record = live.record
            live.cancel_requested = False
            if live.approvals:
                live.approvals.cancelled = False
            turn = AgentTurn(request_id=request_id, prompt=self._redact(prompt, live.secrets))
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
            try:
                stop_reason, text = await self._wait_for_turn_async(live)
            except TimeoutError:
                await self.cancel_async(record.id)
                stop_reason, text = await live.operation
                if not interactive or stop_reason != "cancelled" or live.close_requested:
                    turn.error = "Turn deadline exceeded"
                    raise
                record.state = AgentExecutionState.HELD
                record.held_until = min(
                    datetime.now(UTC) + timedelta(seconds=profile.interactive_hold_seconds),
                    record.created_at + timedelta(seconds=profile.lifetime_seconds),
                )
                turn.error = "Turn deadline exceeded; cancellation confirmed. Execution retained for operator action."
                await self._record_async(
                    live,
                    direction="lifecycle",
                    payload={
                        "type": "execution.held",
                        "until": record.held_until.isoformat(),
                        "mode": "running",
                    },
                )
            turn.stop_reason = stop_reason
            turn.response_text = self._redact(text, live.secrets)
            status = (
                AgentTurnStatus.CANCELLED
                if stop_reason == "cancelled"
                else AgentTurnStatus.COMPLETED
                if stop_reason == "end_turn"
                else AgentTurnStatus.FAILED
            )
            await self._record_async(live, direction="lifecycle", payload={"type": "turn_finished", "status": status})
            turn.status = status
            turn.capture_complete = record.capture_error is None
            return record, turn
        except asyncio.CancelledError:
            try:
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
            turn.error = self._redact(str(error), live.secrets)
            await self.close_execution_async(execution_id=record.id, reason="operation_failed")
            raise
        finally:
            live.operation = None
            record.last_activity_at = datetime.now(UTC)
            if record.state == AgentExecutionState.WORKING:
                record.state = AgentExecutionState.IDLE
            await asyncio.shield(self.store.save_async(record))

    async def _wait_for_turn_async(self, live: _LiveExecution) -> tuple[str, str]:
        started = monotonic()
        baseline_wait = live.approvals.wait_seconds if live.approvals else 0
        assert live.operation is not None
        while not live.operation.done():
            wait = (live.approvals.wait_seconds - baseline_wait) if live.approvals else 0
            remaining = live.record.profile.turn_timeout_seconds - (monotonic() - started - wait)
            if remaining <= 0:
                raise TimeoutError("Agent active-work deadline exceeded")
            await asyncio.wait((live.operation,), timeout=min(remaining, 0.1))
        return live.operation.result()

    def _make_live(self, record: AgentExecution) -> _LiveExecution:
        live = _LiveExecution(
            record=record,
            environment=ExecutionEnvironment(execution=record, directory=self.store.directory(record.id)),
        )

        async def approval_changed_async(approval: AgentApproval) -> None:
            approval.title = self._redact(approval.title, live.secrets)
            await self._record_async(
                live,
                direction="lifecycle",
                payload={
                    "type": "permission.pending" if approval.decision is None else "permission.resolved",
                    "approval": approval.model_dump(mode="json"),
                },
            )
            # The flushed journal is the commit point; a stale snapshot must not reverse it.
            try:
                await self.store.save_async(record)
            except OSError:
                logger.exception(
                    "Approval %s committed to the journal, but execution snapshot %s could not be saved",
                    approval.id,
                    record.id,
                )

        live.approvals = AgentApprovals(record=record, changed=approval_changed_async)
        return live

    async def _launch_async(self, live: _LiveExecution) -> None:
        from pyrit.agent.acp_connection import AcpConnection, RecordingTransport

        async with asyncio.timeout(live.record.profile.startup_timeout_seconds):
            live.secrets = tuple(live.environment.credential_values().values())
            if live.model_binding is not None:
                await self._bind_inference_async(live)
            # Container names are deterministic, so a crash during create remains reconcilable.
            if live.record.profile.environment == "docker":
                live.record.provider_id = f"pyrit-agent-{live.record.id}"
            await self.store.save_async(live.record)
            await live.environment.prepare_async()
            await self.store.save_async(live.record)
            process = await live.environment.launch_async()
            await self.store.save_async(live.record)
            assert process.stdout is not None and process.stdin is not None and process.stderr is not None

            async def record_async(direction: str, payload: dict[str, Any]) -> None:
                await self._record_async(live, direction=direction, payload=payload)

            transport = RecordingTransport(reader=process.stdout, writer=process.stdin, record=record_async)

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
                approvals=live.approvals,
                status_changed=status_changed_async,
            )
            live.stderr_task = asyncio.create_task(self._stderr_async(live, process.stderr))
            live.record.agent_info = await live.connection.initialize_async(
                cwd=live.environment.agent_cwd,
                model="" if live.model_binding else live.record.profile.model,
                authentication_method=None if live.model_binding else live.record.profile.authentication_method,
            )
            live.record.session_id = live.connection.session_id
            live.record.connection_state = AgentConnectionState.READY

    async def _bind_inference_async(self, live: _LiveExecution) -> None:
        from pyrit.agent.inference_relay import InferenceLease, InferenceRelay

        binding = live.model_binding
        assert binding is not None
        async with self._relay_lock:
            if self._relay is None:
                self._relay = InferenceRelay(
                    host=os.getenv("PYRIT_INFERENCE_RELAY_HOST", "127.0.0.1"),
                    advertised_host=os.getenv("PYRIT_INFERENCE_RELAY_ADVERTISED_HOST", "127.0.0.1"),
                )
            if live.record.profile.environment == "docker" and self._relay.advertised_host in (
                "127.0.0.1",
                "localhost",
            ):
                raise ValueError(
                    "Docker BYOK requires an explicitly reachable PYRIT_INFERENCE_RELAY_HOST and "
                    "PYRIT_INFERENCE_RELAY_ADVERTISED_HOST (for example host.docker.internal on Docker Desktop)"
                )
            await self._relay.start_async()

            async def record_async(payload: dict[str, Any]) -> None:
                await self._record_async(live, direction="inference", payload=payload)

            lease = InferenceLease(
                binding=binding,
                record=record_async,
                timeout_seconds=live.record.profile.turn_timeout_seconds,
                max_calls=live.record.profile.max_inference_requests,
                capture_content=live.record.profile.capture_inference_content,
            )
            self._relay.add(lease)
            live.inference_lease = lease
            live.secrets = (*live.secrets, lease.token)
            live.record.inference_target_hash = binding.identifier_hash
            live.record.inference_protocol = binding.requirements.wire_api
            live.environment.runtime_environment = {
                "COPILOT_PROVIDER_BASE_URL": self._relay.url,
                "COPILOT_PROVIDER_TYPE": "openai",
                "COPILOT_PROVIDER_WIRE_API": binding.requirements.wire_api.value,
                "COPILOT_PROVIDER_API_KEY": lease.token,
                "COPILOT_MODEL": binding.model,
            }

    async def _record_async(self, live: _LiveExecution, *, direction: str, payload: dict[str, Any]) -> None:
        async with live.event_lock:
            record = live.record
            if record.capture_error:
                raise RuntimeError(record.capture_error)
            try:
                sanitized = json.loads(self._redact(json.dumps(payload, ensure_ascii=True), live.secrets))
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

    @staticmethod
    def _redact(text: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    async def _stderr_async(self, live: _LiveExecution, reader: asyncio.StreamReader) -> None:
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
            if live.approvals:
                await live.approvals.cancel_async()
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

    async def decide_permission_async(self, *, execution_id: UUID, approval_id: UUID, allow: bool, actor: str) -> None:
        """
        Resolve a live interactive permission through its single-winner arbiter.

        Raises:
            ValueError: The execution cannot accept a permission decision.
        """
        live = self._live.get(execution_id)
        if not live or not live.record.interactive or live.close_requested or not live.approvals:
            raise ValueError("Execution cannot accept permission decisions")
        await live.approvals.decide_async(
            approval_id=approval_id, decision="operator_allow" if allow else "operator_deny", actor=actor
        )

    async def control_held_async(self, *, execution_id: UUID, action: str, actor: str) -> None:
        """
        Continue or extend an interactive hold without exceeding its absolute lifetime.

        Raises:
            ValueError: The execution is not held, expired, or the action is invalid.
        """
        async with self._admission:
            live = self._live.get(execution_id)
            now = datetime.now(UTC)
            if not live or live.record.state != AgentExecutionState.HELD or not live.record.interactive:
                raise ValueError("Execution is not held")
            if live.operation is not None:
                raise ValueError("Turn finalization is still in progress")
            record = live.record
            cap = record.created_at + timedelta(seconds=record.profile.lifetime_seconds)
            if record.connection_state != AgentConnectionState.READY or live.close_requested:
                raise ValueError("Agent session is unavailable; close this execution and start fresh")
            if record.held_until is None or now >= min(record.held_until, cap):
                raise ValueError("Hold expired; start a fresh execution")
            if action not in ("continue", "extend"):
                raise ValueError("Unknown hold action")
            await self._record_async(
                live,
                direction="lifecycle",
                payload={
                    "type": "execution.operator_action",
                    "action": action,
                    "actor": actor,
                },
            )
            if action == "continue":
                record.state, record.held_until = AgentExecutionState.IDLE, None
                record.last_activity_at = now
            else:
                record.held_until = min(cap, now + timedelta(seconds=record.profile.interactive_hold_seconds))
            await self.store.save_async(record)

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
            if live.approvals:
                try:
                    await live.approvals.cancel_async()
                except Exception:
                    logger.exception("Permission finalization failed; resource cleanup will still proceed")
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
            if live.inference_lease and self._relay:
                await self._relay.revoke_async(live.inference_lease)
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
            if persistence_errors:
                raise ExceptionGroup("Execution evidence persistence failed during cleanup", persistence_errors)

    async def close_conversation_async(self, *, target_id: str, conversation_id: str) -> None:
        """Release the execution associated with an attack-owned conversation."""
        for record in tuple(self.records.values()):
            if record.target_id == target_id and record.conversation_id == conversation_id:
                await self.close_execution_async(execution_id=record.id, reason="conversation_finished")

    async def transcript_updated_async(self, conversation_id: str) -> None:
        """Publish the caller's completed memory write so reconnected viewers can reload safely."""
        for record in self.records.values():
            if record.conversation_id == conversation_id:
                record.transcript_revision += 1
                await self.store.save_async(record)
                return

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
                hold_expired = record.held_until is not None and now >= record.held_until
                if (expired or idle or hold_expired) and record.state not in (
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
            if self._relay:
                await self._relay.close_async()
            if self._file_lock:
                await asyncio.to_thread(self._file_lock.release)
                self._file_lock = None
        if errors:
            raise ExceptionGroup("Agent execution cleanup failed", errors)
