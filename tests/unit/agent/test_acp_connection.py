# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("acp")

from acp import RequestError
from acp.schema import (
    AgentCapabilities,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    PermissionOption,
    ToolCallUpdate,
)

from pyrit.agent.acp_connection import AcpConnection, RecordingTransport, _AcpClient
from pyrit.agent.approvals import AgentApprovals
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


@pytest.mark.parametrize("supported", [True, False])
async def test_explicit_native_load_checks_capability_and_disables_permission_actions_async(supported: bool) -> None:
    sdk = MagicMock()
    sdk.initialize = AsyncMock(
        return_value=InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(name="native", version="1"),
            agent_capabilities=AgentCapabilities(load_session=supported),
        )
    )
    sdk.load_session = AsyncMock(return_value=LoadSessionResponse())
    sdk.new_session = AsyncMock()
    transport = RecordingTransport(
        reader=asyncio.StreamReader(),
        writer=MagicMock(spec=asyncio.StreamWriter),
        record=AsyncMock(),
    )
    with patch("pyrit.agent.acp_connection.connect_to_agent", return_value=sdk):
        connection = AcpConnection(transport=transport, permission_policy=AgentPermissionPolicy.ALLOW_ONCE)
        if supported:
            await connection.initialize_async(cwd="/workspace", session_id="old-session", expected_agent_version="1")
            sdk.load_session.assert_awaited_once_with(cwd="/workspace", session_id="old-session", mcp_servers=[])
            assert connection.client.cancelled
            assert connection.session_id == "old-session"
        else:
            with pytest.raises(ValueError, match="loading"):
                await connection.initialize_async(cwd="/workspace", session_id="old-session")
            sdk.load_session.assert_not_awaited()
        sdk.new_session.assert_not_awaited()


async def test_native_auth_required_explains_private_home_without_retrying_async() -> None:
    sdk = MagicMock()
    sdk.initialize = AsyncMock(return_value=InitializeResponse(protocol_version=1))
    sdk.new_session = AsyncMock(side_effect=RequestError.auth_required())
    with patch("pyrit.agent.acp_connection.connect_to_agent", return_value=sdk):
        connection = AcpConnection(
            transport=RecordingTransport(
                reader=asyncio.StreamReader(), writer=MagicMock(spec=asyncio.StreamWriter), record=AsyncMock()
            ),
            permission_policy=AgentPermissionPolicy.DENY,
        )
        with pytest.raises(RequestError, match="COPILOT_GITHUB_TOKEN") as caught:
            await connection.initialize_async(cwd="/workspace", model="gpt-6-astra")
        assert caught.value.code == -32000
        sdk.new_session.assert_awaited_once()


async def test_ask_requires_a_one_time_refusal_before_requesting_operator_input() -> None:
    approvals = MagicMock(spec=AgentApprovals)
    approvals.request_async = AsyncMock()
    client = _AcpClient(AgentPermissionPolicy.ASK, approvals=approvals)
    with pytest.raises(RequestError, match="Invalid params") as caught:
        await client.request_permission(
            session_id="session",
            tool_call=ToolCallUpdate(tool_call_id="tool"),
            options=[PermissionOption(option_id="yes", name="Allow", kind="allow_once")],
        )
    assert caught.value.data == {"details": "Agent did not offer required permission option: reject_once"}
    approvals.request_async.assert_not_awaited()
