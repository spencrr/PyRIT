# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from pyrit.agent.approvals import AgentApprovals
from pyrit.models.agent_execution import AgentApproval, AgentExecution, AgentProfile


def make_approvals(*, timeout: float = 1) -> AgentApprovals:
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True, approval_timeout_seconds=timeout),
    )
    return AgentApprovals(record=record, changed=AsyncMock())


def options() -> list[dict[str, str]]:
    return [
        {"option_id": "yes", "name": "Allow", "kind": "allow_once"},
        {"option_id": "no", "name": "Deny", "kind": "reject_once"},
    ]


async def wait_for_approval_async(approvals: AgentApprovals) -> AgentApproval:
    async with asyncio.timeout(3):
        while not approvals.record.approvals:
            await asyncio.sleep(0)
    return approvals.record.approvals[0]


async def test_permission_has_one_durable_winner_and_idempotent_retries() -> None:
    approvals = make_approvals()
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read file", options=options()))
    approval = await wait_for_approval_async(approvals)
    await approvals.decide_async(approval_id=approval.id, decision="operator_allow", actor="operator")
    await approvals.decide_async(approval_id=approval.id, decision="operator_allow", actor="operator")
    assert await pending == "yes"
    with pytest.raises(ValueError, match="already resolved"):
        await approvals.decide_async(approval_id=approval.id, decision="operator_deny", actor="another")
    assert approval.actor == "operator"
    assert approvals.changed.await_count == 2


async def test_timeout_denial_is_not_attributed_to_operator() -> None:
    approvals = make_approvals(timeout=0.03)
    assert await approvals.request_async(tool_call_id="tool", title="Read", options=options()) == "no"
    approval = approvals.record.approvals[0]
    assert approval.decision == "timeout_deny"
    assert approval.actor == "policy"
    assert approvals.wait_seconds >= 0.02


async def test_cancellation_resolves_wait_and_denies_new_requests() -> None:
    approvals = make_approvals()
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
    await wait_for_approval_async(approvals)
    await approvals.cancel_async()
    assert await pending is None
    assert approvals.record.approvals[0].decision == "cancelled"
    assert await approvals.request_async(tool_call_id="next", title="Read", options=options()) is None


async def test_persistence_failure_never_grants_permission() -> None:
    approvals = make_approvals()
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
    await wait_for_approval_async(approvals)
    approvals.changed.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await approvals.decide_async(
            approval_id=approvals.record.approvals[0].id, decision="operator_allow", actor="operator"
        )
    assert not pending.done()
    assert approvals.record.approvals[0].decision is None
    approvals.changed.side_effect = None
    await approvals.cancel_async()
    assert await pending is None


@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
async def test_runtime_settlement_waits_for_inflight_operator_commit(trigger: str) -> None:
    approvals = make_approvals(timeout=0.2 if trigger == "timeout" else 3)
    committing = asyncio.Event()
    release = asyncio.Event()
    runtime_settling = asyncio.Event()
    original = approvals._decide_async

    async def changed_async(approval: AgentApproval) -> None:
        if approval.decision == "operator_allow":
            committing.set()
            await release.wait()

    async def tracked_decide_async(*, approval_id: UUID, decision: str, actor: str, accept_resolved: bool) -> None:
        if decision in ("timeout_deny", "cancelled"):
            runtime_settling.set()
        await original(approval_id=approval_id, decision=decision, actor=actor, accept_resolved=accept_resolved)

    approvals.changed = AsyncMock(side_effect=changed_async)
    with patch.object(approvals, "_decide_async", side_effect=tracked_decide_async):
        pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
        approval = await wait_for_approval_async(approvals)
        operator = asyncio.create_task(
            approvals.decide_async(approval_id=approval.id, decision="operator_allow", actor="operator")
        )
        await asyncio.wait_for(committing.wait(), 3)
        cancellation = asyncio.create_task(approvals.cancel_async()) if trigger == "cancel" else None
        try:
            await asyncio.wait_for(runtime_settling.wait(), 3)
            assert not operator.done()
            assert not pending.done()
        finally:
            release.set()
        await operator
        if cancellation is not None:
            await cancellation
        assert await pending == "yes"
    assert approval.decision == "operator_allow"
    assert approval.actor == "operator"
    assert approvals.changed.await_count == 2


async def test_cancelled_operator_request_finishes_commit_before_propagating_cancellation() -> None:
    approvals = make_approvals()
    committing = asyncio.Event()
    release = asyncio.Event()

    async def changed_async(approval: AgentApproval) -> None:
        if approval.decision is not None:
            committing.set()
            await release.wait()

    approvals.changed = AsyncMock(side_effect=changed_async)
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
    approval = await wait_for_approval_async(approvals)
    operator = asyncio.create_task(
        approvals.decide_async(approval_id=approval.id, decision="operator_allow", actor="operator")
    )
    await asyncio.wait_for(committing.wait(), 3)
    operator.cancel()
    await asyncio.sleep(0)
    assert not operator.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operator
    assert await pending == "yes"
    assert approval.decision == "operator_allow"


async def test_cancel_waits_for_pending_publication_and_resolves_it() -> None:
    approvals = make_approvals()
    publishing = asyncio.Event()
    release = asyncio.Event()

    async def changed_async(approval: AgentApproval) -> None:
        if approval.decision is None:
            publishing.set()
            await release.wait()

    approvals.changed = AsyncMock(side_effect=changed_async)
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
    await asyncio.wait_for(publishing.wait(), 3)
    cancellation = asyncio.create_task(approvals.cancel_async())
    await asyncio.sleep(0)
    assert not cancellation.done()
    release.set()
    await cancellation
    assert await pending is None
    assert approvals.record.approvals[0].decision == "cancelled"


async def test_failed_pending_publication_does_not_leave_an_actionable_request() -> None:
    approvals = make_approvals()
    approvals.changed.side_effect = OSError("journal unavailable")
    with pytest.raises(OSError, match="journal unavailable"):
        await approvals.request_async(tool_call_id="tool", title="Read", options=options())
    assert not approvals.record.approvals
    assert not approvals._pending
    with pytest.raises(ValueError, match="not found"):
        await approvals.decide_async(approval_id=UUID(int=1), decision="operator_allow", actor="operator")


async def test_cancelled_request_during_publication_leaves_no_unanswered_permission() -> None:
    approvals = make_approvals()
    publishing = asyncio.Event()
    release = asyncio.Event()

    async def changed_async(approval: AgentApproval) -> None:
        if approval.decision is None:
            publishing.set()
            await release.wait()

    approvals.changed = AsyncMock(side_effect=changed_async)
    pending = asyncio.create_task(approvals.request_async(tool_call_id="tool", title="Read", options=options()))
    await asyncio.wait_for(publishing.wait(), 3)
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not approvals._pending
    assert approvals.record.approvals[0].decision == "cancelled"
