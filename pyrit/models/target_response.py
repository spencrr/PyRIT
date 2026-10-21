# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit outcomes for targets whose completed operations need not produce chat text."""

from enum import Enum
from typing import ClassVar

from pydantic import JsonValue

from pyrit.models.messages.message import Message


class TargetOutcome(str, Enum):
    """
    Terminal outcome of a target operation, independent of the messages it produced.

    Distinct from the conversation API's ``target_response_status``, which reports the
    error category of the latest stored response.
    """

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class TargetResponse(list[Message]):
    """A list-compatible target response carrying an explicit terminal outcome."""

    OUTCOME_METADATA_KEY: ClassVar[str] = "target_outcome"

    def __init__(
        self,
        *,
        messages: list[Message] | None = None,
        outcome: TargetOutcome,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Keep legacy message iteration/indexing while exposing the operation outcome."""
        super().__init__(messages or [])
        self.outcome = outcome
        self.metadata = dict(metadata or {})

    @property
    def messages(self) -> list[Message]:
        """The response's message projection."""
        return self
