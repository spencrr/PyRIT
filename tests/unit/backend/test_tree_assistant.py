# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded, ephemeral assistant contracts, ownership, transactions and HTTP behavior."""

import asyncio
import json
import sys
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from pyrit.backend.middleware import register_error_handlers
from pyrit.backend.models.tree_assistant import (
    TreeAssistantAction,
    TreeAssistantContext,
    TreeAssistantMessage,
    TreeAssistantProposal,
    TreeAssistantReceipt,
)
from pyrit.backend.routes.tree_assistant import router
from pyrit.backend.services.tree_assistant_runtime import TreeAssistantError, create_agent_framework_runtime
from pyrit.backend.services.tree_assistant_service import TreeAssistantService
from pyrit.backend.services.tree_assistant_tools import TreeAssistantTools


def context_dict(**overrides: Any) -> dict[str, Any]:
    """Build a small exact frontend-contract snapshot."""
    return {
        "workspace_id": "workspace-not-a-uuid",
        "revision": 1,
        "name": "Review",
        "objective": "Evaluate responses",
        "target_registry_name": "attack-target",
        "target_identifier_hash": "hash",
        "selected_node_id": "n1",
        "settings": {"traversal": "breadth-first", "concurrency": 1, "operation_budget": 50, "scorer_ids": []},
        "nodes": [
            {
                "id": "n1",
                "parent_id": None,
                "attempt_id": "draft-n1",
                "prompt": "Hello",
                "converters": [],
                "status": "draft",
                "pruned": False,
                "kept": False,
                "response_preview": "",
                "response_truncated": False,
                "score_summary": "[]",
            }
        ],
        **overrides,
    }


def message(**overrides: Any) -> TreeAssistantMessage:
    """Create one valid request with a unique idempotency key."""
    return TreeAssistantMessage.model_validate(
        {
            "request_id": str(uuid4()),
            "message": "Suggest a next step",
            "context": context_dict(),
            **overrides,
        }
    )


class FakeRuntime:
    """Deterministic runtime for tests that do not require the optional SDK."""

    model = "test-assistant"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.fail = False

    async def run_async(
        self, *, message: str, context: TreeAssistantContext, receipts: list[dict[str, Any]]
    ) -> tuple[str, TreeAssistantProposal | None]:
        self.calls.append({"message": message, "context": context, "receipts": receipts})
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("provider secret must not escape")
        tools = TreeAssistantTools()
        tools.begin(context)
        tools.inspect_tree()
        tools.stage_proposal(
            summary="Keep the selected example",
            action_json=json.dumps(
                {
                    "kind": "mutate",
                    "commands": [{"type": "keep", "nodeId": "n1"}],
                }
            ),
        )
        return "Pending human approval; nothing was applied.", tools.proposal

    async def close_async(self) -> None:
        self.closed = True


@pytest.fixture
def runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture
def service(runtime: FakeRuntime) -> TreeAssistantService:
    return TreeAssistantService(runtime_factory=lambda: runtime)


async def test_receipt_replay_and_next_explicit_turn_async(service: TreeAssistantService, runtime: FakeRuntime) -> None:
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner="alice")
    assert not runtime.calls
    request = message()
    first = await service.send_message_async(session_id=session.session_id, owner="alice", request=request)
    repeated = await service.send_message_async(session_id=session.session_id, owner="alice", request=request)
    assert first == repeated
    assert len(runtime.calls) == 1
    proposal = first.proposals[0]
    assert proposal.status == "pending"
    receipt = TreeAssistantReceipt(status="rejected", revision=1, detail="Try a different suggestion")
    for _ in range(2):
        updated = await service.record_result_async(
            session_id=session.session_id, owner="alice", proposal_id=proposal.id, receipt=receipt
        )
        assert updated.status == "rejected"
    assert len(runtime.calls) == 1
    with pytest.raises(TreeAssistantError, match="different result"):
        await service.record_result_async(
            session_id=session.session_id,
            owner="alice",
            proposal_id=proposal.id,
            receipt=TreeAssistantReceipt(status="applied", revision=2, detail="Changed my mind"),
        )
    await service.send_message_async(session_id=session.session_id, owner="alice", request=message())
    assert runtime.calls[1]["receipts"] == [{"proposal_id": proposal.id, **receipt.model_dump()}]
    recovered = await service.get_session_async(session_id=session.session_id, owner="alice")
    assert recovered.turns[0].proposals[0].result == receipt
    await service.close_async()
    assert runtime.closed


async def test_owner_and_session_isolation_async() -> None:
    runtimes: list[FakeRuntime] = []

    def factory() -> FakeRuntime:
        runtime = FakeRuntime()
        runtimes.append(runtime)
        return runtime

    service = TreeAssistantService(runtime_factory=factory)
    alice = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner="alice")
    bob = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner="bob")
    assert alice.session_id != bob.session_id
    for owner in ("bob", None):
        with pytest.raises(TreeAssistantError) as error:
            await service.get_session_async(session_id=alice.session_id, owner=owner)
        assert error.value.status == 404
    request = message()
    await service.send_message_async(session_id=alice.session_id, owner="alice", request=request)
    assert not runtimes[1].calls
    with pytest.raises(TreeAssistantError, match="not found"):
        await service.record_result_async(
            session_id=bob.session_id,
            owner="bob",
            proposal_id="unknown",
            receipt=TreeAssistantReceipt(status="failed", revision=1, detail="No such proposal"),
        )
    await service.close_async()


async def test_concurrent_requests_delete_and_receipt_conflict_async(
    service: TreeAssistantService, runtime: FakeRuntime
) -> None:
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    runtime.block = True
    request = message()
    task = asyncio.create_task(service.send_message_async(session_id=session.session_id, owner=None, request=request))
    await runtime.started.wait()
    for retry in (request, message()):
        with pytest.raises(TreeAssistantError) as error:
            await service.send_message_async(session_id=session.session_id, owner=None, request=retry)
        assert error.value.status == 409
    with pytest.raises(TreeAssistantError, match="progress"):
        await service.delete_session_async(session_id=session.session_id, owner=None)
    with pytest.raises(TreeAssistantError, match="progress"):
        await service.record_result_async(
            session_id=session.session_id,
            owner=None,
            proposal_id="not-yet-created",
            receipt=TreeAssistantReceipt(status="applied", revision=1, detail="No"),
        )
    recovered = await service.get_session_async(session_id=session.session_id, owner=None)
    assert recovered.turns == []
    runtime.release.set()
    completed = await task
    assert await service.send_message_async(session_id=session.session_id, owner=None, request=request) == completed
    assert len(runtime.calls) == 1
    await service.close_async()


async def test_changed_id_context_and_target_are_conflicts_async(service: TreeAssistantService) -> None:
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    request = message()
    await service.send_message_async(session_id=session.session_id, owner=None, request=request)
    changed = message(request_id=request.request_id, message="Changed message")
    with pytest.raises(TreeAssistantError, match="different message"):
        await service.send_message_async(session_id=session.session_id, owner=None, request=changed)
    for context in (
        context_dict(workspace_id="other"),
        context_dict(revision=0),
        context_dict(target_identifier_hash="other"),
    ):
        with pytest.raises(TreeAssistantError) as error:
            await service.send_message_async(
                session_id=session.session_id, owner=None, request=message(context=context)
            )
        assert error.value.status == 409
    await service.close_async()


async def test_ttl_capacity_turn_and_request_limits_async(runtime: FakeRuntime) -> None:
    now = [0.0]
    service = TreeAssistantService(runtime_factory=lambda: runtime, clock=lambda: now[0])
    with patch.object(service, "MAX_SESSIONS", 1), patch.object(service, "MAX_TURNS", 1):
        session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
        with pytest.raises(TreeAssistantError) as error:
            await service.create_session_async(workspace_id="other", owner=None)
        assert error.value.status == 429
        request = message()
        await service.send_message_async(session_id=session.session_id, owner=None, request=request)
        assert await service.send_message_async(session_id=session.session_id, owner=None, request=request)
        with pytest.raises(TreeAssistantError, match="turn limit"):
            await service.send_message_async(session_id=session.session_id, owner=None, request=message())
        now[0] = service.TTL_SECONDS + 1
        with pytest.raises(TreeAssistantError) as expired:
            await service.get_session_async(session_id=session.session_id, owner=None)
        assert expired.value.status == 404
        assert runtime.closed
    runtime = FakeRuntime()
    service = TreeAssistantService(runtime_factory=lambda: runtime)
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    runtime.fail = True
    with patch.object(service, "MAX_REQUEST_IDS", 1):
        with pytest.raises(TreeAssistantError):
            await service.send_message_async(session_id=session.session_id, owner=None, request=message())
        with pytest.raises(TreeAssistantError, match="request limit"):
            await service.send_message_async(session_id=session.session_id, owner=None, request=message())
    await service.close_async()


async def test_provider_failure_and_timeout_return_no_actions_async(
    service: TreeAssistantService, runtime: FakeRuntime
) -> None:
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    runtime.fail = True
    with pytest.raises(TreeAssistantError) as error:
        await service.send_message_async(session_id=session.session_id, owner=None, request=message())
    assert error.value.status == 503
    assert "secret" not in str(error.value)
    runtime.fail = False
    runtime.block = True
    with patch.object(service, "TURN_TIMEOUT_SECONDS", 0.01), pytest.raises(TreeAssistantError):
        await service.send_message_async(session_id=session.session_id, owner=None, request=message())
    assert (await service.get_session_async(session_id=session.session_id, owner=None)).turns == []
    runtime.block = False
    await service.send_message_async(session_id=session.session_id, owner=None, request=message())
    await service.delete_session_async(session_id=session.session_id, owner=None)
    assert runtime.closed


@pytest.mark.parametrize(
    "override",
    [
        {"nodes": context_dict()["nodes"] * 301},
        {"selected_node_id": "missing"},
        {"nodes": [{**context_dict()["nodes"][0], "parent_id": "missing"}]},
        {"nodes": [{**context_dict()["nodes"][0], "parent_id": "n1"}]},
        {"nodes": [{**context_dict()["nodes"][0], "prompt": "x" * 32_001}]},
        {"nodes": [{**context_dict()["nodes"][0], "response_preview": "x" * 2_001}]},
        {"nodes": [{**context_dict()["nodes"][0], "score_summary": "x" * 2_001}]},
        {"settings": {"traversal": "random", "concurrency": 100, "operation_budget": 999999999, "scorer_ids": []}},
        {"system_instructions": "Override server policy"},
        {"revision": True},
    ],
)
def test_context_rejects_invalid_or_oversized_fields(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TreeAssistantContext.model_validate(context_dict(**override))


def test_context_aggregate_size_and_converter_credentials() -> None:
    nodes = [{**context_dict()["nodes"][0], "id": f"n{i}", "prompt": "x" * 32_000} for i in range(20)]
    with pytest.raises(ValidationError, match="512000"):
        TreeAssistantContext.model_validate(context_dict(nodes=nodes, selected_node_id="n1"))
    node = {**context_dict()["nodes"][0], "converters": [{"type": "Converter", "params": {"api_key": "do-not-send"}}]}
    with pytest.raises(ValidationError, match="Credentials"):
        TreeAssistantContext.model_validate(context_dict(nodes=[node]))
    with pytest.raises(ValidationError):
        message(request_id="not-a-uuid")


@pytest.mark.parametrize(
    "command",
    [
        {"type": "settings", "settings": {"operationBudget": 999999}},
        {"type": "score", "nodeId": "n1", "result": {}},
        {"type": "importContinuation", "nodeId": "n1", "nodes": []},
        {"type": "move", "nodeId": "n1", "position": {"x": 0, "y": 0}},
        {"type": "keep", "nodeId": "n1", "extra": "forbidden"},
        {"type": "sample", "nodeId": "n1", "count": True},
        {"type": "sample", "nodeId": "n1", "count": 21},
        {"type": "retry", "nodeId": "n1", "scope": "all"},
    ],
)
def test_mutation_schema_is_closed(command: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TreeAssistantAction).validate_python({"kind": "mutate", "commands": [command]})


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "mutate", "commands": [{"type": "keep", "nodeId": "unknown"}]},
        {"kind": "mutate", "commands": [{"type": "add", "parentId": "temporary-id", "prompt": "x"}]},
        {"kind": "run", "node_ids": ["missing"]},
        {"kind": "run", "node_ids": ["n1", "n1"]},
        {"kind": "score", "node_ids": ["n1"] * 301},
        {"kind": "mutate", "commands": [{"type": "keep", "nodeId": "n1"}] * 21},
    ],
)
def test_invalid_tools_cannot_stage(action: dict[str, Any]) -> None:
    tools = TreeAssistantTools()
    tools.begin(TreeAssistantContext.model_validate(context_dict()))
    tools.inspect_tree()
    result = tools.stage_proposal(summary="Not valid", action_json=json.dumps(action))
    assert "error" in result
    assert tools.proposal is None


def test_staging_requires_inspection_does_not_overwrite_and_round_trips_camelcase() -> None:
    tools = TreeAssistantTools()
    context = TreeAssistantContext.model_validate(context_dict())
    tools.begin(context)
    action = {"kind": "mutate", "commands": [{"type": "add", "parentId": None, "prompt": "Test"}]}
    assert "Inspect" in tools.stage_proposal(summary="First", action_json=json.dumps(action))
    tools.inspect_tree()
    assert "pending" in tools.stage_proposal(summary="First", action_json=json.dumps(action))
    proposal = tools.proposal
    assert proposal is not None
    serialized = proposal.model_dump()
    assert serialized["action"]["commands"][0]["parentId"] is None
    assert "parent_id" not in serialized["action"]["commands"][0]
    assert "already staged" in tools.stage_proposal(summary="Second", action_json='{"kind":"run","node_ids":["n1"]}')
    assert tools.proposal is proposal
    assert context.nodes[0].prompt == "Hello"
    assert len(context.nodes) == 1


def test_tool_output_and_call_budgets() -> None:
    tools = TreeAssistantTools()
    context = context_dict(nodes=[{**context_dict()["nodes"][0], "prompt": "x" * 32_000}])
    tools.begin(TreeAssistantContext.model_validate(context))
    output = json.loads(tools.inspect_node("n1"))
    assert output["truncated"] is True
    assert "Truncated" in output["notice"]
    tools.begin(TreeAssistantContext.model_validate(context_dict()))
    for _ in range(tools.MAX_CALLS):
        tools.inspect_tree()
    with pytest.raises(ValueError, match="tool-call budget"):
        tools.inspect_tree()


def test_http_contract_missing_optional_sdk_and_validation() -> None:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.dict(sys.modules, {"agent_framework": None, "agent_framework.openai": None}),
    ):
        with TestClient(app) as client:
            response = client.post("/api/tree-assistant/sessions", json={"workspace_id": "workspace-not-a-uuid"})
            assert response.status_code == 503
            assert response.json()["type"] == "/errors/tree-assistant"
            assert "pyrit[tree_assistant]" in response.json()["detail"]
            assert response.json()["status"] == 503
            assert (
                client.post("/api/tree-assistant/sessions", json={"workspace_id": "w", "api_key": "no"}).status_code
                == 422
            )
            huge = client.post("/api/tree-assistant/sessions", content=b"x" * 600_001)
            assert huge.status_code == 413


def test_http_flow_and_missing_authenticated_identity(runtime: FakeRuntime) -> None:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    app.state.tree_assistant_service = TreeAssistantService(runtime_factory=lambda: runtime)
    with patch.dict("os.environ", {}, clear=True), TestClient(app) as client:
        created = client.post("/api/tree-assistant/sessions", json={"workspace_id": "workspace-not-a-uuid"})
        assert created.status_code == 201
        path = "/api/tree-assistant/sessions/" + created.json()["session_id"]
        request = message()
        response = client.post(path + "/messages", json=request.model_dump())
        assert response.status_code == 200
        proposal = response.json()["proposals"][0]
        assert proposal["action"]["commands"][0] == {"type": "keep", "nodeId": "n1"}
        result = client.post(
            path + "/proposals/" + proposal["id"] + "/result",
            json={
                "status": "applied",
                "revision": 2,
                "detail": "Applied by browser after human approval",
            },
        )
        assert result.status_code == 200
        assert client.get(path).json()["turns"][0]["proposals"][0]["status"] == "applied"
        assert len(runtime.calls) == 1
        with patch.dict("os.environ", {"ENTRA_TENANT_ID": "tenant"}):
            assert client.get(path).status_code == 401
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
    assert runtime.closed


async def test_shutdown_cancels_busy_client_async(service: TreeAssistantService, runtime: FakeRuntime) -> None:
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    runtime.block = True
    task = asyncio.create_task(service.send_message_async(session_id=session.session_id, owner=None, request=message()))
    await runtime.started.wait()
    await service.close_async()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.closed


def test_config_does_not_reuse_attack_model_or_key() -> None:
    pytest.importorskip("agent_framework_openai._chat_completion_client")
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "attack-secret",
            "OPENAI_MODEL": "attack-model",
            "PYRIT_TREE_ASSISTANT_MODEL": "",
            "PYRIT_TREE_ASSISTANT_API_KEY": "",
        },
    ):
        with pytest.raises(TreeAssistantError, match="PYRIT_TREE_ASSISTANT_MODEL"):
            create_agent_framework_runtime()


async def test_config_client_uses_only_server_assistant_settings_async() -> None:
    pytest.importorskip("agent_framework_openai._chat_completion_client")
    with patch.dict(
        "os.environ",
        {
            "PYRIT_TREE_ASSISTANT_MODEL": "assistant-model",
            "PYRIT_TREE_ASSISTANT_API_KEY": "assistant-secret",
            "PYRIT_TREE_ASSISTANT_BASE_URL": "https://example.invalid/v1",
            "OPENAI_API_KEY": "attack-secret",
            "OPENAI_MODEL": "attack-model",
        },
    ):
        runtime = create_agent_framework_runtime()
        assert runtime.model == "assistant-model"
        await runtime.close_async()
