# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read-only assistant tools and a staging slot; no PyRIT execution capabilities."""

import asyncio
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from pyrit.backend.models.tree_assistant import (
    AddMutation,
    ChildVariantsMutation,
    EditMutation,
    ExecuteAction,
    ExistingNodeParent,
    MutateAction,
    PlanAction,
    RetryMutation,
    SampleMutation,
    TreeAssistantAction,
    TreeAssistantContext,
    TreeAssistantNode,
    TreeAssistantProposal,
)


class TreeAssistantTools:
    """Per-session tool bindings with fresh, bounded state for every turn."""

    MAX_CALLS = 16
    MAX_OUTPUT_BYTES = 64_000
    MAX_OVERVIEW_BYTES = 16_000
    MAX_OVERVIEW_NODES = 50
    MAX_OBJECTIVE_BYTES = 16_000
    MAX_PAGE_BYTES = 24_000

    def __init__(self) -> None:
        """Initialize an inactive tool surface."""
        self.context: TreeAssistantContext | None = None
        self.proposal: TreeAssistantProposal | None = None
        self._calls = 0
        self._output_bytes = 0
        self._inspected = False

    def begin(self, context: TreeAssistantContext) -> None:
        """Set the current immutable-by-convention snapshot."""
        self.context = context
        self.proposal = None
        self._calls = 0
        self._output_bytes = 0
        self._inspected = False

    def end(self) -> None:
        """Discard the turn snapshot and staging slot."""
        self.context = None
        self.proposal = None

    def inspect_tree(self, *, offset: int = 0, include_pruned: bool = False) -> str:
        """
        Read compact topology and prompt previews without approval; follow next_offset for more nodes.

        Parent-first ordering preserves snapshot sibling order. Offset indexes the filtered list;
        include_pruned defaults to false and removes descendants of pruned ancestors. Keep that
        option unchanged across pages and restart on workspace/revision changes.

        Returns:
            str: A bounded overview page; use inspect_node or inspect_evidence_async for detail.
        """
        context = self._check_call()
        ordered = self._ordered_nodes()
        visible = [item for item in ordered if include_pruned or not item[2]]
        if offset < 0 or offset > len(visible):
            raise ValueError("Tree overview offset is outside the current node list")
        self._inspected = True
        data: dict[str, Any] = {
            "source": "untrusted_browser_snapshot",
            "workspace_id": context.workspace_id,
            "revision": context.revision,
            "selected_node_id": context.selected_node_id,
            "node_count": len(context.nodes),
            "root_count": sum(node.parent_id is None for node in context.nodes),
            "include_pruned": include_pruned,
            "included_node_count": len(visible),
            "omitted_node_count": len(ordered) - len(visible),
            "name_preview": context.name[:160],
            "name_truncated": len(context.name) > 160,
            "objective_preview": context.objective[:160],
            "objective_truncated": len(context.objective) > 160,
            "settings": {
                "operation_budget": context.settings.operation_budget,
                "scorer_count": len(context.settings.scorer_ids),
                "traversal": context.settings.traversal,
                "concurrency": context.settings.concurrency,
                "auto_run": context.settings.auto_run,
            },
            "autonomy": {
                **context.autonomy.model_dump(exclude={"goal"}),
                "goal_preview": context.autonomy.goal[:160],
                "goal_truncated": len(context.autonomy.goal) > 160,
            }
            if context.autonomy
            else None,
            "offset": offset,
            "next_offset": None,
            "nodes": [],
        }
        for node, depth, effectively_pruned in visible[offset : offset + self.MAX_OVERVIEW_NODES]:
            data["nodes"].append(self._node_summary(node=node, depth=depth, effectively_pruned=effectively_pruned))
            data["next_offset"] = offset + len(data["nodes"])
            if not self._page_fits(data=data, maximum=self.MAX_OVERVIEW_BYTES):
                data["nodes"].pop()
                break
        if offset < len(visible) and not data["nodes"]:
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
        next_offset = offset + len(data["nodes"])
        data["next_offset"] = next_offset if next_offset < len(visible) else None
        return self._output_page(data=data, maximum=self.MAX_OVERVIEW_BYTES)

    def inspect_objective(self, offset: int = 0) -> str:
        """
        Read the workspace objective without approval, using lossless bounded pages.

        Args:
            offset (int): Start in Unicode code points. Use the previous page's next_offset.

        Returns:
            str: JSON containing workspace/revision, objective text, total_length, offset and next_offset.
        """
        context = self._check_call()
        total = len(context.objective)
        if offset < 0 or offset > total:
            raise ValueError("Objective offset is outside the current objective")

        def page(end: int) -> dict[str, Any]:
            return {
                "workspace_id": context.workspace_id,
                "revision": context.revision,
                "total_length": total,
                "offset": offset,
                "next_offset": end if end < total else None,
                "objective": context.objective[offset:end],
            }

        low, high = offset, total
        while low < high:
            end = (low + high + 1) // 2
            if self._page_fits(data=page(end), maximum=self.MAX_OBJECTIVE_BYTES):
                low = end
            else:
                high = end - 1
        result = page(low)
        if low == offset and offset < total:
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
        return self._output_page(data=result, maximum=self.MAX_OBJECTIVE_BYTES)

    def inspect_node(self, *, node_id: str, cursor: str | None = None) -> str:
        """
        Read a full browser snapshot without approval, not verified stored backend evidence.

        Pass next_cursor unchanged with the same node_id until null. A large node uses node=null
        and node_chunk: concatenate its text in offset order, then JSON-decode to recover the node.
        Offsets/total_length count Unicode code points of serialized node JSON. Cursors are bound
        to this snapshot and options, survive fresh turns, and must be restarted after changes.

        Returns:
            str: Valid JSON envelope with complete node or a lossless bounded JSON-text chunk.
        """
        context = self._check_call()
        node = self._node(node_id)
        binding = self._cursor_binding(context=context, options=["node", node_id])
        return self._single_node_page(node=node, binding=binding, cursor=cursor, metadata={})

    def inspect_selected_node(self, cursor: str | None = None) -> str:
        """
        Read the actual current selection without approval, never the autonomy scope root.

        No selection returns selection="none", selected_node_id=null and node=null.
        Otherwise follow next_cursor with this tool; node_chunk follows inspect_node's contract.

        Returns:
            str: Bounded, explicitly unverified snapshot of the selected node, or no-selection metadata.
        """
        context = self._check_call()
        binding = self._cursor_binding(context=context, options=["selected", context.selected_node_id])
        metadata = {
            "selected_node_id": context.selected_node_id,
            "selection": "none" if context.selected_node_id is None else "selected",
        }
        node = self._node(context.selected_node_id) if context.selected_node_id is not None else None
        return self._single_node_page(node=node, binding=binding, cursor=cursor, metadata=metadata)

    def inspect_subtree(
        self,
        *,
        root_node_id: str,
        include_pruned: bool = False,
        max_depth: int | None = None,
        detail: Literal["summary", "nodes"] = "summary",
        cursor: str | None = None,
    ) -> str:
        """
        Inspect a parent-first subtree without approval; root depth is zero.

        Summary pages contain previews; nodes pages contain full unverified browser snapshots.
        Pruning includes ancestors outside the root. Counts describe the entire selection, not
        just this page. Omitted-pruned counts take precedence over omitted-depth counts.
        page_node_count counts complete entries in nodes, excluding the separate node_chunk.
        Follow next_cursor unchanged with identical root/options. A large single node uses
        node_chunk (concatenate text by Unicode offset, then JSON-decode); the cursor advances
        to the next node only after its last chunk. Full nodes and chunks retain parent_id.
        Cursors bind workspace, revision, snapshot and options, remain usable in fresh turns,
        and are rejected after changes. Restart without a cursor after changing options/context.

        Returns:
            str: A bounded valid JSON envelope with counts, depths, nodes and next_cursor.
        """
        context = self._check_call()
        self._node(root_node_id)
        if max_depth is not None and (type(max_depth) is not int or max_depth < 0):
            raise ValueError("Subtree max_depth must be a nonnegative integer or null")
        if detail not in ("summary", "nodes"):
            raise ValueError("Subtree detail must be summary or nodes")
        ordered = self._ordered_nodes(root_node_id)
        visible = [
            item for item in ordered if (include_pruned or not item[2]) and (max_depth is None or item[1] <= max_depth)
        ]
        pruned_count = sum(not include_pruned and item[2] for item in ordered)
        binding = self._cursor_binding(
            context=context, options=["subtree", root_node_id, include_pruned, max_depth, detail]
        )
        index, chunk_offset = self._cursor_position(cursor=cursor, binding=binding)
        if index > len(visible) or (chunk_offset and (detail != "nodes" or index == len(visible))):
            raise ValueError("Invalid subtree cursor position")
        data: dict[str, Any] = {
            **self._snapshot_metadata(),
            "root_node_id": root_node_id,
            "include_pruned": include_pruned,
            "max_depth": max_depth,
            "detail": detail,
            "total_node_count": len(ordered),
            "included_node_count": len(visible),
            "omitted_node_count": len(ordered) - len(visible),
            "omitted_pruned_count": pruned_count,
            "omitted_depth_count": len(ordered) - len(visible) - pruned_count,
            "total_max_depth": max(item[1] for item in ordered),
            "included_max_depth": max((item[1] for item in visible), default=None),
            "root_effectively_pruned": ordered[0][2],
            "root_omitted": not visible,
            "root_omission_reason": "effectively_pruned" if not visible else None,
            "offset": index,
            "page_node_count": 0,
            "nodes": [],
            "node_chunk": None,
            "next_cursor": None,
        }
        for position in range(index, min(len(visible), index + self.MAX_OVERVIEW_NODES)):
            node, depth, effectively_pruned = visible[position]
            record = (
                self._node_summary(node=node, depth=depth, effectively_pruned=effectively_pruned)
                if detail == "summary"
                else node.model_dump()
            )
            data["nodes"].append(record)
            data["page_node_count"] = len(data["nodes"])
            data["next_cursor"] = self._next_cursor(binding=binding, index=position + 1, total=len(visible))
            if chunk_offset or not self._page_fits(data=data):
                data["nodes"].pop()
                data["page_node_count"] = len(data["nodes"])
                data["next_cursor"] = self._next_cursor(binding=binding, index=position, total=len(visible))
                if not data["nodes"] and detail == "nodes":
                    data = self._chunk_page(
                        data=data, node=node, binding=binding, index=position, total=len(visible), offset=chunk_offset
                    )
                elif not data["nodes"]:
                    raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
                break
        self._inspected = True
        return self._output_page(data=data)

    def _single_node_page(
        self, *, node: TreeAssistantNode | None, binding: str, cursor: str | None, metadata: dict[str, Any]
    ) -> str:
        index, offset = self._cursor_position(cursor=cursor, binding=binding)
        if index != 0 or (cursor is not None and node is None):
            raise ValueError("Invalid node cursor position")
        data = {
            **self._snapshot_metadata(),
            **metadata,
            "node": node.model_dump() if node is not None else None,
            "node_chunk": None,
            "next_cursor": None,
        }
        if node is not None and (offset or not self._page_fits(data=data)):
            data["node"] = None
            data = self._chunk_page(data=data, node=node, binding=binding, index=0, total=1, offset=offset)
        self._inspected = True
        return self._output_page(data=data)

    def _chunk_page(
        self, *, data: dict[str, Any], node: TreeAssistantNode, binding: str, index: int, total: int, offset: int
    ) -> dict[str, Any]:
        text = node.model_dump_json()
        if offset >= len(text):
            raise ValueError("Invalid node chunk cursor position")

        def page(end: int) -> dict[str, Any]:
            return {
                **data,
                "node_chunk": {
                    "node_id": node.id,
                    "parent_id": node.parent_id,
                    "encoding": "json",
                    "offset": offset,
                    "total_length": len(text),
                    "text": text[offset:end],
                    "complete": end == len(text),
                },
                "next_cursor": f"{binding}.{index}.{end}"
                if end < len(text)
                else self._next_cursor(binding=binding, index=index + 1, total=total),
            }

        low, high = offset, len(text)
        while low < high:
            end = (low + high + 1) // 2
            if self._page_fits(data=page(end)):
                low = end
            else:
                high = end - 1
        if low == offset:
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn with the same cursor")
        return page(low)

    def _snapshot_metadata(self) -> dict[str, Any]:
        assert self.context is not None
        return {
            "source": "untrusted_browser_snapshot",
            "workspace_id": self.context.workspace_id,
            "revision": self.context.revision,
        }

    def _ordered_nodes(self, root_node_id: str | None = None) -> list[tuple[TreeAssistantNode, int, bool]]:
        assert self.context is not None
        children: dict[str | None, list[TreeAssistantNode]] = {}
        nodes = {node.id: node for node in self.context.nodes}
        for node in self.context.nodes:
            children.setdefault(node.parent_id, []).append(node)
        ancestor = root_node_id
        inherited_pruning = False
        while ancestor is not None:
            inherited_pruning |= nodes[ancestor].pruned
            ancestor = nodes[ancestor].parent_id
        roots = [nodes[root_node_id]] if root_node_id is not None else children.get(None, [])
        pending = [(node, 0, inherited_pruning) for node in reversed(roots)]
        ordered = []
        while pending:
            node, depth, inherited = pending.pop()
            effectively_pruned = inherited or node.pruned
            ordered.append((node, depth, effectively_pruned))
            pending.extend((child, depth + 1, effectively_pruned) for child in reversed(children.get(node.id, [])))
        return ordered

    @staticmethod
    def _node_summary(*, node: TreeAssistantNode, depth: int, effectively_pruned: bool) -> dict[str, Any]:
        return {
            "id": node.id,
            "parent_id": node.parent_id,
            "depth": depth,
            "is_root": node.parent_id is None,
            "status": node.status,
            "pruned": node.pruned,
            "effectively_pruned": effectively_pruned,
            "kept": node.kept,
            "prompt_preview": node.prompt[:160],
            "prompt_truncated": len(node.prompt) > 160,
        }

    @staticmethod
    def _cursor_binding(*, context: TreeAssistantContext, options: list[Any]) -> str:
        snapshot = context.model_dump_json(include={"workspace_id", "revision", "nodes"})
        return hashlib.sha256((snapshot + json.dumps(options)).encode()).hexdigest()

    @staticmethod
    def _cursor_position(*, cursor: str | None, binding: str) -> tuple[int, int]:
        if cursor is None:
            return 0, 0
        parts = cursor.split(".")
        if (
            len(parts) != 3
            or parts[0] != binding
            or any(not part.isascii() or not part.isdecimal() for part in parts[1:])
        ):
            raise ValueError("Invalid or stale inspection cursor; restart without a cursor")
        if any(len(part) > 8 for part in parts[1:]):
            raise ValueError("Invalid inspection cursor position")
        return int(parts[1]), int(parts[2])

    @staticmethod
    def _next_cursor(*, binding: str, index: int, total: int) -> str | None:
        return f"{binding}.{index}.0" if index < total else None

    @staticmethod
    def _page_json(data: dict[str, Any]) -> str:
        return json.dumps(
            {"data": json.dumps(data, ensure_ascii=False), "truncated": False, "notice": ""}, ensure_ascii=False
        )

    def _page_fits(self, *, data: dict[str, Any], maximum: int = MAX_PAGE_BYTES) -> bool:
        return len(self._page_json(data).encode()) <= min(maximum, self.MAX_OUTPUT_BYTES - self._output_bytes)

    def _output_page(self, *, data: dict[str, Any], maximum: int = MAX_PAGE_BYTES) -> str:
        raw = self._page_json(data)
        if not self._page_fits(data=data, maximum=maximum):
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
        self._output_bytes += len(raw.encode())
        return raw

    async def converter_catalog_async(self, converter_type: str = "") -> str:
        """
        Read converter types, or safe parameter metadata for one exact catalog type.

        Returns:
            str: Bounded catalog metadata JSON, excluding defaults and credentials.
        """
        self._check_call()
        from pyrit.backend.services.converter_service import get_converter_service

        service = await asyncio.to_thread(get_converter_service)
        catalog = await service.list_converter_catalog_async()
        return self._catalog_output(items=catalog.items, name_field="converter_type", selected=converter_type)

    async def scorer_catalog_async(self, scorer_type: str = "") -> str:
        """
        Read scorer types, or safe parameter metadata for one exact catalog type.

        Returns:
            str: Bounded catalog metadata JSON, excluding defaults and credentials.
        """
        self._check_call()
        from pyrit.backend.services.scorer_service import get_scorer_service

        service = await asyncio.to_thread(get_scorer_service)
        catalog = await service.list_scorer_catalog_async()
        return self._catalog_output(items=catalog.items, name_field="scorer_type", selected=scorer_type)

    async def registered_scorers_async(self) -> str:
        """
        Read compact registered scorer IDs, hashes, types and score families, never scorer objects.

        Returns:
            str: Bounded compact metadata for the workspace's selected scorers.
        """
        context = self._check_call()
        from pyrit.backend.services.scorer_service import get_scorer_service

        service = await asyncio.to_thread(get_scorer_service)
        result = []
        for scorer_id in context.settings.scorer_ids:
            scorer = await service.get_scorer_async(scorer_id=scorer_id)
            if scorer:
                result.append(scorer.model_dump())
        return self._output(result)

    async def inspect_evidence_async(self, node_id: str) -> str:
        """
        Read stored text evidence without approval for this node's attack/conversation and sequence cutoff.

        Returns:
            str: Verified, bounded text evidence or an explicit missing-reference notice.
        """
        context = self._check_call()
        node = self._node(node_id)
        if not node.attack_result_id or not node.conversation_id or node.last_sequence is None:
            return self._output({"error": "Node has no complete stored-evidence reference"})
        evidence = await asyncio.to_thread(self._read_evidence, context=context, node=node)
        self._inspected = True
        return self._output(evidence)

    def stage_proposal(self, *, summary: str, action_json: str) -> str:
        """
        Submit ONE typed action to the workspace's configured interaction mode.

        Args:
            summary (str): Concise purpose of the workspace action.
            action_json (str): A whitelisted mutate/run/score/plan action encoded as JSON.

        Returns:
            str: Pending proposal identity or a safe validation error.
        """
        context = self._check_call()
        if self.proposal is not None:
            return self._output({"error": "A proposal is already staged; it cannot be replaced in this turn"})
        if not self._inspected:
            return self._output({"error": "Inspect the tree or relevant node before proposing"})
        if len(action_json.encode()) > 128_000 or not summary.strip() or len(summary) > 2_000:
            return self._output({"error": "Proposal exceeds bounds or has no summary"})
        try:
            action = TypeAdapter(TreeAssistantAction).validate_json(action_json)
            self._validate_action(context=context, action=action)
        except (ValidationError, ValueError):
            return self._output(
                {"error": "Invalid proposal: check action schema, ordered plan parents, node limits, scope and budgets"}
            )
        proposal = TreeAssistantProposal(
            id=str(uuid4()),
            workspace_id=context.workspace_id,
            base_revision=context.revision,
            summary=summary,
            action=action,
        )
        output = self._output(
            {
                "proposal_id": proposal.id,
                "status": "pending",
                "application_mode": "auto" if context.autonomy is not None else "interactive",
                "notice": (
                    "Submitted to the workspace action pipeline. "
                    "The current mode, run policy, scope and budget govern application. "
                    "Use the execution receipt to confirm the outcome."
                ),
            }
        )
        self.proposal = proposal
        return output

    def propose_action(self, *, summary: str, action: TreeAssistantAction) -> str:
        """
        Submit typed tree edits, a multilevel plan, or a run/score selection for workspace application.

        A retry prepares drafts and invalidates descendant attempts; it never sends.
        A plan may combine creation and execution in one workspace action.
        The workspace applies the action according to the current mode and returns an execution receipt.

        Returns:
            str: A pending proposal identity or an explicit validation error.
        """
        validated = TypeAdapter(TreeAssistantAction).validate_python(action)
        return self.stage_proposal(summary=summary, action_json=validated.model_dump_json())

    def _check_call(self) -> TreeAssistantContext:
        if self.context is None:
            raise ValueError("No active assistant turn")
        self._calls += 1
        if self._calls > self.MAX_CALLS:
            raise ValueError("Assistant tool-call budget exhausted")
        return self.context

    def _node(self, node_id: str) -> TreeAssistantNode:
        if self.context:
            for node in self.context.nodes:
                if node.id == node_id:
                    return node
        raise ValueError("Unknown node ID")

    def _output(self, data: Any) -> str:
        raw = json.dumps(data, ensure_ascii=False, default=str).encode()
        limit = min(24_000, self.MAX_OUTPUT_BYTES - self._output_bytes)
        if limit <= 0:
            raise ValueError("Assistant tool-output budget exhausted")
        self._output_bytes += min(len(raw), limit)
        return json.dumps(
            {
                "data": raw[:limit].decode(errors="ignore"),
                "truncated": len(raw) > limit,
                "notice": "Truncated to tool-output budget; inspect a narrower item." if len(raw) > limit else "",
            },
            ensure_ascii=False,
        )

    def _catalog_output(self, *, items: list[Any], name_field: str, selected: str) -> str:
        output = []
        for item in items:
            data = item.model_dump()
            if selected and data[name_field] != selected:
                continue
            entry = {key: data[key] for key in (name_field, "is_llm_based", "score_type") if key in data}
            entry["description"] = (data.get("description") or "")[:1_000]
            if selected:
                entry["parameters"] = [
                    {
                        key: parameter[key]
                        for key in ("name", "type_name", "required", "input_kind", "is_list", "reference_kind")
                        if key in parameter
                    }
                    for parameter in data.get("parameters", [])[:30]
                ]
            output.append(entry)
        return self._output(output or {"error": "Catalog type not found"})

    @staticmethod
    def _validate_action(*, context: TreeAssistantContext, action: TreeAssistantAction) -> None:
        nodes = {node.id: node for node in context.nodes}
        TreeAssistantTools._validate_scope(context=context, action=action)
        if isinstance(action, ExecuteAction):
            if not set(action.node_ids) <= nodes.keys():
                raise ValueError("Unknown node")
            selected = [node for node in context.nodes if node.id in action.node_ids]
            operations = (
                len(selected) * max(1, len(context.settings.scorer_ids))
                if action.kind == "score"
                else sum(1 + len(node.converters) for node in selected)
            )
            TreeAssistantTools._validate_budget(context=context, operations=operations)
            return
        if isinstance(action, PlanAction):
            effective_run = context.settings.auto_run if action.run is None else action.run
            for step in action.steps:
                if step.id in nodes:
                    raise ValueError("Plan IDs conflict with existing nodes")
                if isinstance(step.parent, ExistingNodeParent) and step.parent.node_id not in nodes:
                    raise ValueError("Unknown plan parent")
                if (
                    effective_run
                    and isinstance(step.parent, ExistingNodeParent)
                    and nodes[step.parent.node_id].status != "completed"
                ):
                    raise ValueError("Run existing draft anchors before executing a plan")
            if len(nodes) + len(action.steps) > 300:
                raise ValueError("Plan exceeds node limit")
            operations = sum(1 + len(step.converters) for step in action.steps) if effective_run else 0
            TreeAssistantTools._validate_budget(context=context, operations=operations)
            return
        assert isinstance(action, MutateAction)
        added = 0
        operations = 0
        statuses = {node.id: node.status for node in context.nodes}
        converters = {node.id: len(node.converters) for node in context.nodes}
        for command in action.commands:
            if isinstance(command, AddMutation):
                if command.parent_id is not None and command.parent_id not in nodes:
                    raise ValueError("Unknown parent")
                added += 1
                operations += 1 + len(command.converters)
            else:
                if command.node_id not in nodes:
                    raise ValueError("Unknown node")
                if isinstance(command, ChildVariantsMutation):
                    added += len(command.variants)
                    operations += sum(1 + len(variant.converters) for variant in command.variants)
                elif isinstance(command, SampleMutation):
                    added += command.count
                    operations += command.count * (1 + converters[command.node_id])
                elif isinstance(command, EditMutation):
                    if command.type == "fork" or statuses[command.node_id] != "draft":
                        added += 1
                        operations += 1 + len(command.converters)
                    else:
                        converters[command.node_id] = len(command.converters)
                elif isinstance(command, RetryMutation):
                    for node in context.nodes:
                        ancestor: str | None = node.id
                        while ancestor is not None:
                            if ancestor == command.node_id:
                                statuses[node.id] = "draft"
                                break
                            if command.scope == "node":
                                break
                            ancestor = nodes[ancestor].parent_id
        if len(nodes) + added > 300:
            raise ValueError("Proposal exceeds node limit")
        effective_run = context.settings.auto_run if action.run is None else action.run
        TreeAssistantTools._validate_budget(context=context, operations=operations if effective_run else 0)

    @staticmethod
    def _validate_budget(*, context: TreeAssistantContext, operations: int) -> None:
        # This is a lower bound: the frontend engine also accounts for auto-scoring and dependencies.
        budget = context.settings.operation_budget
        if context.autonomy is not None:
            budget = min(budget, context.autonomy.remaining_operations)
        if operations > budget:
            raise ValueError("Selection exceeds the current operation budget")

    @staticmethod
    def _validate_scope(*, context: TreeAssistantContext, action: TreeAssistantAction) -> None:
        grant = context.autonomy
        if grant is None:
            return
        if grant.remaining_turns < 1:
            raise ValueError("Autonomy planning turn budget exhausted")
        if grant.root_node_id is None:
            return
        nodes = {node.id: node for node in context.nodes}
        scope = {grant.root_node_id}
        for node in context.nodes:
            ancestor: str | None = node.id
            while ancestor is not None:
                if ancestor == grant.root_node_id:
                    scope.add(node.id)
                    break
                ancestor = nodes[ancestor].parent_id
        if isinstance(action, ExecuteAction):
            if not set(action.node_ids) <= scope:
                raise ValueError("Execution escapes the selected subtree")
        elif isinstance(action, PlanAction):
            for step in action.steps:
                if step.parent is None or (
                    isinstance(step.parent, ExistingNodeParent) and step.parent.node_id not in scope
                ):
                    raise ValueError("Plan escapes the selected subtree")
        else:
            for command in action.commands:
                node_id = command.parent_id if isinstance(command, AddMutation) else command.node_id
                if node_id not in scope:
                    raise ValueError("Mutation escapes the selected subtree")
                if node_id == grant.root_node_id and (
                    command.type in ("keep", "fork", "sample")
                    or (command.type == "edit" and nodes[node_id].status != "draft")
                ):
                    raise ValueError("Mutation would affect siblings outside the selected subtree")

    @staticmethod
    def _read_evidence(*, context: TreeAssistantContext, node: TreeAssistantNode) -> dict[str, Any]:
        from pyrit.memory import CentralMemory

        if node.attack_result_id is None or node.conversation_id is None or node.last_sequence is None:
            raise ValueError("Missing evidence reference")
        memory = CentralMemory.get_memory_instance()
        results = memory.get_attack_results(attack_result_ids=[node.attack_result_id])
        if not results or node.conversation_id not in results[0].get_active_conversation_ids():
            raise ValueError("Evidence does not belong to the referenced attack")
        identifier = results[0].get_attack_strategy_identifier()
        target = identifier.get_child("objective_target") if identifier else None
        if target is None or target.hash.lower() != context.target_identifier_hash.lower():
            raise ValueError("Stored evidence target does not match workspace target")
        messages = list(memory.get_conversation_messages(conversation_id=node.conversation_id))
        pieces = [piece for message in messages for piece in message.message_pieces]
        if not any(piece.sequence == node.last_sequence for piece in pieces):
            raise ValueError("Stored evidence sequence cutoff is missing")
        selected = [
            piece for piece in pieces if piece.sequence <= node.last_sequence and piece.role in ("user", "assistant")
        ]
        return {
            "source": "stored_backend_evidence",
            "conversation_id": node.conversation_id,
            "last_sequence": node.last_sequence,
            "truncated": len(selected) > 20,
            "pieces": [
                {
                    "sequence": piece.sequence,
                    "role": piece.role,
                    "data_type": piece.converted_value_data_type,
                    "text": piece.converted_value[:2_000]
                    if piece.converted_value_data_type == "text"
                    else "[non-text]",
                    "truncated": piece.converted_value_data_type == "text" and len(piece.converted_value) > 2_000,
                }
                for piece in selected[-20:]
            ],
        }
