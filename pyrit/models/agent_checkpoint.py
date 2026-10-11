# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Immutable application-state checkpoint descriptions, never process snapshots."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.models.agent_execution import AgentProfile


class HarnessCheckpointLayout(BaseModel):
    """A trusted adapter's explicitly versioned native-state contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str
    agent_versions: tuple[str, ...]
    state_root: str

    @field_validator("state_root")
    @classmethod
    def _validate_root(cls, value: str) -> str:
        from pathlib import PurePosixPath

        path = PurePosixPath(value)
        if (
            not value.startswith("/home/agent/")
            or str(path) != value
            or ".." in path.parts
            or "\\" in value
            or ":" in value
        ):
            raise ValueError("Checkpoint state must be a canonical subtree of the private agent home")
        return value


class CheckpointFile(BaseModel):
    """Integrity of one managed archive."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentCheckpoint(BaseModel):
    """A saved native session and workspace, bound to one exact transcript and allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_execution_id: UUID
    conversation_id: str
    target_id: str
    profile: AgentProfile
    image_id: str
    session_id: str
    layout: HarnessCheckpointLayout
    agent_version: str
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_cursor: int = Field(ge=0)
    journal: CheckpointFile
    workspace: CheckpointFile
    harness_state: CheckpointFile
    actor: str


class AgentCheckpointStatus(BaseModel):
    """Administrative readiness, separate from retained raw native-state bytes."""

    save_available: bool
    detail: str
    checkpoint: AgentCheckpoint | None = None
    restore_available: bool = False
    deletion_pending: bool = False
