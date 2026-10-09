# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ACP SDK integration with mandatory, ordered capture at the transport boundary."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Never

from acp import Client, RequestError, connect_to_agent
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallUpdate,
)

from pyrit.models.agent_execution import AgentConnectionState, AgentPermissionPolicy

logger = logging.getLogger(__name__)


class RecordingTransport:
    """Bounded ACP NDJSON transport; capture failure fails the connection, not just a listener."""

    def __init__(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        record: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Bind existing byte streams; the environment owns the process."""
        self.reader = reader
        self.writer = writer
        self.record = record
        self.text: list[str] = []
        self._write_lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:  # pyrit-async-suffix-exempt
        """Record intent before writing a protocol message."""
        async with self._write_lock:
            await self.record("outgoing", message)
            self.writer.write((json.dumps(message, ensure_ascii=True) + "\n").encode("utf-8"))
            await self.writer.drain()

    async def receive(self) -> dict[str, Any] | None:  # pyrit-async-suffix-exempt
        """
        Retain every valid frame, including unsupported extensions.

        Returns:
            dict[str, Any] | None: The next message, or EOF.

        Raises:
            ConnectionError: A frame is incomplete.
            ValueError: A frame is not JSON-RPC.
        """
        while True:
            line = await self.reader.readline()
            if not line:
                await self.record("lifecycle", {"type": "connection.closed"})
                return None
            if not line.endswith(b"\n"):
                raise ConnectionError("ACP closed with an incomplete frame")
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("Agent emitted an invalid ACP JSON-RPC frame")
            await self.record("incoming", message)
            method = message.get("method")
            if method is not None and "id" in message and method != "session/request_permission":
                await self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "PyRIT does not provide client tools or elicitation"},
                    }
                )
                continue
            if method == "session/update":
                update = message.get("params", {}).get("update", {})
                content = update.get("content", {})
                if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                    self.text.append(content["text"])
            return message

    async def close(self) -> None:  # pyrit-async-suffix-exempt
        """Close protocol input without claiming that the agent process has stopped."""
        self.writer.close()


class _AcpClient(Client):
    def __init__(self, policy: AgentPermissionPolicy) -> None:
        self.policy = policy
        self.cancelled = False

    async def request_permission(  # pyrit-async-suffix-exempt
        self, session_id: str, tool_call: ToolCallUpdate, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        if self.cancelled:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        kind = "allow_once" if self.policy == AgentPermissionPolicy.ALLOW_ONCE else "reject_once"
        selected = next((option for option in options if option.kind == kind), None)
        if selected is None:
            raise RequestError.invalid_params({"details": f"Agent did not offer required permission option: {kind}"})
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=selected.option_id))

    async def session_update(  # pyrit-async-suffix-exempt
        self, session_id: str, update: Any, **kwargs: Any
    ) -> None:
        # The transport captures before dispatch; SDK notification scheduling cannot reorder evidence.
        pass

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:  # pyrit-async-suffix-exempt
        logger.info("Retained unsupported ACP notification: %s", method)

    async def _unsupported_async(self, *args: Any, **kwargs: Any) -> Never:
        raise RequestError.method_not_found("PyRIT does not provide client tools or elicitation")

    write_text_file = _unsupported_async
    read_text_file = _unsupported_async
    create_terminal = _unsupported_async
    terminal_output = _unsupported_async
    release_terminal = _unsupported_async
    wait_for_terminal_exit = _unsupported_async
    kill_terminal = _unsupported_async
    create_elicitation = _unsupported_async
    complete_elicitation = _unsupported_async
    ext_method = _unsupported_async

    def on_connect(self, conn: Any) -> None:
        pass


class AcpConnection:
    """Translate prompts and cancellation using the official ACP client."""

    def __init__(
        self,
        *,
        transport: RecordingTransport,
        permission_policy: AgentPermissionPolicy,
        status_changed: Callable[[AgentConnectionState], Awaitable[None]] | None = None,
    ) -> None:
        """Start the SDK reader after the caller has installed the recorder."""
        self.transport = transport
        self.client = _AcpClient(permission_policy)
        self.connection = connect_to_agent(self.client, transport)
        self.session_id: str | None = None
        self.status_changed = status_changed

    async def initialize_async(
        self, *, cwd: str, model: str = "", authentication_method: str | None = None
    ) -> dict[str, Any]:
        """
        Negotiate ACP v1 and open a new, never replayed session.

        Returns:
            dict[str, Any]: Negotiated agent information and capabilities.

        Raises:
            ValueError: The selected protocol version is unsupported.
        """
        result = await self.connection.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="pyrit", version="1"),
        )
        if result.protocol_version != 1:
            raise ValueError(f"Agent selected unsupported ACP version {result.protocol_version}")
        if authentication_method:
            if self.status_changed:
                await self.status_changed(AgentConnectionState.AUTHENTICATING)
            if not any(method.id == authentication_method for method in result.auth_methods or []):
                raise ValueError("Configured ACP authentication method was not advertised by the agent")
            await self.connection.authenticate(method_id=authentication_method)
        session = await self.connection.new_session(cwd=cwd, mcp_servers=[])
        self.session_id = session.session_id
        if model:
            option = next((option for option in session.config_options or [] if option.category == "model"), None)
            if option is None:
                raise ValueError("Agent does not advertise model configuration; set it in the launch command instead")
            await self.connection.set_config_option(config_id=option.id, session_id=self.session_id, value=model)
        if self.status_changed:
            await self.status_changed(AgentConnectionState.READY)
        return {
            "initialize": result.model_dump(mode="json", by_alias=True),
            "session": session.model_dump(mode="json", by_alias=True),
        }

    async def prompt_async(self, prompt: str) -> tuple[str, str]:
        """
        Wait for the prompt response, not merely a text chunk or a tool completion.

        Returns:
            tuple[str, str]: Stop reason and exposed agent text.

        Raises:
            RuntimeError: No session is initialized.
        """
        if self.session_id is None:
            raise RuntimeError("ACP session is not initialized")
        self.transport.text.clear()
        self.client.cancelled = False
        result = await self.connection.prompt(
            session_id=self.session_id, prompt=[TextContentBlock(type="text", text=prompt)]
        )
        return result.stop_reason, "".join(self.transport.text)

    async def cancel_async(self) -> None:
        """Request cancellation while keeping the reader available to drain final events."""
        self.client.cancelled = True
        if self.session_id is not None:
            await self.connection.cancel(session_id=self.session_id)

    async def close_async(self) -> None:
        """Close the SDK connection; the environment separately stops owned processes."""
        await self.connection.close()
