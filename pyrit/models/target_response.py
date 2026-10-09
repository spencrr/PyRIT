# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit outcomes for targets whose completed operations need not produce chat text."""

from enum import Enum

from pydantic import JsonValue

from pyrit.models.messages.message import Message


class TargetResponseStatus(str, Enum):
    """Operation outcome independent of the messages an operation produced."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class TargetResponse(list[Message]):
    """A list-compatible target response carrying an explicit terminal outcome."""

    def __init__(
        self,
        *,
        messages: list[Message] | None = None,
        status: TargetResponseStatus,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Keep legacy message iteration/indexing while exposing the operation outcome."""
        super().__init__(messages or [])
        self.status = status
        self.metadata = dict(metadata or {})

    @property
    def messages(self) -> list[Message]:
        """The response's message projection."""
        return self
