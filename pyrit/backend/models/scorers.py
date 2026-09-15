# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for backend scorer endpoints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pyrit.backend.models.attacks import ScoreView
from pyrit.models import JSONValue, Parameter, ScoreType

__all__ = [
    "CreateScorerRequest",
    "ScoreAttackRequest",
    "ScoreAttackResponse",
    "ScorerCatalogEntry",
    "ScorerCatalogResponse",
    "ScorerInstance",
    "ScorerListResponse",
]


class ScorerCatalogEntry(BaseModel):
    """A scorer type available from the backend registry."""

    scorer_type: str = Field(..., description="Scorer class name (e.g., 'SubStringScorer')")
    score_type: ScoreType = Field(..., description="Score family produced by this scorer")
    is_llm_based: bool = Field(False, description="Whether this scorer depends on an LLM judge target")
    parameters: list[Parameter] = Field(
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


class ScoreAttackResponse(BaseModel):
    """Response after scoring stored backend evidence."""

    scorer_id: str = Field(..., description="Registered scorer instance ID or alias")
    scorer_hash: str = Field(..., description="Canonical scorer identifier hash used for scoring")
    scores: list[ScoreView] = Field(default_factory=list, description="Persisted score results returned by the scorer")
    status: Literal["complete", "not_applicable"] = Field(
        ...,
        description="Whether the scorer produced at least one score for the requested evidence",
    )
