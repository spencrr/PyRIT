# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.backend.middleware.auth import AuthenticationError, EntraAuthMiddleware
from pyrit.backend.routes import executions
from pyrit.models.agent_execution import AgentExecution, AgentExecutionEvent, AgentExecutionState, AgentProfile


async def test_stream_replays_once_then_ends_closed_execution(tmp_path: Path) -> None:
    manager = AgentExecutionManager(root=tmp_path)
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True),
        state=AgentExecutionState.CLOSED,
        event_count=2,
    )
    await manager.store.save_async(record)
    for sequence in (1, 2):
        await manager.store.append_async(
            AgentExecutionEvent(
                execution_id=record.id,
                sequence=sequence,
                direction="incoming",
                payload={"method": "session/update"},
            )
        )

    async def receive_async() -> dict[str, object]:
        await asyncio.sleep(10)
        return {"type": "http.disconnect"}

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []}, receive=receive_async)
    with (
        patch.object(executions, "_resolve_async", AsyncMock(return_value=record)),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
    ):
        response = await executions.stream_conversation_execution_async(
            request,
            "attack",
            "one",
            after=1,
            execution_id=record.id,
        )
        frames = [frame async for frame in response.body_iterator]
    assert any('"sequence": 2' in frame for frame in frames)
    assert not any('"sequence": 1' in frame for frame in frames)
    assert any("event: end" in frame for frame in frames)
    assert not manager.store._listeners


def test_stream_uses_existing_http_authentication() -> None:
    with patch.dict(
        "os.environ",
        {
            "ENTRA_TENANT_ID": "tenant",
            "ENTRA_CLIENT_ID": "client",
            "ENTRA_ALLOWED_GROUP_IDS": "group",
        },
    ):
        app = FastAPI()
        app.include_router(executions.conversation_router, prefix="/api")
        app.add_middleware(EntraAuthMiddleware)
        with patch.object(
            EntraAuthMiddleware,
            "_authenticate_request_async",
            AsyncMock(side_effect=AuthenticationError(status_code=401, detail="Missing authentication")),
        ):
            response = TestClient(app).get(f"/api/attacks/{uuid4()}/conversations/one/execution/stream")
    assert response.status_code == 401


async def test_stream_shutdown_releases_subscriber_without_cancelling_execution(tmp_path: Path) -> None:
    manager = AgentExecutionManager(root=tmp_path)
    record = AgentExecution(
        owner_id="test",
        conversation_id="one",
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True),
        state=AgentExecutionState.WORKING,
    )
    await manager.store.save_async(record)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with (
        patch.object(executions, "_resolve_async", AsyncMock(return_value=record)),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
    ):
        response = await executions.stream_conversation_execution_async(
            request,
            "attack",
            "one",
            after=0,
            execution_id=None,
        )
        await anext(response.body_iterator)
        assert len(manager.store._listeners) == 1
        await response.body_iterator.aclose()
    assert not manager.store._listeners
    assert record.state == AgentExecutionState.WORKING
