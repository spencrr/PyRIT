# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for backend scorer endpoints."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.backend.models.attacks import ScoreView
from pyrit.models import JSONValue, Parameter, PromptDataType, ScoreType

__all__ = [
    "CreateScorerRequest",
    "ExpectedResponsePiece",
    "ParameterPreset",
    "ScoreAttackRequest",
    "ScoreAttackResponse",
    "ScorerCatalogEntry",
    "ScorerCatalogResponse",
    "ScorerInstance",
    "ScorerListResponse",
    "ScorerParameter",
    "ScorerValidationResponse",
]


class ParameterPreset(BaseModel):
    """A vetted preset value the frontend can apply to a scorer parameter."""

    name: str = Field(..., min_length=1, max_length=200)
    value: JSONValue


class ScorerParameter(Parameter):
    """Parameter descriptor including supported structured JSON inputs."""

    input_kind: Literal["field", "multiline", "json", "unsupported"] = "field"
    json_schema: dict[str, Any] | None = None
    example: str | None = None
    reference_kind: Literal["scorer", "target"] | None = None
    accepts_inline: bool = False
    accepted_types: list[str] = Field(default_factory=list)
    supports_yaml: bool = False
    accepts_text: bool = False
    presets: list[ParameterPreset] | None = None


class ScorerCatalogEntry(BaseModel):
    """A scorer type available from the backend registry."""

    scorer_type: str = Field(..., description="Scorer class name (e.g., 'SubStringScorer')")
    score_type: ScoreType = Field(..., description="Score family produced by this scorer")
    is_llm_based: bool = Field(False, description="Whether this scorer depends on an LLM judge target")
    parameters: list[ScorerParameter] = Field(
        default_factory=list,
        description="Constructor parameters suitable for dynamic form generation",
    )
    description: str | None = Field(None, description="Short description of the scorer from its docstring")


class ScorerCatalogResponse(BaseModel):
    """Response for listing available scorer types from the registry."""

    items: list[ScorerCatalogEntry] = Field(..., description="List of available scorer types")


class ScorerInstance(BaseModel):
    """A registered scorer instance."""

    scorer_id: str = Field(..., description="Registered scorer instance ID or alias")
    scorer_type: str = Field(..., description="Scorer class name")
    identifier_hash: str = Field(..., description="Canonical scorer identifier hash")
    score_type: ScoreType = Field(..., description="Score family produced by this scorer")


class ScorerListResponse(BaseModel):
    """Response for listing registered scorer instances."""

    items: list[ScorerInstance] = Field(..., description="List of registered scorer instances")


class CreateScorerRequest(BaseModel):
    """Request to create a scorer instance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: str = Field(..., min_length=1, max_length=200, description="Scorer type (e.g., 'SubStringScorer')")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Scorer constructor parameters")


class ScorerValidationResponse(BaseModel):
    """Result of preflighting a scorer configuration without registration."""

    valid: Literal[True] = True


class ExpectedResponsePiece(BaseModel):
    """Exact stored response content the score request expects to anchor against."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    converted_value: str
    converted_value_data_type: PromptDataType


class ScoreAttackRequest(BaseModel):
    """Request to score one stored attack conversation or response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    attack_result_id: str = Field(..., min_length=1, max_length=255, description="Attack result ID to score")
    conversation_id: str = Field(..., min_length=1, max_length=255, description="Conversation ID to score")
    expected_scorer_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[A-Fa-f0-9]{64}$",
        description="Expected scorer identifier hash used for stale-instance protection",
    )
    objective: str | None = Field(
        None,
        max_length=4000,
        description="Optional objective to pass through ScoringExpectation.objective",
    )
    scope: Literal["response", "conversation"] = Field(
        "response",
        description="Whether to score the latest assistant response or each stored message in the conversation",
    )
    evidence_message_piece_ids: list[uuid.UUID] | None = Field(
        None,
        min_length=1,
        description=(
            "Optional exact stored message-piece IDs anchoring scoring to one persisted message. "
            "Must be paired with evidence_sequence."
        ),
    )
    evidence_sequence: int | None = Field(
        None,
        ge=0,
        description=(
            "Optional persisted conversation sequence to anchor scoring against. "
            "Must be paired with evidence_message_piece_ids."
        ),
    )
    expected_response: list[ExpectedResponsePiece] | None = Field(
        None,
        min_length=1,
        description=(
            "Optional exact stored converted content expected at the anchored response. "
            "Used to reject stale or modified evidence before scoring."
        ),
    )

    @model_validator(mode="after")
    def _validate_evidence_anchor(self) -> "ScoreAttackRequest":
        """
        Require anchor fields to be supplied together.

        Returns:
            ScoreAttackRequest: ``self`` when the paired anchor fields are valid.
        """
        has_piece_ids = self.evidence_message_piece_ids is not None
        has_sequence = self.evidence_sequence is not None
        if has_piece_ids != has_sequence:
            raise ValueError("evidence_message_piece_ids and evidence_sequence must be provided together.")
        if self.expected_response is not None and not has_piece_ids:
            raise ValueError("expected_response requires evidence_message_piece_ids and evidence_sequence.")
        return self


class ScoreAttackResponse(BaseModel):
    """Response after scoring stored backend evidence."""

    scorer_id: str = Field(..., description="Registered scorer instance ID or alias")
    scorer_hash: str = Field(..., description="Canonical scorer identifier hash used for scoring")
    scores: list[ScoreView] = Field(default_factory=list, description="Persisted score results returned by the scorer")
    status: Literal["complete", "not_applicable"] = Field(
        ...,
        description="Whether the scorer produced at least one score for the requested evidence",
    )
