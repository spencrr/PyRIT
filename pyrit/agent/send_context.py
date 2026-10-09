# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Caller-owned interaction mode, scoped to a single conversation dispatch."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_interactive_conversation: ContextVar[str | None] = ContextVar("pyrit_interactive_agent_conversation", default=None)


@contextmanager
def interactive_agent_send(conversation_id: str) -> Generator[None, None, None]:
    """
    Mark a manual send without changing shared target configuration.

    Yields:
        None: The conversation-specific interaction scope.
    """
    token = _interactive_conversation.set(conversation_id)
    try:
        yield
    finally:
        _interactive_conversation.reset(token)


def is_interactive_agent_send(conversation_id: str) -> bool:
    """
    Identify a manually owned operation.

    Returns:
        bool: Whether the caller owns this conversation interactively.
    """
    return _interactive_conversation.get() == conversation_id
