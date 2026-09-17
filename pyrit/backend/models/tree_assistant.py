# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded wire contracts for the ephemeral, proposal-only tree assistant."""

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


TreeAssistantAction = Annotated[MutateAction | ExecuteAction, Field(discriminator="kind")]


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

    @model_validator(mode="after")
    def _validate_tree(self) -> Self:
        if len(self.model_dump_json().encode()) > self.MAX_BYTES:
            raise ValueError("Assistant context exceeds 512000 bytes; use a smaller workspace")
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Duplicate node IDs")
        if self.selected_node_id is not None and self.selected_node_id not in nodes:
            raise ValueError("Unknown selected node")
        for node in self.nodes:
            visited = {node.id}
            parent = node.parent_id
            while parent is not None:
                if parent not in nodes or parent in visited:
                    raise ValueError("Unknown parent node or cycle")
                visited.add(parent)
                parent = nodes[parent].parent_id
        return self


class CreateTreeAssistantSession(AssistantModel):
    """Create an ephemeral chat tied to one workspace."""

    workspace_id: OpaqueId


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


class TreeAssistantTurn(AssistantModel):
    """One completed assistant turn and at most one staged proposal."""

    request_id: OpaqueId
    message: Prompt
    reply: Annotated[str, Field(max_length=32_000)]
    proposals: Annotated[list[TreeAssistantProposal], Field(max_length=1)]


class TreeAssistantSession(AssistantModel):
    """Recoverable completed turns, held in this process only."""

    session_id: OpaqueId
    workspace_id: OpaqueId
    model: OpaqueId
    turns: Annotated[list[TreeAssistantTurn], Field(max_length=50)] = Field(default_factory=list)
