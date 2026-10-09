# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-backend administration of owned agent executions."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from pyrit.agent.runtime import get_agent_execution_manager
from pyrit.backend.middleware.auth import require_admin
from pyrit.models.agent_execution import (
    AgentEventPage,
    AgentExecution,
)

router = APIRouter(prefix="/executions", tags=["executions"], dependencies=[Depends(require_admin)])


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
