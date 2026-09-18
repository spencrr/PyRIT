# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded wire contracts for the ephemeral, proposal-only tree assistant."""

import json
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from typing_extensions import Self

OpaqueId = Annotated[str, Field(min_length=1, max_length=256)]
Prompt = Annotated[str, Field(max_length=32_000)]
Preview = Annotated[str, Field(max_length=2_000)]
Revision = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]


class AssistantModel(BaseModel):
    """Strict base for browser and model-supplied data."""

    model_config = ConfigDict(extra="forbid", strict=True, serialize_by_alias=True)


class TreeConverterSpec(AssistantModel):
    """A proposed converter configuration, never constructed by this service."""

    type: OpaqueId
    params: dict[str, JsonValue] = Field(max_length=30)

    @model_validator(mode="after")
    def _bound_parameters(self) -> Self:
        if len(self.model_dump_json().encode()) > 16_000:
            raise ValueError("Converter parameters exceed 16000 bytes")
        _reject_credentials(self.params)
        return self


def _reject_credentials(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if any(
                word in normalized
                for word in ("apikey", "password", "secret", "credential", "authorization", "privatekey")
            ) or normalized in ("token", "accesstoken", "refreshtoken", "bearertoken"):
                raise ValueError("Credentials must not be included in assistant context or proposals")
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


ConverterList = Annotated[list[TreeConverterSpec], Field(max_length=10)]


class PromptVariant(AssistantModel):
    """One candidate prompt and converter stack."""

    prompt: Prompt
    converters: ConverterList


class AddMutation(AssistantModel):
    """Propose a child or root node."""

    type: Literal["add"]
    parent_id: OpaqueId | None = Field(alias="parentId")
    prompt: Prompt
    converters: ConverterList = Field(default_factory=list)


class EditMutation(PromptVariant):
    """Propose editing a draft or forking an existing node."""

    type: Literal["edit", "fork"]
    node_id: OpaqueId = Field(alias="nodeId")


class ChildVariantsMutation(AssistantModel):
    """Propose a bounded set of child variants."""

    type: Literal["childVariants"]
    node_id: OpaqueId = Field(alias="nodeId")
    variants: Annotated[list[PromptVariant], Field(min_length=1, max_length=20)]


class SampleMutation(AssistantModel):
    """Propose repeated samples."""

    type: Literal["sample"]
    node_id: OpaqueId = Field(alias="nodeId")
    count: Annotated[int, Field(ge=1, le=20)]


class RetryMutation(AssistantModel):
    """Propose resetting a node or subtree."""

    type: Literal["retry"]
    node_id: OpaqueId = Field(alias="nodeId")
    scope: Literal["node", "subtree"]


class PruneMutation(AssistantModel):
    """Propose changing pruning state."""

    type: Literal["prune"]
    node_id: OpaqueId = Field(alias="nodeId")
    pruned: bool


class KeepMutation(AssistantModel):
    """Propose keeping a node."""

    type: Literal["keep"]
    node_id: OpaqueId = Field(alias="nodeId")


TreeAssistantMutation = Annotated[
    AddMutation | EditMutation | ChildVariantsMutation | SampleMutation | RetryMutation | PruneMutation | KeepMutation,
    Field(discriminator="type"),
]


class MutateAction(AssistantModel):
    """An atomic reviewable batch; the browser remains the mutation engine."""

    kind: Literal["mutate"]
    commands: Annotated[list[TreeAssistantMutation], Field(min_length=1, max_length=20)]


class ExecuteAction(AssistantModel):
    """Explicit node selection for a human-approved run or score."""

    kind: Literal["run", "score"]
    node_ids: Annotated[list[OpaqueId], Field(min_length=1, max_length=300)]

    @field_validator("node_ids")
    @classmethod
    def _unique_nodes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Node IDs must be unique")
        return value


class ExistingNodeParent(AssistantModel):
    """An attachment to a node in the current workspace."""

    node_id: OpaqueId


class PlanStepParent(AssistantModel):
    """An attachment to a preceding step in the same plan."""

    step_id: OpaqueId


class TreeAssistantPlanStep(PromptVariant):
    """One new draft in an ordered, multilevel plan."""

    id: OpaqueId
    parent: ExistingNodeParent | PlanStepParent | None

    @field_validator("prompt")
    @classmethod
    def _nonempty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Plan prompts must not be empty")
        return value


class PlanAction(AssistantModel):
    """Create a tree of drafts and optionally request execution in the same approval."""

    kind: Literal["plan"]
    steps: Annotated[list[TreeAssistantPlanStep], Field(min_length=1, max_length=20)]
    run: bool

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        preceding: set[str] = set()
        for step in self.steps:
            if step.id in preceding:
                raise ValueError("Plan step IDs must be unique")
            if isinstance(step.parent, PlanStepParent) and step.parent.step_id not in preceding:
                raise ValueError("Plan parents must reference preceding steps")
            preceding.add(step.id)
        return self


TreeAssistantAction = Annotated[MutateAction | ExecuteAction | PlanAction, Field(discriminator="kind")]


class TreeAssistantReceipt(AssistantModel):
    """The human/browser's reported outcome, not a provider-generated assertion."""

    status: Literal["applied", "rejected", "failed"]
    revision: Revision
    detail: Preview


class TreeAssistantProposal(AssistantModel):
    """A staged proposal requiring explicit approval outside the assistant."""

    id: OpaqueId
    workspace_id: OpaqueId
    base_revision: Revision
    summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    action: TreeAssistantAction
    status: Literal["pending", "applied", "rejected", "failed"] = "pending"
    result: TreeAssistantReceipt | None = None


class TreeAssistantNode(AssistantModel):
    """A bounded browser snapshot, not authoritative backend evidence."""

    id: OpaqueId
    parent_id: OpaqueId | None
    attempt_id: OpaqueId
    prompt: Prompt
    converters: ConverterList
    status: Literal["draft", "running", "completed", "error"]
    pruned: bool
    kept: bool
    response_preview: Preview
    response_truncated: bool
    score_summary: Preview
    error: Preview | None = None
    attack_result_id: OpaqueId | None = None
    conversation_id: OpaqueId | None = None
    last_sequence: Annotated[int, Field(ge=0, le=1_000_000)] | None = None


class TreeAssistantSettings(AssistantModel):
    """Read-only budget and scoring context."""

    traversal: Literal["breadth-first", "depth-first"]
    concurrency: Literal[1, 2, 4]
    operation_budget: Annotated[int, Field(ge=1, le=100_000)]
    scorer_ids: Annotated[list[OpaqueId], Field(max_length=30)]


class TreeAssistantAutonomy(AssistantModel):
    """Application-reported planning bounds, never backend execution authority."""

    root_node_id: OpaqueId
    remaining_operations: Annotated[int, Field(ge=0, le=100_000)]
    remaining_turns: Annotated[int, Field(ge=0, le=50)]
    goal: Prompt


class TreeAssistantContext(AssistantModel):
    """The complete, bounded tree snapshot for one turn."""

    MAX_BYTES: ClassVar[int] = 512_000

    workspace_id: OpaqueId
    revision: Revision
    name: Annotated[str, Field(max_length=1_000)]
    objective: Prompt
    target_registry_name: OpaqueId
    target_identifier_hash: OpaqueId
    selected_node_id: OpaqueId | None
    nodes: Annotated[list[TreeAssistantNode], Field(max_length=300)]
    settings: TreeAssistantSettings
    autonomy: TreeAssistantAutonomy | None = None

    @model_validator(mode="after")
    def _validate_tree(self) -> Self:
        if len(self.model_dump_json().encode()) > self.MAX_BYTES:
            raise ValueError("Assistant context exceeds 512000 bytes; use a smaller workspace")
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Duplicate node IDs")
        if self.selected_node_id is not None and self.selected_node_id not in nodes:
            raise ValueError("Unknown selected node")
        if self.autonomy is not None and self.autonomy.root_node_id not in nodes:
            raise ValueError("Unknown autonomy root node")
        for node in self.nodes:
            visited = {node.id}
            parent = node.parent_id
            while parent is not None:
                if parent not in nodes or parent in visited:
                    raise ValueError("Unknown parent node or cycle")
                visited.add(parent)
                parent = nodes[parent].parent_id
        return self


class TreeAssistantMessage(AssistantModel):
    """An idempotent user message and its current workspace."""

    request_id: OpaqueId
    message: Annotated[str, Field(min_length=1, max_length=32_000)]
    context: TreeAssistantContext

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        UUID(value)
        return value


class TreeAssistantToolCall(AssistantModel):
    """A bounded display trace of an actual SDK invocation, not a replayable call."""

    id: OpaqueId
    name: OpaqueId
    arguments: dict[str, JsonValue]
    result: Annotated[str, Field(max_length=24_000)]
    status: Literal["completed", "error"]
    duration_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    truncated: bool

    @field_validator("arguments")
    @classmethod
    def _redact_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return cls.redact_arguments(value)

    @staticmethod
    def redact_arguments(arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """
        Remove credential-like argument values, including invalid calls and imported traces.

        Returns:
            dict[str, JsonValue]: A copy with credential-like values replaced by redaction markers.
        """
        # The SDK uses this root-only shape when arguments are not a JSON object.
        # No assistant tool accepts a raw parameter; never expose that unparsed payload.
        if set(arguments) == {"raw"}:
            return {"raw": "[unparseable arguments omitted]"}

        def redact(value: JsonValue) -> JsonValue:
            if isinstance(value, dict):
                return {
                    key: "[redacted]"
                    if any(
                        word in key.lower().replace("_", "").replace("-", "")
                        for word in (
                            "key",
                            "token",
                            "secret",
                            "password",
                            "credential",
                            "authorization",
                            "headers",
                            "cookie",
                        )
                    )
                    else redact(child)
                    for key, child in value.items()
                }
            return [redact(child) for child in value] if isinstance(value, list) else value

        redacted = redact(arguments)
        assert isinstance(redacted, dict)
        return redacted

    @model_validator(mode="after")
    def _bound_trace(self) -> Self:
        if self.status == "error":
            self.result = "Tool invocation failed."
        if len(json.dumps(self.arguments, ensure_ascii=False).encode()) > 16_000:
            raise ValueError("Tool arguments exceed 16000 bytes")
        if len(self.result.encode()) > 24_000:
            raise ValueError("Tool result exceeds 24000 bytes")
        return self


class TreeAssistantTurnContext(AssistantModel):
    """Public request configuration without credentials or provider-private state."""

    workspace_id: OpaqueId
    revision: Revision
    selected_node_id: OpaqueId | None
    node_count: Annotated[int, Field(ge=0, le=300)]
    model: OpaqueId
    api: OpaqueId
    instructions: Prompt
    tools: Annotated[list[OpaqueId], Field(max_length=16)]
    restored: bool
    restoration_notice: Preview | None = None


class TreeAssistantUsage(AssistantModel):
    """Provider-reported token counts, excluding private reasoning."""

    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None


class TreeAssistantTurn(AssistantModel):
    """One completed assistant turn and at most one staged proposal."""

    request_id: OpaqueId
    message: Prompt
    reply: Annotated[str, Field(max_length=32_000)]
    proposals: Annotated[list[TreeAssistantProposal], Field(max_length=1)]
    tool_calls: Annotated[list[TreeAssistantToolCall], Field(max_length=16)] = Field(default_factory=list)
    context_summary: TreeAssistantTurnContext | None = None
    usage: TreeAssistantUsage | None = None

    @model_validator(mode="after")
    def _bound_traces(self) -> Self:
        if len(json.dumps([call.model_dump() for call in self.tool_calls], ensure_ascii=False).encode()) > 128_000:
            raise ValueError("Turn tool traces exceed 128000 bytes")
        return self


class CreateTreeAssistantSession(AssistantModel):
    """Create a fresh session, optionally importing a portable, untrusted transcript."""

    MAX_BYTES: ClassVar[int] = 2_000_000

    workspace_id: OpaqueId
    history: Annotated[list[TreeAssistantTurn], Field(max_length=50)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_history(self) -> Self:
        if len(self.model_dump_json().encode()) > self.MAX_BYTES:
            raise ValueError("Restored session exceeds 2000000 bytes")
        request_ids: set[str] = set()
        proposal_ids: set[str] = set()
        for turn in self.history:
            if turn.request_id in request_ids:
                raise ValueError("Restored request IDs must be unique")
            request_ids.add(turn.request_id)
            if turn.context_summary and turn.context_summary.workspace_id != self.workspace_id:
                raise ValueError("Restored context belongs to another workspace")
            for proposal in turn.proposals:
                if proposal.workspace_id != self.workspace_id or proposal.id in proposal_ids:
                    raise ValueError("Restored proposals must be unique and belong to this workspace")
                if proposal.status != "pending" and proposal.result and proposal.result.status != proposal.status:
                    raise ValueError("Restored proposal status and receipt disagree")
                proposal_ids.add(proposal.id)
        return self


class TreeAssistantSession(AssistantModel):
    """The latest 50 completed turns, including restored history, held in this process only."""

    session_id: OpaqueId
    workspace_id: OpaqueId
    model: OpaqueId
    turns: Annotated[list[TreeAssistantTurn], Field(max_length=50)] = Field(default_factory=list)
