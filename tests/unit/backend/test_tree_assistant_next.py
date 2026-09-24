# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Portable history, bounded diagnostics and scoped multilevel planning contracts."""

import json
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from pyrit.backend.middleware import register_error_handlers
from pyrit.backend.models.tree_assistant import (
    CreateTreeAssistantSession,
    TreeAssistantAction,
    TreeAssistantContext,
    TreeAssistantReceipt,
    TreeAssistantToolCall,
    TreeAssistantTurn,
)
from pyrit.backend.routes.tree_assistant import router
from pyrit.backend.services.tree_assistant_runtime import AgentFrameworkRuntime, TreeAssistantError
from pyrit.backend.services.tree_assistant_service import TreeAssistantService
from pyrit.backend.services.tree_assistant_tools import TreeAssistantTools

from .test_tree_assistant import FakeRuntime, context_dict, message


def plan(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "plan",
        "run": True,
        "steps": [
            {"id": "first", "parent": {"node_id": "n1"}, "prompt": "First turn", "converters": []},
            {"id": "second", "parent": {"step_id": "first"}, "prompt": "Follow-up", "converters": []},
            {"id": "third", "parent": {"step_id": "first"}, "prompt": "Alternate follow-up", "converters": []},
        ],
        **overrides,
    }


def scoped_context(**overrides: Any) -> TreeAssistantContext:
    template = context_dict()["nodes"][0]
    nodes = [
        {**template, "id": "outside", "status": "completed"},
        {**template, "id": "n1", "parent_id": "outside"},
        {**template, "id": "child", "parent_id": "n1"},
        {**template, "id": "sibling", "parent_id": "outside"},
    ]
    return TreeAssistantContext.model_validate(
        context_dict(
            **{
                "nodes": nodes,
                "autonomy": {
                    "root_node_id": "n1",
                    "remaining_operations": 5,
                    "remaining_turns": 3,
                    "goal": "Explore this subtree",
                },
                **overrides,
            }
        )
    )


def stage(*, action: dict[str, Any], context: TreeAssistantContext | None = None) -> TreeAssistantTools:
    tools = TreeAssistantTools()
    tools.begin(context or TreeAssistantContext.model_validate(context_dict()))
    tools.inspect_tree()
    tools.stage_proposal(summary="Review this next step", action_json=json.dumps(action))
    return tools


@pytest.mark.parametrize("run", [True, False])
def test_plan_stages_multilevel_children_without_mutating(run: bool) -> None:
    context = scoped_context()
    if run:
        context.nodes[1].status = "completed"
    before = context.model_dump()
    tools = stage(action=plan(run=run), context=context)
    assert tools.proposal is not None
    assert tools.proposal.action.model_dump() == plan(run=run)
    assert context.model_dump() == before
    assert tools.proposal.status == "pending"
    assert "already staged" in tools.stage_proposal(summary="Again", action_json=json.dumps(plan()))


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [plan()["steps"][0]] * 21,
        [plan()["steps"][0]] * 2,
        [{**plan()["steps"][0], "parent": {"step_id": "first"}}],
        [{**plan()["steps"][0], "parent": {"step_id": "second"}}, plan()["steps"][1]],
        [{**plan()["steps"][0], "parent": {"node_id": "n1", "step_id": "other"}}],
        [{**plan()["steps"][0], "prompt": " \t"}],
        [{**plan()["steps"][0], "parent": {"step_id": "missing"}}],
    ],
)
def test_plan_rejects_empty_duplicate_forward_cyclic_and_malformed_steps(steps: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TreeAssistantAction).validate_python(plan(steps=steps))


@pytest.mark.parametrize(
    "step",
    [
        {**plan()["steps"][0], "id": "n1"},
        {**plan()["steps"][0], "parent": {"node_id": "missing"}},
        {**plan()["steps"][0], "parent": {"node_id": "first"}},
    ],
)
def test_plan_rejects_existing_id_collisions_and_unknown_parents(step: dict[str, Any]) -> None:
    assert stage(action=plan(steps=[step])).proposal is None


def test_plan_respects_total_node_limit_and_draft_only_operation_cost() -> None:
    nodes = [{**context_dict()["nodes"][0], "id": f"n{index}"} for index in range(299)]
    assert (
        stage(action=plan(run=False), context=TreeAssistantContext.model_validate(context_dict(nodes=nodes))).proposal
        is None
    )
    context = scoped_context()
    context.nodes[1].status = "completed"
    context.autonomy.remaining_operations = 0
    assert stage(action=plan(run=False), context=context).proposal is not None
    assert stage(action=plan(run=True), context=context).proposal is None
    context.autonomy.remaining_turns = 0
    assert stage(action=plan(run=False), context=context).proposal is None


@pytest.mark.parametrize(
    "action",
    [
        plan(steps=[{**plan()["steps"][0], "parent": None}]),
        plan(steps=[{**plan()["steps"][0], "parent": {"node_id": "outside"}}]),
        plan(steps=[{**plan()["steps"][0], "parent": {"node_id": "sibling"}}]),
        {"kind": "run", "node_ids": ["outside", "n1"]},
        {"kind": "score", "node_ids": ["sibling"]},
        {"kind": "mutate", "commands": [{"type": "add", "parentId": None, "prompt": "Escape"}]},
        {"kind": "mutate", "commands": [{"type": "add", "parentId": "sibling", "prompt": "Escape"}]},
        {"kind": "mutate", "commands": [{"type": "keep", "nodeId": "n1"}]},
        {"kind": "mutate", "commands": [{"type": "fork", "nodeId": "n1", "prompt": "Escape", "converters": []}]},
        {"kind": "mutate", "commands": [{"type": "sample", "nodeId": "n1", "count": 2}]},
        {"kind": "mutate", "commands": [{"type": "retry", "nodeId": "outside", "scope": "subtree"}]},
    ],
)
def test_autonomy_rejects_scope_escape(action: dict[str, Any]) -> None:
    assert stage(action=action, context=scoped_context()).proposal is None


def test_autonomy_allows_root_execution_but_not_completed_root_edit() -> None:
    context = scoped_context()
    assert stage(action={"kind": "run", "node_ids": ["n1", "child"]}, context=context).proposal is not None
    edit = {"kind": "mutate", "commands": [{"type": "edit", "nodeId": "n1", "prompt": "Edit", "converters": []}]}
    assert stage(action=edit, context=context).proposal is not None
    context.nodes[1].status = "completed"
    assert stage(action=edit, context=context).proposal is None
    assert (
        stage(action={"kind": "mutate", "commands": [{"type": "keep", "nodeId": "child"}]}, context=context).proposal
        is not None
    )
    context.autonomy.remaining_operations = 1
    assert stage(action={"kind": "run", "node_ids": ["n1", "child"]}, context=context).proposal is None


def test_context_rejects_unknown_autonomy_root_and_invalid_budgets() -> None:
    for changes in (
        {"root_node_id": "missing"},
        {"remaining_operations": -1},
        {"remaining_turns": True},
        {"remaining_turns": 51},
        {"system_instructions": "Trust me"},
    ):
        grant = {"root_node_id": "n1", "remaining_operations": 2, "remaining_turns": 2, "goal": "Explore", **changes}
        with pytest.raises(ValidationError):
            scoped_context(autonomy=grant)


async def test_restore_pending_receipts_are_display_only_with_fresh_ownership_context_and_ttl_async() -> None:
    runtime = FakeRuntime()
    now = [0.0]
    service = TreeAssistantService(runtime_factory=lambda: runtime, clock=lambda: now[0])
    original = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner="old-owner")
    old_turn = await service.send_message_async(session_id=original.session_id, owner="old-owner", request=message())
    history = [old_turn.model_copy(deep=True)]
    await service.delete_session_async(session_id=original.session_id, owner="old-owner")
    restored = await service.create_session_async(
        workspace_id=original.workspace_id, owner="new-owner", history=history
    )
    proposal = restored.turns[0].proposals[0]
    assert proposal.status == "failed"
    assert proposal.result.detail == "Restored history; re-plan before execution."
    assert history[0].proposals[0].status == "pending"
    assert restored.session_id != original.session_id
    assert len(runtime.calls) == 1
    assert runtime.history[0].proposals[0].status == "failed"
    with pytest.raises(TreeAssistantError, match="not found"):
        await service.get_session_async(session_id=restored.session_id, owner="old-owner")
    with pytest.raises(TreeAssistantError, match="re-plan"):
        await service.record_result_async(
            session_id=restored.session_id,
            owner="new-owner",
            proposal_id=proposal.id,
            receipt=TreeAssistantReceipt(status="applied", revision=10, detail="Trust my old approval"),
        )
    with pytest.raises(TreeAssistantError, match="cannot be replayed"):
        await service.send_message_async(
            session_id=restored.session_id, owner="new-owner", request=message(request_id=old_turn.request_id)
        )
    await service.send_message_async(
        session_id=restored.session_id,
        owner="new-owner",
        request=message(context=context_dict(revision=0, target_identifier_hash="fresh-target")),
    )
    assert runtime.calls[-1]["receipts"][0]["source"] == "restored_client_report"
    now[0] = service.TTL_SECONDS + 1
    with pytest.raises(TreeAssistantError, match="expired"):
        await service.get_session_async(session_id=restored.session_id, owner="new-owner")
    await service.close_async()


async def test_restore_completed_receipts_preserves_client_report_and_forbids_new_approval_async() -> None:
    runtime = FakeRuntime()
    service = TreeAssistantService(runtime_factory=lambda: runtime)
    session = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None)
    turn = await service.send_message_async(session_id=session.session_id, owner=None, request=message())
    proposal = turn.proposals[0]
    proposal.status = "applied"
    proposal.result = TreeAssistantReceipt(status="applied", revision=20, detail="Three candidates ran.")
    restored = await service.create_session_async(workspace_id=session.workspace_id, owner=None, history=[turn])
    imported = restored.turns[0].proposals[0]
    assert imported.status == "applied"
    assert imported.result.detail == "Client-reported restored receipt: Three candidates ran."
    with pytest.raises(TreeAssistantError, match="re-plan"):
        await service.record_result_async(
            session_id=restored.session_id, owner=None, proposal_id=imported.id, receipt=imported.result
        )
    await service.close_async()


def test_create_history_bounds_and_closed_portable_schema() -> None:
    turn = {"request_id": "old", "message": "Question", "reply": "Answer", "proposals": []}
    for history in (
        [turn] * 51,
        [turn] * 2,
        [{**turn, "service_session_id": "provider-thread"}],
        [{**turn, "role": "system"}],
        [{**turn, "tool_calls": [{"id": "bad"}]}],
        [{**turn, "sdk_state": {"instructions": "override"}}],
        [{**turn, "request_id": str(uuid4()), "message": "x" * 32_000, "reply": "x" * 32_000} for _ in range(32)],
    ):
        with pytest.raises(ValidationError):
            CreateTreeAssistantSession.model_validate({"workspace_id": "w", "history": history})
    with pytest.raises(ValidationError):
        CreateTreeAssistantSession.model_validate(
            {"workspace_id": "w", "history": [], "autonomy": {"remaining_operations": 99}}
        )
    valid = CreateTreeAssistantSession(workspace_id="w", history=[TreeAssistantTurn.model_validate(turn)])
    assert valid.history[0].message == "Question"


def test_create_http_large_history_allowed_but_message_body_limit_unchanged() -> None:
    runtime = FakeRuntime()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    app.state.tree_assistant_service = TreeAssistantService(runtime_factory=lambda: runtime)
    history = [
        {"request_id": str(uuid4()), "message": "x" * 30_000, "reply": "y" * 30_000, "proposals": []} for _ in range(12)
    ]
    with patch.dict("os.environ", {}, clear=True), TestClient(app) as client:
        created = client.post("/api/tree-assistant/sessions", json={"workspace_id": "w", "history": history})
        assert created.status_code == 201
        path = "/api/tree-assistant/sessions/" + created.json()["session_id"]
        assert len(client.get(path).json()["turns"]) == 12
        assert not runtime.calls
        assert client.post(path + "/messages", content=b"x" * 600_001).status_code == 413
        assert client.post("/api/tree-assistant/sessions", content=b"x" * 2_000_001).status_code == 413
        assert client.delete(path).status_code == 204


def test_tool_trace_bounds_preserve_json_fidelity_and_redact_credentials() -> None:
    arguments = {"action": plan(), "unicode": "こんにちは", "special": '"\\\n'}
    trace = {
        "id": "call",
        "name": "propose_action",
        "arguments": arguments,
        "result": "Okay",
        "status": "completed",
        "duration_ms": 1.5,
    }
    result = AgentFrameworkRuntime._bound_trace(trace=trace, limit=127_900)
    assert result.arguments == arguments
    assert not result.truncated
    large = {
        **trace,
        "arguments": {"api_key": "never-display", "data": arguments, "long": '"' * 50_000},
        "result": "😀\n" * 30_000,
    }
    result = AgentFrameworkRuntime._bound_trace(trace=large, limit=127_900)
    assert result.truncated
    assert "never-display" not in result.model_dump_json()
    assert len(json.dumps(result.model_dump(), ensure_ascii=False).encode()) <= 7_900
    turn = TreeAssistantTurn(request_id="id", message="message", reply="reply", proposals=[], tool_calls=[result] * 16)
    assert len(json.dumps([call.model_dump() for call in turn.tool_calls], ensure_ascii=False).encode()) <= 128_000
    with pytest.raises(ValidationError):
        TreeAssistantTurn(request_id="id", message="message", reply="reply", proposals=[], tool_calls=[result] * 17)
    with pytest.raises(ValidationError):
        TreeAssistantToolCall.model_validate({**trace, "duration_ms": float("nan"), "truncated": False})


def test_restored_trace_redacts_credentials_and_drops_provider_exception_details() -> None:
    restored = CreateTreeAssistantSession.model_validate(
        {
            "workspace_id": "workspace",
            "history": [
                {
                    "request_id": "previous",
                    "message": "Question",
                    "reply": "Answer",
                    "proposals": [],
                    "tool_calls": [
                        {
                            "id": "call",
                            "name": "propose_action",
                            "arguments": {"items": [{"client_secret": "old-secret", "api-key": "old-key", "safe": 1}]},
                            "result": "Provider exception: request headers included sensitive authentication details.",
                            "status": "error",
                            "duration_ms": 0,
                            "truncated": True,
                        }
                    ],
                }
            ],
        }
    )
    trace = restored.history[0].tool_calls[0]
    assert trace.arguments == {"items": [{"client_secret": "[redacted]", "api-key": "[redacted]", "safe": 1}]}
    assert trace.result == "Tool invocation failed."
    assert trace.truncated
    assert "old-secret" not in restored.model_dump_json()
    assert "Provider exception" not in restored.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        '{"api_key":"RESTORED_RAW_SENTINEL',
        [{"api_key": "RESTORED_RAW_SENTINEL"}],
        "RESTORED_RAW_SENTINEL",
    ],
)
def test_restored_trace_omits_sdk_raw_argument_payload(payload: Any) -> None:
    restored = CreateTreeAssistantSession.model_validate(
        {
            "workspace_id": "workspace",
            "history": [
                {
                    "request_id": "previous",
                    "message": "Question",
                    "reply": "Answer",
                    "proposals": [],
                    "tool_calls": [
                        {
                            "id": "call",
                            "name": "propose_action",
                            "arguments": {"raw": payload},
                            "result": "Argument parsing failed.",
                            "status": "error",
                            "duration_ms": 0,
                            "truncated": False,
                        }
                    ],
                }
            ],
        }
    )
    assert restored.history[0].tool_calls[0].arguments == {"raw": "[unparseable arguments omitted]"}
    assert "RESTORED_RAW_SENTINEL" not in restored.model_dump_json()


def test_trace_preserves_arbitrary_prompt_content_and_nested_raw_strings() -> None:
    literal = '{"api_key":"LITERAL_CONTENT_SENTINEL'
    arguments = {"prompt": literal, "content": literal, "example": {"raw": literal}}
    trace = TreeAssistantToolCall(
        id="call",
        name="propose_action",
        arguments=arguments,
        result="Okay",
        status="completed",
        duration_ms=1,
        truncated=False,
    )
    assert trace.arguments == arguments


def test_restored_proposals_reject_foreign_workspaces_duplicate_ids_and_inconsistent_receipts() -> None:
    proposal = stage(action=plan(run=False)).proposal
    turn = TreeAssistantTurn(request_id="first", message="Question", reply="Answer", proposals=[proposal])
    with pytest.raises(ValidationError, match="workspace"):
        CreateTreeAssistantSession(workspace_id="other", history=[turn])
    duplicate = turn.model_copy(update={"request_id": "second"})
    with pytest.raises(ValidationError, match="unique"):
        CreateTreeAssistantSession(workspace_id=proposal.workspace_id, history=[turn, duplicate])
    proposal.status = "applied"
    proposal.result = TreeAssistantReceipt(status="failed", revision=1, detail="Inconsistent")
    with pytest.raises(ValidationError, match="disagree"):
        CreateTreeAssistantSession(workspace_id=proposal.workspace_id, history=[turn])


def test_budget_counts_converters_and_score_calls_without_charging_draft_creation() -> None:
    context = scoped_context()
    context.nodes[1].status = "completed"
    context.autonomy.remaining_operations = 2
    action = plan(
        steps=[{**plan()["steps"][0], "converters": [{"type": "A", "params": {}}, {"type": "B", "params": {}}]}]
    )
    assert stage(action=action, context=context).proposal is None
    assert stage(action={**action, "run": False}, context=context).proposal is not None
    context.nodes[1].converters = TypeAdapter(TreeAssistantAction).validate_python(action).steps[0].converters
    assert stage(action={"kind": "run", "node_ids": ["n1"]}, context=context).proposal is None
    context.settings.scorer_ids = ["a", "b", "c"]
    assert stage(action={"kind": "score", "node_ids": ["n1"]}, context=context).proposal is None


@pytest.mark.parametrize(
    ("workspace_budget", "remaining_operations", "run", "accepted"),
    [(2, 5, True, False), (3, 5, True, True), (5, 2, True, False), (4, 3, True, True), (1, 0, False, True)],
)
def test_plan_checks_workspace_and_grant_budgets_before_staging(
    *, workspace_budget: int, remaining_operations: int, run: bool, accepted: bool
) -> None:
    context = scoped_context()
    context.nodes[1].status = "completed"
    context.settings.operation_budget = workspace_budget
    context.autonomy.remaining_operations = remaining_operations
    before = context.model_dump()
    tools = stage(action=plan(run=run), context=context)
    assert (tools.proposal is not None) is accepted
    assert context.model_dump() == before


@pytest.mark.parametrize("status", ["draft", "running", "error"])
def test_plan_execution_requires_completed_existing_anchors(status: str) -> None:
    context = scoped_context()
    context.nodes[1].status = status
    assert stage(action=plan(run=True), context=context).proposal is None
    assert stage(action=plan(run=False), context=context).proposal is not None
    assert stage(action={"kind": "run", "node_ids": ["n1"]}, context=context).proposal is not None
    context.nodes[1].status = "completed"
    assert stage(action=plan(run=True), context=context).proposal is not None


def test_non_draft_edit_counts_fork_against_node_limit() -> None:
    nodes = [{**context_dict()["nodes"][0], "id": f"n{index}", "status": "completed"} for index in range(300)]
    context = TreeAssistantContext.model_validate(context_dict(nodes=nodes))
    assert (
        stage(
            action={
                "kind": "mutate",
                "commands": [{"type": "edit", "nodeId": "n1", "prompt": "New", "converters": []}],
            },
            context=context,
        ).proposal
        is None
    )


def test_tree_overview_is_compact_and_keeps_fixture_fields() -> None:
    context = scoped_context()
    context.objective = "objective " * 2_000
    context.autonomy.goal = "goal " * 2_000
    context.nodes[1].prompt = "prompt " * 4_000
    context.nodes[1].response_preview = "Response only available through node inspection"
    context.nodes[1].kept = True
    context.nodes[2].pruned = True
    tools = TreeAssistantTools()
    tools.begin(context)
    envelope = json.loads(tools.inspect_tree(include_pruned=True))
    assert envelope["truncated"] is False
    overview = json.loads(envelope["data"])
    assert overview["selected_node_id"] == "n1"
    assert [(node["id"], node["status"]) for node in overview["nodes"]] == [
        ("outside", "completed"),
        ("n1", "draft"),
        ("child", "draft"),
        ("sibling", "draft"),
    ]
    assert overview["autonomy"]["remaining_operations"] == context.autonomy.remaining_operations
    assert overview["node_count"] == 4 and overview["root_count"] == 1
    assert overview["nodes"][0]["is_root"] is True
    assert overview["nodes"][1]["parent_id"] == "outside"
    assert overview["nodes"][1]["kept"] is True
    assert overview["nodes"][2]["pruned"] is True
    assert overview["objective_truncated"] and overview["autonomy"]["goal_truncated"]
    assert overview["nodes"][1]["prompt_truncated"]
    assert len(overview["nodes"][1]["prompt_preview"]) == 160
    assert overview["next_offset"] is None
    assert "response_preview" not in envelope["data"] and "converters" not in envelope["data"]
    assert len(envelope["data"].encode()) < 3_000
    assert context.objective not in envelope["data"]
    assert context.autonomy.goal not in envelope["data"]
    assert tools.proposal is None


@pytest.mark.parametrize("long_ids", [False, True])
def test_tree_overview_pages_without_breaking_json_or_exceeding_output_budget(long_ids: bool) -> None:
    count = 40 if long_ids else 120
    ids = [f"n{index}".ljust(256 if long_ids else 4, "x") for index in range(count)]
    nodes = [
        {**context_dict()["nodes"][0], "id": node_id, "parent_id": ids[0] if index else None, "prompt": "x" * 160}
        for index, node_id in enumerate(ids)
    ]
    context = TreeAssistantContext.model_validate(context_dict(nodes=nodes, selected_node_id=ids[0]))
    tools = TreeAssistantTools()
    tools.begin(context)
    seen = []
    offset = 0
    while offset is not None:
        envelope = json.loads(tools.inspect_tree(offset=offset))
        assert envelope["truncated"] is False
        assert len(envelope["data"].encode()) <= tools.MAX_OVERVIEW_BYTES
        page = json.loads(envelope["data"])
        assert page["offset"] == offset
        assert page["node_count"] == count
        assert len(page["nodes"]) <= tools.MAX_OVERVIEW_NODES
        seen.extend(node["id"] for node in page["nodes"])
        next_offset = page["next_offset"]
        assert next_offset is None or next_offset > offset
        offset = next_offset
    assert seen == [node["id"] for node in nodes]
    with pytest.raises(ValueError, match="offset"):
        tools.inspect_tree(offset=-1)
    with pytest.raises(ValueError, match="offset"):
        tools.inspect_tree(offset=count + 1)


async def test_fifty_restored_turns_allow_fifty_fresh_turns_and_keep_replay_and_receipts_safe_async() -> None:
    runtime = FakeRuntime()
    service = TreeAssistantService(runtime_factory=lambda: runtime)
    history = [
        TreeAssistantTurn(request_id=str(uuid4()), message=f"Earlier {index}", reply="Earlier reply", proposals=[])
        for index in range(50)
    ]
    old_proposal = stage(action=plan(run=False)).proposal
    history[0].proposals = [old_proposal]
    created = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner="owner", history=history)
    assert len(created.turns) == 50 and not runtime.calls
    requests = [message() for _ in range(50)]
    first = await service.send_message_async(session_id=created.session_id, owner="owner", request=requests[0])
    public = await service.get_session_async(session_id=created.session_id, owner="owner")
    assert len(public.turns) == 50
    assert public.turns[0].request_id == history[1].request_id
    assert public.turns[-1] == first
    for request in requests[1:]:
        await service.send_message_async(session_id=created.session_id, owner="owner", request=request)
    public = await service.get_session_async(session_id=created.session_id, owner="owner")
    assert [turn.request_id for turn in public.turns] == [request.request_id for request in requests]
    assert len(runtime.calls) == 50
    receipt = TreeAssistantReceipt(status="applied", revision=1, detail="The application reported completion.")
    for _ in range(2):
        await service.record_result_async(
            session_id=created.session_id, owner="owner", proposal_id=first.proposals[0].id, receipt=receipt
        )
    replay = await service.send_message_async(session_id=created.session_id, owner="owner", request=requests[0])
    assert replay.proposals[0].result == receipt
    assert len(runtime.calls) == 50
    with pytest.raises(TreeAssistantError, match="live turn limit") as limit:
        await service.send_message_async(session_id=created.session_id, owner="owner", request=message())
    assert limit.value.status == 429
    with pytest.raises(TreeAssistantError, match="different message"):
        await service.send_message_async(
            session_id=created.session_id,
            owner="owner",
            request=message(request_id=requests[0].request_id, message="Changed"),
        )
    with pytest.raises(TreeAssistantError, match="cannot be replayed"):
        await service.send_message_async(
            session_id=created.session_id, owner="owner", request=message(request_id=history[0].request_id)
        )
    with pytest.raises(TreeAssistantError, match="re-plan"):
        await service.record_result_async(
            session_id=created.session_id, owner="owner", proposal_id=old_proposal.id, receipt=receipt
        )
    stored = service._sessions[created.session_id]
    assert stored.fresh_turns == 50 and len(stored.public.turns) == 50
    assert len(stored.fingerprints) == 50 <= service.MAX_REQUEST_IDS
    assert len(stored.restored_requests) == 50
    assert len(stored.restored_proposals) == 1
    await service.close_async()


async def test_restored_history_does_not_consume_failed_request_cache_or_fresh_turn_budget_async() -> None:
    runtime = FakeRuntime()
    service = TreeAssistantService(runtime_factory=lambda: runtime)
    history = [
        TreeAssistantTurn(request_id=str(uuid4()), message="Earlier", reply="Reply", proposals=[]) for _ in range(50)
    ]
    created = await service.create_session_async(workspace_id="workspace-not-a-uuid", owner=None, history=history)
    requests = [message(), message()]
    runtime.fail = True
    with patch.object(service, "MAX_REQUEST_IDS", 2):
        for request in requests:
            with pytest.raises(TreeAssistantError, match="turn failed"):
                await service.send_message_async(session_id=created.session_id, owner=None, request=request)
        stored = service._sessions[created.session_id]
        assert stored.fresh_turns == 0 and stored.public.turns == created.turns
        assert len(stored.fingerprints) == 2
        with pytest.raises(TreeAssistantError, match="request limit"):
            await service.send_message_async(session_id=created.session_id, owner=None, request=message())
        runtime.fail = False
        await service.send_message_async(session_id=created.session_id, owner=None, request=requests[0])
        assert stored.fresh_turns == 1
        assert len(stored.public.turns) == 50 and len(stored.fingerprints) == 2
    await service.close_async()


@pytest.mark.parametrize(
    "objective",
    ["", "short objective", "\U0001f9ea" * 32_000, 'á\U0001f9ea\n"\\' * 6_400],
    ids=["empty", "short", "long-unicode", "long-unicode-with-json-escaping"],
)
def test_objective_pages_reconstruct_full_text_within_per_page_and_turn_bounds(objective: str) -> None:
    context = TreeAssistantContext.model_validate(context_dict(objective=objective))
    tools = TreeAssistantTools()
    tools.begin(context)
    tools.inspect_tree()
    offset = 0
    parts = []
    resumed = 0
    for _ in range(32):
        try:
            envelope = json.loads(tools.inspect_objective(offset))
        except ValueError as exc:
            assert "tool-output budget exhausted" in str(exc)
            assert parts
            tools.begin(context)
            resumed += 1
            continue
        assert envelope["truncated"] is False
        assert len(envelope["data"].encode()) <= tools.MAX_OBJECTIVE_BYTES
        assert tools._output_bytes <= tools.MAX_OUTPUT_BYTES
        page = json.loads(envelope["data"])
        assert page["workspace_id"] == context.workspace_id and page["revision"] == context.revision
        assert page["offset"] == offset and page["total_length"] == len(objective)
        parts.append(page["objective"])
        next_offset = page["next_offset"]
        if next_offset is None:
            assert offset + len(page["objective"]) == len(objective)
            break
        assert next_offset == offset + len(page["objective"]) > offset
        offset = next_offset
    else:
        pytest.fail("Objective pagination did not finish within bounded pages")
    assert "".join(parts) == objective
    assert tools.proposal is None
    if len(objective) == 32_000:
        assert resumed > 0


def test_objective_paging_rejects_invalid_offsets_and_fails_explicitly_when_no_page_fits() -> None:
    context = TreeAssistantContext.model_validate(context_dict(objective="Workspace objective"))
    tools = TreeAssistantTools()
    tools.begin(context)
    for offset in (-1, len(context.objective) + 1):
        with pytest.raises(ValueError, match="Objective offset"):
            tools.inspect_objective(offset)
    end = json.loads(json.loads(tools.inspect_objective(len(context.objective)))["data"])
    assert end["objective"] == "" and end["next_offset"] is None
    with patch.object(tools, "MAX_OUTPUT_BYTES", tools._output_bytes + 1):
        with pytest.raises(ValueError, match="tool-output budget exhausted"):
            tools.inspect_objective()
    assert tools.proposal is None
