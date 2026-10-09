# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-backend administration of owned agent executions."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from pyrit.agent.runtime import get_agent_execution_manager
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.services.agent_execution_service import resolve_conversation_execution_async
from pyrit.models.agent_execution import (
    AgentConnectionState,
    AgentEnvironment,
    AgentEventPage,
    AgentExecution,
    AgentExecutionState,
    AgentTurn,
)

router = APIRouter(prefix="/executions", tags=["executions"], dependencies=[Depends(require_admin)])
conversation_router = APIRouter(prefix="/attacks", tags=["attacks"])


class ConversationExecution(BaseModel):
    """Operator-visible evidence without infrastructure identities or credential references."""

    id: UUID
    conversation_id: str
    state: AgentExecutionState
    environment: AgentEnvironment
    model: str
    turns: list[AgentTurn]
    capture_error: str | None
    close_reason: str | None
    source_coverage: str
    artifacts: list[str]
    event_count: int
    connection_state: AgentConnectionState = AgentConnectionState.DISCONNECTED
    expires_at: datetime | None = None
    last_event_at: datetime | None = None
    transcript_revision: int = 0


async def _resolve_async(
    *, attack_result_id: str, conversation_id: str, execution_id: UUID | None = None
) -> AgentExecution | None:
    try:
        return await resolve_conversation_execution_async(
            attack_result_id=attack_result_id, conversation_id=conversation_id, execution_id=execution_id
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@conversation_router.get("/{attack_result_id}/conversations/{conversation_id}/execution")
async def get_conversation_execution_async(attack_result_id: str, conversation_id: str) -> ConversationExecution | None:
    """
    Observe the execution through the same attack access boundary as the chat transcript.

    Returns:
        ConversationExecution | None: Operator-visible state, or not yet provisioned.
    """
    record = await _resolve_async(attack_result_id=attack_result_id, conversation_id=conversation_id)
    if record is None:
        return None
    return ConversationExecution(
        id=record.id,
        conversation_id=record.conversation_id,
        state=record.state,
        environment=record.profile.environment,
        model=record.profile.model,
        turns=[turn.model_copy(deep=True) for turn in record.turns],
        capture_error=record.capture_error,
        close_reason=record.close_reason,
        source_coverage=record.source_coverage,
        artifacts=list(record.artifacts),
        event_count=record.event_count,
        connection_state=record.connection_state,
        expires_at=record.created_at + timedelta(seconds=record.profile.lifetime_seconds),
        last_event_at=record.last_event_at,
        transcript_revision=record.transcript_revision,
    )


@conversation_router.get("/{attack_result_id}/conversations/{conversation_id}/execution/stream")
async def stream_conversation_execution_async(
    request: Request,
    attack_result_id: str,
    conversation_id: str,
    after: int = Query(0, ge=0),
    execution_id: UUID | None = None,
) -> StreamingResponse:
    """
    Stream durable evidence and state; refresh HTTP authorization every minute.

    Returns:
        StreamingResponse: SSE state/events with execution-scoped resume cursor.
    """
    await _resolve_async(attack_result_id=attack_result_id, conversation_id=conversation_id)
    manager = get_agent_execution_manager()

    async def stream_async() -> AsyncIterator[str]:
        cursor = after
        identity = execution_id
        previous_state = ""
        deadline = monotonic() + 60
        with manager.store.subscribe() as changed:
            while monotonic() < deadline:
                changed.clear()
                if manager.is_closed:
                    yield _sse("reconnect", {"reason": "runtime_replaced"})
                    return
                current = await get_conversation_execution_async(attack_result_id, conversation_id)
                if current is not None and current.id != identity:
                    cursor, identity = 0, current.id
                    yield _sse("reset", {"execution_id": str(identity)})
                state = current.model_dump_json() if current else "null"
                if state != previous_state:
                    previous_state = state
                    yield f"event: state\ndata: {state}\n\n"
                if current is not None:
                    while True:
                        if monotonic() >= deadline:
                            yield _sse("reconnect", {"reason": "reauthorize"})
                            return
                        page = await get_conversation_execution_events_async(
                            attack_result_id,
                            conversation_id,
                            current.id,
                            after=cursor,
                            limit=100,
                        )
                        if page.next_cursor == cursor:
                            break
                        if page.next_cursor < cursor:
                            raise RuntimeError("Execution journal cursor moved backwards")
                        cursor = page.next_cursor
                        yield _sse("events", {"execution_id": str(identity), **page.model_dump(mode="json")})
                        if await request.is_disconnected():
                            return
                    if current.state == AgentExecutionState.CLOSED:
                        yield _sse("end", {"reason": "execution_closed"})
                        return
                if await request.is_disconnected():
                    return
                try:
                    await asyncio.wait_for(changed.wait(), timeout=min(10, max(0.01, deadline - monotonic())))
                except TimeoutError:
                    yield ": heartbeat\n\n"
            yield _sse("reconnect", {"reason": "reauthorize"})

    return StreamingResponse(
        stream_async(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@conversation_router.get("/{attack_result_id}/conversations/{conversation_id}/execution/{execution_id}/events")
async def get_conversation_execution_events_async(
    attack_result_id: str,
    conversation_id: str,
    execution_id: UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> AgentEventPage:
    """
    Read chat-scoped activity without requiring execution-administration privileges.

    Returns:
        AgentEventPage: Retained activity and a cursor that includes filtered protocol frames.
    """
    await _resolve_async(attack_result_id=attack_result_id, conversation_id=conversation_id, execution_id=execution_id)
    page = await get_agent_execution_manager().store.events_async(execution_id=execution_id, after=after, limit=limit)
    return AgentEventPage(
        events=[
            event
            for event in page.events
            if event.payload.get("method") in ("session/update", "session/request_permission")
            or event.direction in ("lifecycle", "inference")
        ],
        next_cursor=page.next_cursor,
    )


@conversation_router.post("/{attack_result_id}/conversations/{conversation_id}/execution/{execution_id}/cancel")
async def cancel_conversation_execution_async(
    attack_result_id: str, conversation_id: str, execution_id: UUID
) -> ConversationExecution | None:
    """
    Cancel this conversation's turn, never an arbitrary execution or its environment.

    Returns:
        ConversationExecution | None: Updated operation state.

    Raises:
        HTTPException: Cancellation was not confirmed.
    """
    await _resolve_async(attack_result_id=attack_result_id, conversation_id=conversation_id, execution_id=execution_id)
    try:
        await get_agent_execution_manager().cancel_async(execution_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return await get_conversation_execution_async(attack_result_id, conversation_id)


@router.get("")
async def list_executions_async(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    conversation_id: str | None = None,
) -> list[AgentExecution]:
    """
    List executions owned by this backend.

    Returns:
        list[AgentExecution]: A bounded page, newest first.
    """
    manager = get_agent_execution_manager()
    await manager.start_async()
    records = sorted(manager.records.values(), key=lambda record: record.created_at, reverse=True)
    if conversation_id:
        records = [record for record in records if record.conversation_id == conversation_id]
    return [record.model_copy(deep=True) for record in records[offset : offset + limit]]


@router.get("/{execution_id}")
async def get_execution_async(execution_id: UUID) -> AgentExecution:
    """
    Get a retained execution.

    Returns:
        AgentExecution: Resource state, outcome, and configuration.

    Raises:
        HTTPException: The execution is unknown.
    """
    manager = get_agent_execution_manager()
    await manager.start_async()
    record = manager.records.get(execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return record.model_copy(deep=True)


@router.get("/{execution_id}/events")
async def get_execution_events_async(
    execution_id: UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> AgentEventPage:
    """
    Read retained events; polling never renews the execution lease.

    Returns:
        AgentEventPage: The next bounded event page.
    """
    await get_execution_async(execution_id)
    return await get_agent_execution_manager().store.events_async(execution_id=execution_id, after=after, limit=limit)


@router.post("/{execution_id}/cancel")
async def cancel_execution_turn_async(execution_id: UUID) -> AgentExecution:
    """
    Cancel active work without silently recreating or closing a settled session.

    Returns:
        AgentExecution: Current execution state.

    Raises:
        HTTPException: Cancellation cannot be confirmed.
    """
    await get_execution_async(execution_id)
    try:
        await get_agent_execution_manager().cancel_async(execution_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return await get_execution_async(execution_id)


@router.post("/{execution_id}/close")
async def close_execution_async(execution_id: UUID) -> AgentExecution:
    """
    Close resources or retry previously failed cleanup.

    Returns:
        AgentExecution: Verified closure or an explicit cleanup-failed state.
    """
    await get_execution_async(execution_id)
    await get_agent_execution_manager().close_execution_async(execution_id=execution_id)
    return await get_execution_async(execution_id)
