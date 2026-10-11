# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Portable execution evidence, without conflating replay and re-execution."""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from pyrit.models.agent_execution import AgentExecution, AgentExecutionEvent
from pyrit.models.messages.message import Message
from pyrit.models.score import Score


class EvidenceReadiness(str, Enum):
    """Availability of a specifically scoped operation, not global reproducibility."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class EvidenceOperation(BaseModel):
    """A capability assessment with its scope and limitation."""

    status: EvidenceReadiness
    detail: str


class AgentReplayAssessment(BaseModel):
    """Replay is read-only; recreation and resume require independent prerequisites."""

    playback: EvidenceOperation
    rescore: EvidenceOperation
    recreate: EvidenceOperation
    resume: EvidenceOperation


class EvidenceFile(BaseModel):
    """Digest of a file in an execution package, measured over its exact stored bytes."""

    path: str
    bytes: int = Field(ge=0)
    sha256: str


class AgentEvidenceSnapshot(BaseModel):
    """One closed execution's journal cutoff and separately acquired memory snapshot."""

    format_version: Literal[1] = 1
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    execution: AgentExecution
    event_cursor: int = Field(ge=0)
    events: list[AgentExecutionEvent] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    scores: list[Score] = Field(default_factory=list)
    assessment: AgentReplayAssessment
    omissions: list[str] = Field(default_factory=list)


class AgentEvidenceManifest(BaseModel):
    """Contents, integrity hashes, and explicit scope of a downloaded package."""

    format_version: Literal[1] = 1
    execution_id: UUID
    conversation_id: str
    target_id: str
    captured_at: datetime
    event_cursor: int
    source_coverage: str
    assessment: AgentReplayAssessment
    files: list[EvidenceFile]
    omissions: list[str]
    notes: list[str] = Field(
        default_factory=lambda: [
            "Playback never runs tools. This archive is not an executable checkpoint.",
            (
                "Messages and scores are the current memory snapshot at export, "
                "not an atomic historical database snapshot."
            ),
            "Hashes detect changed package bytes; they do not attest that harness reports are true or complete.",
            "Content can be sensitive. Protect this package like the original test data.",
        ]
    )
