# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.routes import executions
from pyrit.models.agent_execution import AgentEventPage, AgentExecution, AgentProfile


def test_execution_admin_access_is_required() -> None:
    app = FastAPI()
    app.include_router(executions.router)
    with patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "false"}):
        assert TestClient(app).get("/executions").status_code == 403


@pytest.mark.usefixtures("patch_central_database")
def test_execution_routes_return_real_records_and_events() -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager

    profile = AgentProfile(environment="local", local_execution_acknowledged=True)
    record = AgentExecution(owner_id="test", conversation_id="conversation", target_id="target", profile=profile)
    manager = MagicMock(spec=AgentExecutionManager)
    manager.records = {record.id: record}
    manager.start_async = AsyncMock()
    manager.cancel_async = AsyncMock()
    manager.close_execution_async = AsyncMock()
    from pyrit.memory.agent_execution_store import AgentExecutionStore

    manager.store = MagicMock(spec=AgentExecutionStore)
    manager.store.events_async = AsyncMock(return_value=AgentEventPage(events=[], next_cursor=0))
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[require_admin] = lambda: None
    client = TestClient(app)
    with patch.object(executions, "get_agent_execution_manager", return_value=manager):
        assert client.get("/executions").json()[0]["profile"]["environment"] == "local"
        assert client.get(f"/executions/{record.id}/events").json() == {"events": [], "next_cursor": 0}
        assert client.post(f"/executions/{record.id}/cancel").status_code == 200
        assert client.post(f"/executions/{record.id}/close").status_code == 200
        assert client.get(f"/executions/{uuid4()}").status_code == 404
        assert client.get(f"/executions/{record.id}/events?limit=501").status_code == 422
    manager.cancel_async.assert_awaited_once()
    manager.close_execution_async.assert_awaited_once()
