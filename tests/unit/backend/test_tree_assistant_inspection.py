# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lossless inspection pages and host-authoritative new-node execution contracts."""

import json
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from pyrit.backend.models.tree_assistant import TreeAssistantAction, TreeAssistantContext
from pyrit.backend.services.tree_assistant_tools import TreeAssistantTools

from .test_tree_assistant import context_dict
from .test_tree_assistant_next import plan, scoped_context, stage


def inspection_context(**overrides: Any) -> TreeAssistantContext:
    template = context_dict()["nodes"][0]
    return TreeAssistantContext.model_validate(
        context_dict(
            **{
                "nodes": [
                    {**template, "id": "grandchild", "parent_id": "child"},
                    {**template, "id": "hidden", "parent_id": "pruned"},
                    {**template, "id": "child", "parent_id": "n1"},
                    {**template, "id": "pruned", "parent_id": "n1", "pruned": True},
                    {**template, "id": "n1"},
                    {**template, "id": "unrelated"},
                ],
                "selected_node_id": "grandchild",
                "autonomy": {"root_node_id": "n1", "remaining_operations": 5, "remaining_turns": 3, "goal": "Explore"},
                **overrides,
            }
        )
    )


def unpack(raw: str) -> dict[str, Any]:
    assert len(raw.encode()) <= TreeAssistantTools.MAX_PAGE_BYTES
    envelope = json.loads(raw)
    assert envelope["truncated"] is False
    return json.loads(envelope["data"])


@pytest.mark.parametrize("selected", [None, "grandchild", "hidden"])
def test_selected_node_uses_actual_selection_not_scope_root(selected: str | None) -> None:
    context = inspection_context(selected_node_id=selected)
    tools = TreeAssistantTools()
    tools.begin(context)
    data = unpack(tools.inspect_selected_node())
    assert data["source"] == "untrusted_browser_snapshot"
    assert data["selected_node_id"] == selected
    assert data["selection"] == ("selected" if selected else "none")
    assert (data["node"]["id"] if data["node"] else None) == selected
    assert data["next_cursor"] is None
    assert tools._inspected and tools.proposal is None


@pytest.mark.parametrize("include_pruned", [False, True])
@pytest.mark.parametrize("detail", ["summary", "nodes"])
@pytest.mark.parametrize("depth", [None, 0, 1])
def test_subtree_parent_first_order_pruning_and_depth_counts(
    *, include_pruned: bool, detail: str, depth: int | None
) -> None:
    tools = TreeAssistantTools()
    context = inspection_context()
    before = context.model_dump()
    tools.begin(context)
    page = unpack(
        tools.inspect_subtree(root_node_id="n1", include_pruned=include_pruned, detail=detail, max_depth=depth)
    )
    expected = ["n1", "child", "grandchild", "pruned", "hidden"] if include_pruned else ["n1", "child", "grandchild"]
    depths = {"n1": 0, "child": 1, "pruned": 1, "grandchild": 2, "hidden": 2}
    expected = [node_id for node_id in expected if depth is None or depths[node_id] <= depth]
    assert [node["id"] for node in page["nodes"]] == expected
    assert page["total_node_count"] == 5
    assert page["included_node_count"] == len(expected)
    assert page["omitted_node_count"] == 5 - len(expected)
    assert page["omitted_pruned_count"] == (0 if include_pruned else 2)
    assert page["omitted_depth_count"] + page["omitted_pruned_count"] == page["omitted_node_count"]
    assert page["total_max_depth"] == 2
    assert page["included_max_depth"] == (2 if depth is None else depth)
    assert page["root_effectively_pruned"] is False and page["root_omitted"] is False
    assert page["next_cursor"] is None
    if detail == "summary":
        assert all(node["depth"] == depths[node["id"]] for node in page["nodes"])
        if include_pruned and depth != 0:
            assert page["nodes"][-1]["effectively_pruned"] is True
    else:
        assert page["nodes"] == [
            next(node for node in context.nodes if node.id == node_id).model_dump() for node_id in expected
        ]
    assert context.model_dump() == before and tools.proposal is None


@pytest.mark.parametrize("root", ["pruned", "hidden"])
def test_explicit_effectively_pruned_root_has_clear_omission_metadata(root: str) -> None:
    tools = TreeAssistantTools()
    tools.begin(inspection_context())
    page = unpack(tools.inspect_subtree(root_node_id=root))
    assert page["nodes"] == [] and page["included_node_count"] == 0
    assert page["root_omitted"] and page["root_effectively_pruned"]
    assert page["root_omission_reason"] == "effectively_pruned"
    assert page["omitted_node_count"] == page["total_node_count"] == page["omitted_pruned_count"]
    assert page["included_max_depth"] is None and page["next_cursor"] is None
    included = unpack(tools.inspect_subtree(root_node_id=root, include_pruned=True))
    assert included["nodes"][0]["id"] == root and included["nodes"][0]["depth"] == 0
    assert included["omitted_node_count"] == 0 and not included["root_omitted"]


def test_tree_overview_default_excludes_pruned_descendants_and_orders_parents_first() -> None:
    tools = TreeAssistantTools()
    tools.begin(inspection_context())
    page = unpack(tools.inspect_tree())
    assert [node["id"] for node in page["nodes"]] == ["n1", "child", "grandchild", "unrelated"]
    assert page["node_count"] == 6 and page["included_node_count"] == 4 and page["omitted_node_count"] == 2
    assert [node["id"] for node in unpack(tools.inspect_tree(include_pruned=True))["nodes"]] == [
        "n1",
        "child",
        "grandchild",
        "pruned",
        "hidden",
        "unrelated",
    ]


@pytest.mark.parametrize("detail", ["summary", "nodes"])
def test_subtree_all_three_hundred_nodes_retrievable_over_bounded_pages_and_fresh_turns(detail: str) -> None:
    template = context_dict()["nodes"][0]
    nodes = [
        {**template, "id": f"n{index}", "parent_id": f"n{index - 1}" if index else None, "prompt": 'á🧪\n"\\' * 40}
        for index in range(300)
    ]
    context = TreeAssistantContext.model_validate(context_dict(nodes=list(reversed(nodes)), selected_node_id="n0"))
    tools = TreeAssistantTools()
    tools.begin(context)
    cursor = None
    seen = []
    for _ in range(100):
        page = unpack(tools.inspect_subtree(root_node_id="n0", detail=detail, cursor=cursor))
        seen.extend(node["id"] for node in page["nodes"])
        assert page["total_node_count"] == page["included_node_count"] == 300
        assert page["total_max_depth"] == page["included_max_depth"] == 299
        assert page["offset"] == len(seen) - len(page["nodes"])
        assert 0 < page["page_node_count"] <= tools.MAX_OVERVIEW_NODES
        assert tools._output_bytes <= tools.MAX_OUTPUT_BYTES
        cursor = page["next_cursor"]
        if cursor is None:
            break
        tools.end()
        tools.begin(context)
    else:
        pytest.fail("Subtree pagination failed to advance")
    assert seen == [node["id"] for node in nodes]


@pytest.mark.parametrize("tool", ["inspect_node", "inspect_selected_node", "inspect_subtree"])
def test_oversized_single_node_reconstructs_losslessly_with_unicode_json_and_converter_data(tool: str) -> None:
    template = context_dict()["nodes"][0]
    node = {
        **template,
        "prompt": 'á🧪\n"\\' * 6_400,
        "converters": [{"type": f"C{index}", "params": {"text": "🧪" * 3_000}} for index in range(3)],
        "response_preview": "🧪" * 2_000,
    }
    context = TreeAssistantContext.model_validate(context_dict(nodes=[node]))
    tools = TreeAssistantTools()
    tools.begin(context)
    arguments = {"root_node_id": "n1", "detail": "nodes"} if tool == "inspect_subtree" else {}
    if tool == "inspect_node":
        arguments["node_id"] = "n1"
    cursor = None
    chunks = []
    exhausted = 0
    for _ in range(80):
        try:
            page = unpack(getattr(tools, tool)(**arguments, cursor=cursor))
        except ValueError as exc:
            assert "tool-output budget exhausted" in str(exc)
            exhausted += 1
            tools.begin(context)
            continue
        chunk = page["node_chunk"]
        assert chunk["offset"] == sum(len(part) for part in chunks)
        assert chunk["node_id"] == "n1" and chunk["parent_id"] is None
        assert chunk["encoding"] == "json" and chunk["text"]
        assert page["source"] == "untrusted_browser_snapshot"
        assert tools._output_bytes <= tools.MAX_OUTPUT_BYTES
        chunks.append(chunk["text"])
        next_cursor = page["next_cursor"]
        assert next_cursor != cursor
        cursor = next_cursor
        if cursor is None:
            assert chunk["complete"]
            assert chunk["total_length"] == sum(len(part) for part in chunks)
            break
    else:
        pytest.fail("Oversized node pagination stalled")
    assert exhausted > 0
    assert json.loads("".join(chunks)) == context.nodes[0].model_dump()


@pytest.mark.parametrize("change", ["workspace", "revision", "snapshot", "root", "depth", "detail", "pruned", "tool"])
def test_subtree_cursor_rejects_stale_context_and_option_changes(change: str) -> None:
    context = inspection_context()
    tools = TreeAssistantTools()
    tools.begin(context)
    with patch.object(tools, "MAX_OVERVIEW_NODES", 1):
        cursor = unpack(tools.inspect_subtree(root_node_id="n1"))["next_cursor"]
    assert cursor
    arguments: dict[str, Any] = {"root_node_id": "n1", "cursor": cursor}
    if change == "workspace":
        context.workspace_id = "different"
    elif change == "revision":
        context.revision += 1
    elif change == "snapshot":
        context.nodes[0].prompt = "Changed without a revision"
    elif change == "root":
        arguments["root_node_id"] = "child"
    elif change == "depth":
        arguments["max_depth"] = 1
    elif change == "detail":
        arguments["detail"] = "nodes"
    elif change == "pruned":
        arguments["include_pruned"] = True
    tools.begin(context)
    with pytest.raises(ValueError, match="stale inspection cursor"):
        if change == "tool":
            tools.inspect_node(node_id="n1", cursor=cursor)
        else:
            tools.inspect_subtree(**arguments)


def test_subtree_cursor_survives_fresh_turn_authority_and_selection_changes() -> None:
    context = inspection_context()
    tools = TreeAssistantTools()
    tools.begin(context)
    with patch.object(tools, "MAX_OVERVIEW_NODES", 1):
        cursor = unpack(tools.inspect_subtree(root_node_id="n1"))["next_cursor"]
    tools.end()
    context.autonomy.remaining_turns -= 1
    context.autonomy.root_node_id = None
    context.settings.auto_run = True
    context.selected_node_id = None
    tools.begin(context)
    page = unpack(tools.inspect_subtree(root_node_id="n1", cursor=cursor))
    assert [node["id"] for node in page["nodes"]] == ["child", "grandchild"]
    assert page["next_cursor"] is None


@pytest.mark.parametrize(
    "arguments",
    [{"root_node_id": "missing"}, {"max_depth": -1}, {"max_depth": True}, {"detail": "other"}, {"cursor": "garbage"}],
)
def test_subtree_invalid_arguments_fail_without_staging(arguments: dict[str, Any]) -> None:
    tools = TreeAssistantTools()
    tools.begin(inspection_context())
    with pytest.raises(ValueError):
        tools.inspect_subtree(**{"root_node_id": "n1", **arguments})
    assert tools.proposal is None


@pytest.mark.parametrize("auto_run", [False, True])
@pytest.mark.parametrize("run", ["omitted", None, False, True])
@pytest.mark.parametrize("kind", ["mutate", "plan"])
def test_run_tristate_inherits_only_fresh_workspace_setting_and_checks_budget(
    *, auto_run: bool, run: bool | str | None, kind: str
) -> None:
    context = scoped_context()
    context.nodes[1].status = "completed"
    context.settings.auto_run = auto_run
    context.settings.operation_budget = 1
    context.autonomy.remaining_operations = 1
    converter = {"type": "Example", "params": {}}
    action: dict[str, Any] = (
        {"kind": "mutate", "commands": [{"type": "add", "parentId": "n1", "prompt": "New", "converters": [converter]}]}
        if kind == "mutate"
        else plan(steps=[{**plan()["steps"][0], "converters": [converter]}])
    )
    action.pop("run", None)
    if run != "omitted":
        action["run"] = run
    effective_run = auto_run if run is None or run == "omitted" else run
    assert (stage(action=action, context=context).proposal is None) is effective_run
    context.settings.operation_budget = context.autonomy.remaining_operations = 2
    proposal = stage(action=action, context=context).proposal
    assert proposal is not None
    assert proposal.action.run is (None if run == "omitted" else run)


@pytest.mark.parametrize(
    ("command", "operations"),
    [
        ({"type": "add", "parentId": "n1", "prompt": "New", "converters": [{"type": "C", "params": {}}]}, 2),
        ({"type": "fork", "nodeId": "child", "prompt": "New", "converters": [{"type": "C", "params": {}}]}, 2),
        ({"type": "edit", "nodeId": "child", "prompt": "New", "converters": [{"type": "C", "params": {}}]}, 2),
        ({"type": "sample", "nodeId": "child", "count": 3}, 6),
        (
            {
                "type": "childVariants",
                "nodeId": "n1",
                "variants": [
                    {"prompt": "One", "converters": [{"type": "C", "params": {}}]},
                    {"prompt": "Two", "converters": []},
                ],
            },
            3,
        ),
    ],
)
def test_auto_run_mutations_budget_only_new_nodes_and_their_converters(
    *, command: dict[str, Any], operations: int
) -> None:
    context = scoped_context()
    context.nodes[2].status = "completed"
    context.nodes[2].converters = (
        TypeAdapter(TreeAssistantAction)
        .validate_python(
            {
                "kind": "mutate",
                "commands": [
                    {"type": "add", "parentId": None, "prompt": "", "converters": [{"type": "C", "params": {}}]}
                ],
            }
        )
        .commands[0]
        .converters
    )
    context.settings.auto_run = True
    context.settings.scorer_ids = ["score1", "score2", "score3"]
    context.autonomy.remaining_operations = operations - 1
    action = {"kind": "mutate", "commands": [command]}
    before = context.model_dump()
    assert stage(action=action, context=context).proposal is None
    assert context.model_dump() == before
    context.autonomy.remaining_operations = operations
    assert stage(action=action, context=context).proposal is not None
    context.autonomy.remaining_operations = 0
    assert stage(action={**action, "run": False}, context=context).proposal is not None


def test_auto_run_does_not_include_in_place_edits_retries_ancestors_or_unrelated_drafts() -> None:
    context = scoped_context()
    context.settings.auto_run = True
    context.autonomy.remaining_operations = 0
    for command in [
        {"type": "edit", "nodeId": "child", "prompt": "Edited", "converters": []},
        {"type": "retry", "nodeId": "n1", "scope": "subtree"},
        {"type": "keep", "nodeId": "child"},
    ]:
        assert stage(action={"kind": "mutate", "commands": [command]}, context=context).proposal is not None
    context.autonomy.remaining_operations = 1
    assert (
        stage(
            action={"kind": "mutate", "commands": [{"type": "add", "parentId": "child", "prompt": "New"}]},
            context=context,
        ).proposal
        is not None
    )


@pytest.mark.parametrize("kind", ["mutate", "plan"])
def test_whole_workspace_autonomy_allows_empty_tree_roots_without_changing_selection(kind: str) -> None:
    context = TreeAssistantContext.model_validate(
        context_dict(
            nodes=[],
            selected_node_id=None,
            autonomy={"root_node_id": None, "remaining_operations": 1, "remaining_turns": 1, "goal": "Start"},
        )
    )
    assert context.settings.auto_run is False
    context.settings.auto_run = True
    action = (
        {"kind": "mutate", "commands": [{"type": "add", "parentId": None, "prompt": "New root"}]}
        if kind == "mutate"
        else plan(run=None, steps=[{**plan()["steps"][0], "parent": None}])
    )
    assert stage(action=action, context=context).proposal is not None
    assert context.selected_node_id is None and context.autonomy.root_node_id is None and context.nodes == []
    context.autonomy.remaining_turns = 0
    assert stage(action=action, context=context).proposal is None


def test_whole_workspace_scope_allows_existing_roots_siblings_and_independent_selection() -> None:
    context = scoped_context()
    context.autonomy.root_node_id = None
    context.selected_node_id = "child"
    for action in [
        {"kind": "run", "node_ids": ["outside", "sibling"]},
        {"kind": "mutate", "commands": [{"type": "keep", "nodeId": "outside"}]},
        {"kind": "mutate", "commands": [{"type": "sample", "nodeId": "outside", "count": 1}]},
        plan(run=False, steps=[{**plan()["steps"][0], "parent": None}]),
    ]:
        assert stage(action=action, context=context).proposal is not None
    assert context.selected_node_id == "child"
