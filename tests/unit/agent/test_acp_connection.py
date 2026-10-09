# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("acp")

from acp.schema import PermissionOption, ToolCallUpdate

from pyrit.agent.acp_connection import RecordingTransport, _AcpClient
from pyrit.models.agent_execution import AgentPermissionPolicy


async def test_transport_records_before_write_and_preserves_extensions() -> None:
    reader = asyncio.StreamReader()
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    record = AsyncMock()
    transport = RecordingTransport(reader=reader, writer=writer, record=record)
    payload = {"jsonrpc": "2.0", "method": "_future_event", "params": {"value": 1}}
    reader.feed_data((json.dumps(payload) + "\n").encode())
    assert await transport.receive() == payload
    assert record.call_args.args == ("incoming", payload)
    record.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await transport.send(payload)
    writer.write.assert_not_called()


async def test_transport_rejects_client_tool_requests_without_executing_them() -> None:
    reader = asyncio.StreamReader()
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    record = AsyncMock()
    transport = RecordingTransport(reader=reader, writer=writer, record=record)
    reader.feed_data(b'{"jsonrpc":"2.0","id":7,"method":"terminal/create","params":{"command":"unexpected"}}\n')
    reader.feed_eof()
    assert await transport.receive() is None
    response = json.loads(writer.write.call_args.args[0])
    assert response["error"]["code"] == -32601
    assert record.await_count == 3
    assert record.call_args.args == ("lifecycle", {"type": "connection.closed"})


async def test_transport_rejects_truncated_frame() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"jsonrpc":"2.0"}')
    reader.feed_eof()
    transport = RecordingTransport(reader=reader, writer=MagicMock(spec=asyncio.StreamWriter), record=AsyncMock())
    with pytest.raises(ConnectionError, match="incomplete"):
        await transport.receive()


@pytest.mark.parametrize(
    "policy,expected", [(AgentPermissionPolicy.DENY, "no"), (AgentPermissionPolicy.ALLOW_ONCE, "yes")]
)
async def test_permission_policy_and_cancel(policy: AgentPermissionPolicy, expected: str) -> None:
    client = _AcpClient(policy)
    options = [
        PermissionOption(option_id="no", name="Reject", kind="reject_once"),
        PermissionOption(option_id="yes", name="Allow", kind="allow_once"),
    ]
    arguments = {"session_id": "session", "tool_call": ToolCallUpdate(tool_call_id="tool"), "options": options}
    response = await client.request_permission(**arguments)
    assert response.outcome.option_id == expected
    client.cancelled = True
    assert (await client.request_permission(**arguments)).outcome.outcome == "cancelled"
