# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Journal provenance for canonical tool messages projected from an external harness."""

from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentToolEvidence(BaseModel):
    """An observed tool report, not synthetic history or complete internal execution coverage."""

    METADATA_KEY: ClassVar[str] = "agent_tool_evidence"
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal["acp"] = "acp"
    execution_id: UUID
    turn_id: UUID
    source_call_id: str = Field(min_length=1)
    event_sequence: int = Field(ge=1)

    def to_metadata(self) -> dict[str, Any]:
        """
        Return the message-owned provenance.

        Returns:
            dict[str, Any]: The canonical metadata fragment.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "AgentToolEvidence | None":
        """
        Read retained provenance without inferring it from message content.

        Returns:
            AgentToolEvidence | None: Validated provenance, when present.
        """
        value = metadata.get(cls.METADATA_KEY)
        return cls.model_validate(value) if value is not None else None
