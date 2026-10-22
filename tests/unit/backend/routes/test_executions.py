# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.routes import executions
from pyrit.models.agent_execution import AgentEventPage, AgentExecution, AgentProfile


def test_execution_admin_access_is_required() -> None:
    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.router)
    assert TestClient(app).get("/executions").status_code == 403


@pytest.mark.usefixtures("patch_central_database")
def test_execution_routes_return_real_records_and_events() -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager

    profile = AgentProfile(environment="local", local_execution_acknowledged=True)
    record = AgentExecution(owner_id="test", conversation_id="conversation", target_id="target", profile=profile)
    manager = MagicMock(spec=AgentExecutionManager)
    manager.records = {record.id: record}
    manager.latest_execution.return_value = record
    manager.start_async = AsyncMock()
    manager.cancel_async = AsyncMock()
    manager.close_execution_async = AsyncMock()
    from pyrit.agent.execution_store import AgentExecutionStore

    manager.store = MagicMock(spec=AgentExecutionStore)
    manager.store.events_async = AsyncMock(return_value=AgentEventPage(events=[], next_cursor=0))
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[require_admin] = lambda: None
    client = TestClient(app)
    with patch.object(executions, "get_agent_execution_manager", return_value=manager):
        listed = client.get("/executions").json()[0]
        assert listed["profile"]["environment"] == "local"
        assert listed["configuration"]["environment_template"]["environment"] == "local"
        assert "configuration" not in record.model_dump()
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
    from pyrit.agent.execution_store import AgentExecutionStore
    from pyrit.backend.services import agent_execution_service
    from pyrit.memory import CentralMemory
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
    manager.latest_execution.return_value = record
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
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.router)
    app.include_router(executions.conversation_router)
    client = TestClient(app)
    path = f"/attacks/{attack.attack_result_id}/conversations/{attack.conversation_id}/execution"
    with (
        patch.object(agent_execution_service, "get_agent_execution_manager", return_value=manager),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
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


@pytest.mark.usefixtures("patch_central_database")
def test_conversation_routes_never_resolve_another_conversations_execution() -> None:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.agent.execution_store import AgentExecutionStore
    from pyrit.backend.services import agent_execution_service
    from pyrit.memory import CentralMemory
    from pyrit.models import AttackResult

    memory = CentralMemory.get_memory_instance()
    mine = AttackResult(conversation_id=str(uuid4()), objective="mine")
    other = AttackResult(conversation_id=str(uuid4()), objective="other")
    asyncio.run(memory.add_attack_results_to_memory_async(attack_results=[mine, other]))
    profile = AgentProfile(environment="local", local_execution_acknowledged=True)
    records = {
        attack.conversation_id: AgentExecution(
            owner_id="owner", conversation_id=attack.conversation_id, target_id="target", profile=profile
        )
        for attack in (mine, other)
    }
    manager = MagicMock(spec=AgentExecutionManager)
    manager.records = {record.id: record for record in records.values()}
    manager.latest_execution.side_effect = lambda *, conversation_id, target_id=None: records.get(conversation_id)
    manager.start_async = AsyncMock()
    manager.cancel_async = AsyncMock()
    manager.store = MagicMock(spec=AgentExecutionStore)
    manager.store.events_async = AsyncMock(return_value=AgentEventPage(events=[], next_cursor=0))
    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.conversation_router)
    client = TestClient(app)
    path = f"/attacks/{mine.attack_result_id}/conversations/{mine.conversation_id}/execution"
    foreign = records[other.conversation_id].id
    with (
        patch.object(agent_execution_service, "get_agent_execution_manager", return_value=manager),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
    ):
        assert client.get(path).json()["id"] == str(records[mine.conversation_id].id)
        assert client.get(f"{path}/{foreign}/events").status_code == 404
        assert client.post(f"{path}/{foreign}/cancel").status_code == 404
        # The other attack's conversation is not reachable through this attack either.
        crossed = f"/attacks/{mine.attack_result_id}/conversations/{other.conversation_id}/execution"
        assert client.get(crossed).status_code == 404
        assert client.get(f"{crossed}/{foreign}/events").status_code == 404
    manager.cancel_async.assert_not_awaited()
    manager.store.events_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
def test_permission_and_hold_routes_require_an_interactive_execution() -> None:
    from datetime import UTC, datetime, timedelta

    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.backend.services import agent_execution_service
    from pyrit.memory import CentralMemory
    from pyrit.models import AttackResult
    from pyrit.models.agent_execution import AgentApproval, AgentExecutionState

    attack = AttackResult(conversation_id=str(uuid4()), objective="receipt")
    asyncio.run(CentralMemory.get_memory_instance().add_attack_results_to_memory_async(attack_results=[attack]))
    approval = AgentApproval(
        tool_call_id="call",
        title="Read orders",
        options=[{"option_id": "yes", "name": "Allow", "kind": "allow_once"}],
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    record = AgentExecution(
        owner_id="owner",
        conversation_id=attack.conversation_id,
        target_id="target",
        profile=AgentProfile(environment="local", local_execution_acknowledged=True, permission_policy="ask"),
        state=AgentExecutionState.HELD,
        interactive=True,
        approvals=[approval],
    )
    manager = MagicMock(spec=AgentExecutionManager)
    manager.records = {record.id: record}
    manager.latest_execution.return_value = record
    manager.start_async = AsyncMock()
    manager.decide_permission_async = AsyncMock()
    manager.control_held_async = AsyncMock(side_effect=[None, ValueError("Hold expired; start a fresh execution")])
    manager.close_execution_async = AsyncMock()
    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.conversation_router)
    client = TestClient(app)
    path = f"/attacks/{attack.attack_result_id}/conversations/{attack.conversation_id}/execution/{record.id}"
    with (
        patch.object(agent_execution_service, "get_agent_execution_manager", return_value=manager),
        patch.object(executions, "get_agent_execution_manager", return_value=manager),
    ):
        decided = client.post(f"{path}/permissions/{approval.id}", json={"allow": True})
        assert decided.status_code == 200
        assert decided.json()["approvals"][0]["tool_call_id"] == "call"
        assert "owner_id" not in decided.json()
        manager.decide_permission_async.assert_awaited_once_with(
            execution_id=record.id, approval_id=approval.id, allow=True, actor="local-operator"
        )
        # Only strict booleans decide; nothing is coerced into an approval.
        assert client.post(f"{path}/permissions/{approval.id}", json={"allow": "yes"}).status_code == 422
        assert client.post(f"{path}/control/extend").status_code == 200
        assert client.post(f"{path}/control/extend").status_code == 409
        assert client.post(f"{path}/control/close").status_code == 200
        manager.close_execution_async.assert_awaited_once_with(execution_id=record.id, reason="operator:local-operator")
        assert client.post(f"{path}/control/resume").status_code == 422
        manager.decide_permission_async.side_effect = ValueError("Execution cannot accept permission decisions")
        assert client.post(f"{path}/permissions/{approval.id}", json={"allow": False}).status_code == 409
        record.interactive = False
        assert client.post(f"{path}/control/continue").status_code == 409
    manager.control_held_async.assert_awaited_with(execution_id=record.id, action="extend", actor="local-operator")
