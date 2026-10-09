# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Server-owned permission arbitration; no browser connection owns a waiting turn."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_execution import AgentApproval, AgentExecution


class AgentApprovals:
    """Resolve a permission once, persist before replying, and account for human wait time."""

    def __init__(self, *, record: AgentExecution, changed: Callable[[AgentApproval], Awaitable[None]]) -> None:
        """Bind decisions to one execution identity."""
        self.record = record
        self.changed = changed
        self._pending: dict[UUID, asyncio.Future[str | None]] = {}
        self._lock = asyncio.Lock()
        self._waiting_since: float | None = None
        self._waited = 0.0
        self.cancelled = False

    @property
    def wait_seconds(self) -> float:
        """Union of time spent waiting for one or more human decisions."""
        return self._waited + (monotonic() - self._waiting_since if self._waiting_since is not None else 0)

    async def request_async(self, *, tool_call_id: str, title: str, options: list[dict[str, str]]) -> str | None:
        """
        Wait for an explicit one-time decision or a bounded fail-closed timeout.

        Returns:
            str | None: The selected ACP option, or cancellation.

        Raises:
            asyncio.CancelledError: The request was cancelled after pending state was settled.
        """
        if self.cancelled:
            return None
        approval = AgentApproval(
            tool_call_id=tool_call_id,
            title=title,
            options=options,
            turn_id=self.record.turns[-1].id if self.record.turns else None,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.record.profile.approval_timeout_seconds),
        )
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        try:
            registered = await await_task_completion_async(
                asyncio.create_task(self._register_async(approval=approval, future=future))
            )
            if not registered:
                return None
            try:
                remaining = max(0, (approval.expires_at - datetime.now(UTC)).total_seconds())
                return await asyncio.wait_for(asyncio.shield(future), remaining)
            except TimeoutError:
                await self._settle_if_pending_async(approval_id=approval.id, decision="timeout_deny", actor="policy")
                return await future
        except asyncio.CancelledError:
            if approval.id in self._pending:
                await self._settle_if_pending_async(approval_id=approval.id, decision="cancelled", actor="runtime")
            raise
        finally:
            self._pending.pop(approval.id, None)
            self._finish_wait()

    async def _register_async(self, *, approval: AgentApproval, future: asyncio.Future[str | None]) -> bool:
        async with self._lock:
            if self.cancelled:
                return False
            self.record.approvals.append(approval)
            self._pending[approval.id] = future
            if self._waiting_since is None:
                self._waiting_since = monotonic()
            try:
                await self.changed(approval)
            except BaseException:
                self.record.approvals.remove(approval)
                self._pending.pop(approval.id, None)
                self._finish_wait()
                raise
            return True

    def _finish_wait(self) -> None:
        if not self._pending and self._waiting_since is not None:
            self._waited += monotonic() - self._waiting_since
            self._waiting_since = None

    async def decide_async(self, *, approval_id: UUID, decision: str, actor: str) -> None:
        """
        Apply a single durable decision; identical retries are idempotent.

        Raises:
            ValueError: The request, decision, or offered permission is invalid.
        """
        await await_task_completion_async(
            asyncio.create_task(
                self._decide_async(
                    approval_id=approval_id,
                    decision=decision,
                    actor=actor,
                    accept_resolved=False,
                )
            )
        )

    async def _settle_if_pending_async(self, *, approval_id: UUID, decision: str, actor: str) -> None:
        await await_task_completion_async(
            asyncio.create_task(
                self._decide_async(
                    approval_id=approval_id,
                    decision=decision,
                    actor=actor,
                    accept_resolved=True,
                )
            )
        )

    async def _decide_async(self, *, approval_id: UUID, decision: str, actor: str, accept_resolved: bool) -> None:
        async with self._lock:
            approval = next((item for item in self.record.approvals if item.id == approval_id), None)
            if approval is None:
                raise ValueError("Permission request not found")
            if approval.decision is not None:
                if approval.decision == decision or accept_resolved:
                    return
                raise ValueError("Permission request already resolved")
            if approval_id not in self._pending:
                raise ValueError("Permission request is no longer pending")
            if decision not in ("operator_allow", "operator_deny", "timeout_deny", "cancelled"):
                raise ValueError("Unsupported permission decision")
            if decision.startswith("operator") and datetime.now(UTC) >= approval.expires_at:
                raise ValueError("Permission request has expired")
            kind = "allow_once" if decision == "operator_allow" else "reject_once"
            option = next((item["option_id"] for item in approval.options if item["kind"] == kind), None)
            if option is None and decision == "operator_allow":
                raise ValueError("Harness did not offer a one-time approval")
            approval.decision, approval.option_id, approval.actor = decision, option, actor
            try:
                await self.changed(approval)
            except BaseException:
                approval.decision = approval.option_id = approval.actor = None
                raise
            future = self._pending.get(approval.id)
            if future is not None and not future.done():
                future.set_result(None if decision == "cancelled" else option)

    async def cancel_async(self) -> None:
        """Resolve all pending permissions as cancelled before cancelling the harness."""
        self.cancelled = True
        for approval_id in tuple(self._pending):
            await self._settle_if_pending_async(approval_id=approval_id, decision="cancelled", actor="runtime")
