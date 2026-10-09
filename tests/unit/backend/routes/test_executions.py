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


@pytest.mark.usefixtures("patch_central_database")
def test_conversation_access_is_scoped_without_admin_privileges() -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.backend.services import agent_execution_service
    from pyrit.memory import CentralMemory
    from pyrit.memory.agent_execution_store import AgentExecutionStore
    from pyrit.models import AttackResult
    from pyrit.models.agent_execution import AgentExecutionEvent

    attack = AttackResult(conversation_id=str(uuid4()), objective="receipt")
    asyncio.run(CentralMemory.get_memory_instance().add_attack_results_to_memory_async(attack_results=[attack]))
    record = AgentExecution(
        owner_id="private-owner",
        conversation_id=attack.conversation_id,
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True, credential_env=("TOKEN_NAME",)),
    )
    manager = MagicMock(spec=AgentExecutionManager)
    manager.records = {record.id: record}
    manager.start_async = AsyncMock()
    manager.cancel_async = AsyncMock()
    manager.store = MagicMock(spec=AgentExecutionStore)
    manager.store.events_async = AsyncMock(
        return_value=AgentEventPage(
            events=[
                AgentExecutionEvent(
                    execution_id=record.id, sequence=1, direction="incoming", payload={"method": "initialize"}
                ),
                AgentExecutionEvent(
                    execution_id=record.id, sequence=2, direction="incoming", payload={"method": "session/update"}
                ),
            ],
            next_cursor=2,
        )
    )
    app = FastAPI()
    app.include_router(executions.router)
    app.include_router(executions.conversation_router)
    client = TestClient(app)
    path = f"/attacks/{attack.attack_result_id}/conversations/{attack.conversation_id}/execution"
    with (
        patch.object(agent_execution_service, "get_agent_execution_manager", return_value=manager),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
        patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "false"}),
    ):
        assert client.get("/executions").status_code == 403
        response = client.get(path)
        assert response.status_code == 200
        assert "profile" not in response.json()
        assert "owner_id" not in response.json()
        assert "TOKEN_NAME" not in response.text
        page = client.get(f"{path}/{record.id}/events").json()
        assert [event["sequence"] for event in page["events"]] == [2]
        assert page["next_cursor"] == 2
        assert client.post(f"{path}/{record.id}/cancel").status_code == 200
        assert client.post(f"{path}/{uuid4()}/cancel").status_code == 404
        assert client.get(f"/attacks/{attack.attack_result_id}/conversations/{uuid4()}/execution").status_code == 404
    manager.cancel_async.assert_awaited_once()
