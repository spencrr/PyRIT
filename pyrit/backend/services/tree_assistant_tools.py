# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read-only assistant tools and a staging slot; no PyRIT execution capabilities."""

import asyncio
import json
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from pyrit.backend.models.tree_assistant import (
    AddMutation,
    ChildVariantsMutation,
    ExecuteAction,
    ExistingNodeParent,
    MutateAction,
    PlanAction,
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

    def inspect_tree(self, offset: int = 0) -> str:
        """
        Read compact topology and prompt previews without approval; follow next_offset for more nodes.

        Returns:
            str: A bounded overview page; use inspect_node or inspect_evidence_async for detail.
        """
        context = self._check_call()
        if offset < 0 or offset > len(context.nodes):
            raise ValueError("Tree overview offset is outside the current node list")
        self._inspected = True
        data: dict[str, Any] = {
            "workspace_id": context.workspace_id,
            "revision": context.revision,
            "selected_node_id": context.selected_node_id,
            "node_count": len(context.nodes),
            "root_count": sum(node.parent_id is None for node in context.nodes),
            "name_preview": context.name[:160],
            "name_truncated": len(context.name) > 160,
            "objective_preview": context.objective[:160],
            "objective_truncated": len(context.objective) > 160,
            "settings": {
                "operation_budget": context.settings.operation_budget,
                "scorer_count": len(context.settings.scorer_ids),
                "traversal": context.settings.traversal,
                "concurrency": context.settings.concurrency,
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
        limit = min(self.MAX_OVERVIEW_BYTES, self.MAX_OUTPUT_BYTES - self._output_bytes)
        for node in context.nodes[offset : offset + self.MAX_OVERVIEW_NODES]:
            data["nodes"].append(
                {
                    "id": node.id,
                    "parent_id": node.parent_id,
                    "is_root": node.parent_id is None,
                    "status": node.status,
                    "pruned": node.pruned,
                    "kept": node.kept,
                    "prompt_preview": node.prompt[:160],
                    "prompt_truncated": len(node.prompt) > 160,
                }
            )
            data["next_offset"] = offset + len(data["nodes"])
            if len(json.dumps(data, ensure_ascii=False).encode()) > limit:
                data["nodes"].pop()
                break
        if offset < len(context.nodes) and not data["nodes"]:
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
        next_offset = offset + len(data["nodes"])
        data["next_offset"] = next_offset if next_offset < len(context.nodes) else None
        return self._output(data)

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
        limit = min(self.MAX_OBJECTIVE_BYTES, self.MAX_OUTPUT_BYTES - self._output_bytes)

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
            if len(json.dumps(page(end), ensure_ascii=False).encode()) <= limit:
                low = end
            else:
                high = end - 1
        result = page(low)
        if (low == offset and offset < total) or len(json.dumps(result, ensure_ascii=False).encode()) > limit:
            raise ValueError("Assistant tool-output budget exhausted; request a fresh turn")
        return self._output(result)

    def inspect_node(self, node_id: str) -> str:
        """
        Read one full bounded node snapshot without approval, including response and score previews.

        Returns:
            str: Bounded node snapshot JSON.
        """
        self._check_call()
        self._inspected = True
        return self._output({"source": "untrusted_browser_snapshot", "node": self._node(node_id).model_dump()})

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
        Stage ONE proposal for human approval, never execution.

        Args:
            summary (str): Concise purpose for human review.
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
            {"proposal_id": proposal.id, "status": "pending", "notice": "Not applied. Await human review."}
        )
        self.proposal = proposal
        return output

    def propose_action(self, *, summary: str, action: TreeAssistantAction) -> str:
        """
        Propose typed tree edits, a multilevel plan, or an explicit run/score selection.

        A retry prepares drafts and invalidates descendant attempts; it never sends.
        A plan may request creation and execution under one application approval.
        No action is applied by this tool.

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
            for step in action.steps:
                if step.id in nodes:
                    raise ValueError("Plan IDs conflict with existing nodes")
                if isinstance(step.parent, ExistingNodeParent) and step.parent.node_id not in nodes:
                    raise ValueError("Unknown plan parent")
                if (
                    action.run
                    and isinstance(step.parent, ExistingNodeParent)
                    and nodes[step.parent.node_id].status != "completed"
                ):
                    raise ValueError("Run existing draft anchors before executing a plan")
            if len(nodes) + len(action.steps) > 300:
                raise ValueError("Plan exceeds node limit")
            operations = sum(1 + len(step.converters) for step in action.steps) if action.run else 0
            TreeAssistantTools._validate_budget(context=context, operations=operations)
            return
        assert isinstance(action, MutateAction)
        added = 0
        for command in action.commands:
            if isinstance(command, AddMutation):
                if command.parent_id is not None and command.parent_id not in nodes:
                    raise ValueError("Unknown parent")
                added += 1
            else:
                if command.node_id not in nodes:
                    raise ValueError("Unknown node")
                if isinstance(command, ChildVariantsMutation):
                    added += len(command.variants)
                elif isinstance(command, SampleMutation):
                    added += command.count
                elif command.type == "fork" or (command.type == "edit" and nodes[command.node_id].status != "draft"):
                    added += 1
        if len(nodes) + added > 300:
            raise ValueError("Proposal exceeds node limit")

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
