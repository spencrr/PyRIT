# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
A duck-typed ``SupportsAgentRun`` with no model and no Agent Framework dependency.

The PyRIT ACP host loads it with ``--agent tests.unit.agent.fake_agent_framework:build`` so the
full ACP path (process, streams, tool calls, cancellation, errors) is exercised without a login.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FakeContent:
    type: str
    text: str | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: Any = None
    result: Any = None
    exception: str | None = None
    message: str | None = None


@dataclass
class FakeUpdate:
    contents: list[FakeContent] = field(default_factory=list)


@dataclass
class FakeSession:
    session_id: str = "fake-session"
    turns: int = 0


class FakeAgent:
    """Writes a receipt with its own 'tool', streams the evidence, and honors WAIT/ERROR prompts."""

    id = "fake-agent"
    name = "fake"
    description = "login-free fixture"

    def __init__(self, cwd: str) -> None:
        self.cwd = Path(cwd)

    def create_session(self, *, session_id: str | None = None) -> FakeSession:
        return FakeSession(session_id=session_id or "fake-session")

    def get_session(self, service_session_id: str, *, session_id: str | None = None) -> FakeSession:
        return FakeSession(session_id=session_id or service_session_id)

    def run(self, messages: Any = None, *, stream: bool = False, session: Any = None, **kwargs: Any) -> Any:
        if not stream:
            raise NotImplementedError("The fixture streams only")
        return self._stream(str(messages), session)

    async def _stream(self, prompt: str, session: FakeSession | None) -> AsyncIterator[FakeUpdate]:
        if session is not None:
            session.turns += 1
        if "ERROR" in prompt:
            yield FakeUpdate([FakeContent(type="error", message="synthetic agent failure")])
            return
        if "WAIT" in prompt:
            yield FakeUpdate([FakeContent(type="text", text="waiting")])
            await asyncio.sleep(3600)
            return
        total = 31 + 5 * ((session.turns - 1) if session else 0)
        yield FakeUpdate([FakeContent(type="text", text="Writing the receipt. ")])
        arguments = json.dumps({"path": "receipt.json", "content": json.dumps({"total": total})})
        half = len(arguments) // 2
        yield FakeUpdate(
            [FakeContent(type="function_call", call_id="call-1", name="write_file", arguments=arguments[:half])]
        )
        yield FakeUpdate([FakeContent(type="function_call", call_id="call-1", arguments=arguments[half:])])
        (self.cwd / "receipt.json").write_text(json.dumps({"total": total}), encoding="utf-8")
        yield FakeUpdate(
            [FakeContent(type="function_result", call_id="call-1", result=f"wrote receipt.json with total {total}")]
        )
        yield FakeUpdate([FakeContent(type="text", text=f"Receipt total: {total}")])


def build(cwd: str) -> FakeAgent:
    return FakeAgent(cwd)
