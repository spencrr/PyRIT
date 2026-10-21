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
from pyrit.agent.checkpoint import (
    checkpoint_agent_version,
    checkpoint_layout,
    checkpoint_transcript_async,
    require_checkpoint_ready,
)
from pyrit.agent.checkpoint_archive import CheckpointArchive
from pyrit.agent.environment import (
    AcpByteReader,
    CheckpointRestore,
    ExecutionSession,
    ExecutionSpec,
    create_execution_session,
    validate_environment_template,
)
from pyrit.agent.harness import (
    inference_environment,
    launch_environment,
    resolve_credential_values,
    validate_harness_authentication,
)
from pyrit.agent.tool_calls import AgentToolProjection, project_agent_tools
from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.memory.agent_checkpoint_store import AgentCheckpointStore
from pyrit.memory.agent_execution_store import AgentExecutionStore
from pyrit.models.agent_checkpoint import AgentCheckpoint, AgentCheckpointStatus
from pyrit.models.agent_execution import (
    AgentApproval,
    AgentConnectionState,
    AgentExecution,
    AgentExecutionEvent,
    AgentExecutionState,
    AgentPermissionPolicy,
    AgentProfile,
    AgentTargetConfiguration,
    AgentTurn,
    AgentTurnStatus,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.agent.acp_connection import AcpConnection
    from pyrit.agent.inference_relay import InferenceLease, InferenceRelay
    from pyrit.agent.model_binding import ResolvedModelBinding
    from pyrit.memory import MemoryInterface


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
    model_binding: "ResolvedModelBinding | None" = None
    inference_lease: "InferenceLease | None" = None
    inference_environment: dict[str, str] = field(default_factory=dict)
    approvals: AgentApprovals | None = None
    checkpoint_task: asyncio.Task[Any] | None = None
    restoration: tuple[AgentCheckpoint, bytes, bytes] | None = None
    replaying_history: bool = False


class AgentExecutionManager:
    """Own resources, not attack decisions. One manager exclusively owns its storage directory."""

    MAX_ERROR_CHARS = 2048

    def __init__(self, *, root: Path, max_executions: int = 4) -> None:
        """
        Configure storage and capacity without acquiring resources.

        Raises:
            ValueError: Capacity is not positive.
        """
        if max_executions < 1:
            raise ValueError("max_executions must be positive")
        self.store = AgentExecutionStore(root=root)
        self.checkpoints = AgentCheckpointStore(root / "checkpoints")
        self.max_executions = max_executions
        self.records: dict[UUID, AgentExecution] = {}
        self._live: dict[UUID, _LiveExecution] = {}
        self._admission = asyncio.Lock()
        self._started = False
        self._closed = False
        self._file_lock: BaseFileLock | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._checkpoint_tasks: set[asyncio.Task[Any]] = set()
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
        """Whether allocations or checkpoint publication still own runtime work."""
        return bool(self._live or self._checkpoint_tasks)

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
        superseded = {record.parent_execution_id for record in candidates if record.parent_execution_id}
        candidates = [record for record in candidates if record.id not in superseded]
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
        for approval in record.approvals:
            if approval.decision is None:
                approval.decision, approval.actor = "cancelled", "interrupted_runtime"
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
        if profile.target_registry_name is not None and model_binding is None:
            raise ValueError("A target-backed agent execution requires a resolved model binding")
        configuration = AgentTargetConfiguration.from_profile(profile)
        validate_harness_authentication(
            harness=configuration.harness_profile,
            target_backed=model_binding is not None,
        )
        validate_environment_template(configuration.environment_template)
        await self.start_async()
        async with self._admission:
            if self._closed:
                raise RuntimeError("Agent execution manager is closing")
            existing = self.latest_execution(target_id=target_id, conversation_id=conversation_id)
            if existing:
                if existing.interactive != interactive:
                    raise ValueError("Execution interaction mode cannot change within a conversation")
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
                live.secrets = tuple(resolve_credential_values(credential_env=profile.credential_env).values())
                self.records[record.id] = record
                self._live[record.id] = live
            record = live.record
            live.cancel_requested = False
            if live.approvals:
                live.approvals.cancelled = False
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
            turn.event_end_cursor = record.event_count
            projection = await self.tool_calls_async(execution_id=record.id, turn_id=turn.id)
            turn.tool_call_messages = len(projection.messages)
            turn.tool_call_omissions = projection.omitted
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
            turn.error = self._redact(self._describe_error(error), live.secrets)
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
            environment=create_execution_session(execution=record, directory=self.store.directory(record.id)),
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

    async def tool_calls_async(self, *, execution_id: UUID, turn_id: UUID) -> AgentToolProjection:
        """
        Read one turn's retained reports through the shared canonical tool projection.

        Returns:
            AgentToolProjection: Scorer-compatible messages and conservative omission reasons.

        Raises:
            ValueError: The turn does not belong to this execution.
        """
        record = self.records[execution_id]
        turn = next((turn for turn in record.turns if turn.id == turn_id), None)
        if turn is None:
            raise ValueError("Evidence turn does not belong to the execution")
        events: list[AgentExecutionEvent] = []
        cursor = turn.event_start_cursor
        while turn.event_end_cursor is None or cursor < turn.event_end_cursor:
            page = await self.store.events_async(execution_id=execution_id, after=cursor, limit=500)
            if not page.events:
                break
            events.extend(
                event
                for event in page.events
                if event.turn_id == turn_id
                and (turn.event_end_cursor is None or event.sequence <= turn.event_end_cursor)
            )
            cursor = page.next_cursor
        return project_agent_tools(
            events=events,
            execution_id=execution_id,
            turn_id=turn_id,
            conversation_id=record.conversation_id,
        )

    async def _launch_async(self, live: _LiveExecution) -> None:
        from pyrit.agent.acp_connection import AcpConnection, RecordingTransport

        profile = live.record.profile
        async with asyncio.timeout(profile.startup_timeout_seconds):
            credentials = resolve_credential_values(credential_env=profile.credential_env)
            live.secrets = tuple(credentials.values())
            live.environment.reserve()
            await self.store.save_async(live.record)
            if live.model_binding is not None:
                await self._bind_inference_async(live)
            restore = None
            if live.restoration is not None:
                checkpoint, workspace, state = live.restoration
                restore = CheckpointRestore(layout=checkpoint.layout, workspace=workspace, state=state)
            spec = ExecutionSpec(
                command=profile.command,
                environment={
                    **launch_environment(kind=profile.harness_kind, home=live.environment.harness_home),
                    **live.inference_environment,
                },
                credentials=credentials,
                restore=restore,
            )
            try:
                await live.environment.launch_async(spec)
            finally:
                await self.store.save_async(live.record)

            async def record_async(direction: str, payload: dict[str, Any]) -> None:
                if live.replaying_history and direction == "incoming" and payload.get("method") == "session/update":
                    direction = "restore"
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
                approvals=live.approvals,
                status_changed=status_changed_async,
            )
            live.stderr_task = asyncio.create_task(self._stderr_async(live, live.environment.stderr))
            restore = live.restoration[0] if live.restoration else None
            live.replaying_history = restore is not None
            live.record.agent_info = await live.connection.initialize_async(
                cwd=live.environment.agent_cwd,
                model="" if live.model_binding else live.record.profile.model,
                authentication_method=None if live.model_binding else live.record.profile.authentication_method,
                **(
                    {"session_id": restore.session_id, "expected_agent_version": restore.agent_version}
                    if restore
                    else {}
                ),
            )
            live.replaying_history = False
            live.restoration = None
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
            await self._relay.start_async()
            base_url = await live.environment.inference_endpoint_async(relay_url=self._relay.url)

            async def record_async(payload: dict[str, Any]) -> None:
                await self._record_async(live, direction="inference", payload=payload)

            lease = InferenceLease(
                binding=binding,
                record=record_async,
                timeout_seconds=live.record.profile.turn_timeout_seconds,
                max_calls=live.record.profile.max_inference_requests,
                capture_content=live.record.profile.capture_inference_content,
                active=live.restoration is None,
            )
            self._relay.add(lease)
            live.inference_lease = lease
            live.secrets = (*live.secrets, lease.token)
            live.record.inference_target_hash = binding.identifier_hash
            live.record.inference_protocol = binding.requirements.wire_api
            live.inference_environment = inference_environment(
                kind=live.record.profile.harness_kind,
                model=binding.model,
                wire_api=binding.requirements.wire_api,
                base_url=base_url,
                token=lease.token,
            )

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
        if live.checkpoint_task is not None and live.checkpoint_task is not asyncio.current_task():
            try:
                await await_task_completion_async(live.checkpoint_task)
            finally:
                if execution_id in self._live:
                    await self._close_execution_async(live, reason=reason)
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
        record = self.latest_execution(conversation_id=conversation_id)
        if record is not None:
            record.transcript_revision += 1
            await self.store.save_async(record)

    async def checkpoint_status_async(self, execution_id: UUID) -> AgentCheckpointStatus:
        """
        Describe checkpoint readiness without returning private archive contents.

        Returns:
            AgentCheckpointStatus: Current save/restore availability and limitations.

        Raises:
            ValueError: Stored checkpoint metadata is corrupt.
        """
        await self.start_async()
        record = self.records[execution_id]
        latest = self.latest_execution(conversation_id=record.conversation_id)
        if record.checkpoint_id:
            deleting = await self.checkpoints.deletion_pending_async(record.checkpoint_id)
            try:
                checkpoint = await self.checkpoints.get_async(record.checkpoint_id)
            except FileNotFoundError:
                return AgentCheckpointStatus(
                    save_available=False,
                    deletion_pending=deleting,
                    detail="Checkpoint deletion is incomplete; retry deleting retained private state."
                    if deleting
                    else "Checkpoint was deleted or is unavailable.",
                )
            return AgentCheckpointStatus(
                save_available=False,
                checkpoint=checkpoint,
                restore_available=not deleting and latest is record and record.state == AgentExecutionState.CLOSED,
                deletion_pending=deleting,
                detail="Sensitive native state retained. Restore requires unchanged transcript and configuration."
                if latest is record and not deleting
                else "Checkpoint deletion is incomplete; retry deleting retained private state."
                if deleting
                else "A later allocation exists; this checkpoint cannot rewind the conversation.",
            )
        try:
            require_checkpoint_ready(record)
            await self.checkpoints.ensure_capacity_async()
            live = self._live.get(record.id)
            if latest is not record or live is None or live.operation is not None or live.checkpoint_task is not None:
                raise ValueError("Execution finalization is still in progress or a later allocation exists")
        except ValueError as error:
            return AgentCheckpointStatus(save_available=False, detail=str(error))
        return AgentCheckpointStatus(
            save_available=True, detail="Save sensitive native state and close this allocation."
        )

    async def save_checkpoint_async(
        self,
        *,
        execution_id: UUID,
        memory: "MemoryInterface",
        actor: str,
    ) -> AgentCheckpoint:
        """
        Save and close the latest completed allocation; callers must authorize private-state retention.

        Returns:
            AgentCheckpoint: Immutable state bound to the original transcript and journal cutoff.

        Raises:
            ValueError: The latest allocation is not eligible for capture.
        """
        await self.start_async()
        async with self._admission:
            if self._closed:
                raise ValueError("Execution manager is closing")
            record = self.records[execution_id]
            require_checkpoint_ready(record)
            await self.checkpoints.ensure_capacity_async()
            live = self._live.get(execution_id)
            if (
                live is None
                or live.operation is not None
                or live.checkpoint_task is not None
                or live.close_task is not None
                or self.latest_execution(conversation_id=record.conversation_id) is not record
            ):
                raise ValueError("Only the latest settled allocation can be saved")
            transcript = await checkpoint_transcript_async(
                memory=memory,
                conversation_id=record.conversation_id,
                executions=[item for item in self.records.values() if item.conversation_id == record.conversation_id],
            )
            require_checkpoint_ready(record)
            checkpoint_id = await self.checkpoints.reserve_async()
            record.state = AgentExecutionState.CLOSING
            record.close_reason = "checkpoint_capture"
            live.checkpoint_task = asyncio.create_task(
                self._save_checkpoint_async(
                    live,
                    memory=memory,
                    actor=actor,
                    transcript=transcript,
                    checkpoint_id=checkpoint_id,
                )
            )
            self._checkpoint_tasks.add(live.checkpoint_task)
            live.checkpoint_task.add_done_callback(self._checkpoint_tasks.discard)
        return await await_task_completion_async(live.checkpoint_task)

    async def _save_checkpoint_async(
        self,
        live: _LiveExecution,
        *,
        memory: "MemoryInterface",
        actor: str,
        transcript: str,
        checkpoint_id: UUID,
    ) -> AgentCheckpoint:
        record = live.record
        checkpoint: AgentCheckpoint | None = None
        published = False
        try:
            await self.store.save_async(record)
            layout = checkpoint_layout(record)
            if live.inference_lease and self._relay:
                await self._relay.revoke_async(live.inference_lease)
            workspace, state = await live.environment.capture_checkpoint_async(layout=layout, secrets=live.secrets)
            await self._close_execution_async(live, reason="checkpoint_saved")
            if record.cleanup_error or record.capture_error:
                raise ValueError("Resource closure or evidence capture failed; checkpoint was not saved")
            if transcript != await checkpoint_transcript_async(memory=memory, conversation_id=record.conversation_id):
                raise ValueError("Conversation changed during checkpoint capture")
            journal = await asyncio.to_thread(
                self.checkpoints._read,
                path=self.store.directory(record.id) / "events.jsonl",
                maximum=record.profile.max_evidence_bytes,
            )
            if record.image_id is None or record.session_id is None:
                raise ValueError("Native checkpoint identities disappeared during capture")
            checkpoint = AgentCheckpoint(
                id=checkpoint_id,
                source_execution_id=record.id,
                conversation_id=record.conversation_id,
                target_id=record.target_id,
                profile=record.profile,
                image_id=record.image_id,
                session_id=record.session_id,
                layout=layout,
                agent_version=checkpoint_agent_version(record),
                transcript_sha256=transcript,
                event_cursor=record.event_count,
                journal=self.checkpoints.describe(journal),
                workspace=self.checkpoints.describe(workspace),
                harness_state=self.checkpoints.describe(state),
                actor=actor,
            )
            await self.checkpoints.save_async(checkpoint=checkpoint, workspace=workspace, state=state)
            published = True
            record.checkpoint_id = checkpoint.id
            await self.store.save_async(record)
            return checkpoint
        except BaseException:
            record.checkpoint_id = None
            if published and checkpoint is not None:
                await self.checkpoints.delete_async(checkpoint.id)
            await self._close_execution_async(live, reason="checkpoint_failed")
            raise
        finally:
            await self.checkpoints.release_reservation_async(checkpoint_id)

    async def restore_checkpoint_async(
        self,
        *,
        checkpoint_id: UUID,
        profile: AgentProfile,
        target_id: str,
        memory: "MemoryInterface",
        model_binding: "ResolvedModelBinding",
    ) -> AgentExecution:
        """
        Create one new allocation for the unchanged same-chat checkpoint; never replay a prompt.

        Returns:
            AgentExecution: The new idle allocation. Failed attempts remain visible and prevent rewind.

        Raises:
            ValueError: The checkpoint, transcript, configuration, or admission state is incompatible.
        """
        await self.start_async()
        async with self._admission:
            if self._closed or len(self._live) >= self.max_executions:
                raise ValueError("Execution manager is closing or at capacity")
            checkpoint, workspace, state = await self.checkpoints.load_async(checkpoint_id)
            source = self.records.get(checkpoint.source_execution_id)
            if (
                source is None
                or source.state != AgentExecutionState.CLOSED
                or source.checkpoint_id != checkpoint_id
                or self.latest_execution(conversation_id=checkpoint.conversation_id) is not source
            ):
                raise ValueError(
                    "Only the latest saved allocation can be restored; rewinds and forks are not supported"
                )
            if profile != checkpoint.profile or target_id != checkpoint.target_id:
                raise ValueError("Target configuration changed; restore requires the original pinned target")
            if checkpoint_layout(source) != checkpoint.layout:
                raise ValueError("Installed harness checkpoint layout changed")
            if source.inference_target_hash != model_binding.identifier_hash:
                raise ValueError("Model binding changed; restore requires the checkpoint's model")
            if checkpoint.transcript_sha256 != await checkpoint_transcript_async(
                memory=memory,
                conversation_id=checkpoint.conversation_id,
            ):
                raise ValueError("Conversation changed since saving; restoration would rewind or mismatch history")
            journal = await asyncio.to_thread(
                self.checkpoints._read,
                path=self.store.directory(source.id) / "events.jsonl",
                maximum=source.profile.max_evidence_bytes,
            )
            if (
                self.checkpoints.describe(journal) != checkpoint.journal
                or source.event_count != checkpoint.event_cursor
            ):
                raise ValueError("Original execution evidence changed")
            workspace = await asyncio.to_thread(
                CheckpointArchive.normalize,
                raw=workspace,
                root="/workspace",
                portable=True,
            )
            state = await asyncio.to_thread(
                CheckpointArchive.normalize,
                raw=state,
                root=checkpoint.layout.state_root,
                portable=True,
            )
            record = AgentExecution(
                owner_id=self.store.root.resolve().as_posix(),
                conversation_id=checkpoint.conversation_id,
                target_id=target_id,
                profile=profile,
                image_id=checkpoint.image_id,
                interactive=source.interactive,
                parent_execution_id=source.id,
                restored_from_checkpoint_id=checkpoint.id,
            )
            live = self._make_live(record)
            live.model_binding = model_binding
            live.restoration = (checkpoint, workspace, state)
            self.records[record.id] = record
            self._live[record.id] = live
            live.checkpoint_task = asyncio.create_task(self._restore_checkpoint_async(live, memory=memory))
            self._checkpoint_tasks.add(live.checkpoint_task)
            live.checkpoint_task.add_done_callback(self._checkpoint_tasks.discard)
        return await await_task_completion_async(live.checkpoint_task)

    async def _restore_checkpoint_async(self, live: _LiveExecution, *, memory: "MemoryInterface") -> AgentExecution:
        record = live.record
        assert live.restoration is not None
        checkpoint = live.restoration[0]
        try:
            await self.store.save_async(record)
            await self._record_async(
                live,
                direction="lifecycle",
                payload={
                    "type": "checkpoint.restore_started",
                    "checkpoint_id": str(checkpoint.id),
                    "source_execution_id": str(checkpoint.source_execution_id),
                },
            )
            live.startup = asyncio.create_task(self._launch_async(live))
            await live.startup
            if checkpoint.transcript_sha256 != await checkpoint_transcript_async(
                memory=memory,
                conversation_id=record.conversation_id,
            ):
                raise ValueError("Conversation changed during restore; restored allocation was closed")
            await self._record_async(live, direction="lifecycle", payload={"type": "checkpoint.restore_completed"})
            if live.inference_lease is not None:
                live.inference_lease.active = True
            record.state = AgentExecutionState.IDLE
            record.last_activity_at = datetime.now(UTC)
            await self.store.save_async(record)
            return record
        except BaseException:
            await self._close_execution_async(live, reason="checkpoint_restore_failed")
            raise
        finally:
            live.restoration = None
            live.checkpoint_task = None

    async def delete_checkpoint_async(self, checkpoint_id: UUID) -> None:
        """
        Delete private checkpoint bytes, not source execution evidence or running resources.

        Raises:
            ValueError: A checkpoint transition still owns the state.
        """
        await self.start_async()
        async with self._admission:
            if self._checkpoint_tasks:
                raise ValueError("A checkpoint transition is in progress; retry deletion after it settles")
            await self.checkpoints.delete_async(checkpoint_id)
            self.store.notify()

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
            for task in tuple(self._checkpoint_tasks):
                try:
                    await await_task_completion_async(task)
                except Exception as error:
                    errors.append(error)
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
