# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("acp")

from acp import RequestError

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.agent.hosts.agent_framework_acp import AgentFrameworkAcpHost, _Session, load_factory
from pyrit.agent.tool_calls import TOOL_NAME_META_KEY, project_agent_tools
from pyrit.models.agent_execution import (
    AgentExecutionEvent,
    AgentExecutionState,
    AgentProfile,
    AgentTargetConfiguration,
    AgentTurnStatus,
)
from pyrit.models.messages.tool_content import FunctionCallContent
from tests.unit.agent import fake_agent_framework
from tests.unit.agent.fake_agent_framework import FakeContent

pytestmark = pytest.mark.usefixtures("patch_central_database")

FIXTURE_FACTORY = "tests.unit.agent.fake_agent_framework:build"


@pytest.fixture
def fixture_directory(tmp_path: Path) -> Path:
    """The fake agent travels inside the workspace; ``python -m`` puts the host's cwd on sys.path."""
    directory = tmp_path / "fixture"
    directory.mkdir()
    shutil.copy(fake_agent_framework.__file__, directory / "fake_agent_framework.py")
    return directory


def host_profile(fixture_directory: Path, **overrides: object) -> AgentProfile:
    return AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        harness_kind="agent_framework",
        command=(
            sys.executable,
            "-m",
            "pyrit.agent.hosts.agent_framework_acp",
            "--agent",
            "fake_agent_framework:build",
        ),
        permission_policy="allow_once",
        fixture_directory=str(fixture_directory),
        artifact_paths=("receipt.json",),
        **overrides,
    )


async def test_host_serves_a_fake_agent_over_real_acp_with_named_tool_calls(
    tmp_path: Path, fixture_directory: Path
) -> None:
    async with AgentExecutionManager(root=tmp_path / "executions") as manager:
        record, turn = await manager.send_async(
            configuration=AgentTargetConfiguration.from_profile(host_profile(fixture_directory)),
            target_id="target",
            conversation_id="one",
            request_id="1",
            prompt="Create the receipt",
            has_history=False,
        )
        assert turn.status == AgentTurnStatus.COMPLETED and turn.stop_reason == "end_turn"
        assert turn.response_text == "Writing the receipt. Receipt total: 31"
        assert record.agent_info["initialize"]["agentInfo"]["name"] == "pyrit-agent-framework-host"
        projection = await manager.tool_calls_async(execution_id=record.id, turn_id=turn.id)
        assert not projection.omitted
        call = FunctionCallContent.model_validate_json(projection.messages[0].get_piece().original_value)
        assert call.name == "write_file"
        assert json.loads(call.arguments)["path"] == "receipt.json"
        second, turn_two = await manager.send_async(
            configuration=AgentTargetConfiguration.from_profile(host_profile(fixture_directory)),
            target_id="target",
            conversation_id="one",
            request_id="2",
            prompt="Update the receipt",
            has_history=True,
        )
        assert second.id == record.id and turn_two.response_text.endswith("36")
        await manager.close_execution_async(execution_id=record.id)
        assert record.state == AgentExecutionState.CLOSED and not record.cleanup_error
        receipt = (manager.store.directory(record.id) / "artifacts" / "receipt.json").read_text()
        assert json.loads(receipt) == {"total": 36}


async def test_host_reports_cancellation_and_agent_errors_explicitly(tmp_path: Path, fixture_directory: Path) -> None:
    async with AgentExecutionManager(root=tmp_path / "executions") as manager:
        send = asyncio.create_task(
            manager.send_async(
                configuration=AgentTargetConfiguration.from_profile(host_profile(fixture_directory)),
                target_id="target",
                conversation_id="wait",
                request_id="1",
                prompt="WAIT for cancellation",
                has_history=False,
            )
        )
        async with asyncio.timeout(30):
            while not manager.records or next(iter(manager.records.values())).state != AgentExecutionState.WORKING:
                await asyncio.sleep(0.02)
            record = next(iter(manager.records.values()))
            while not any(
                event.direction == "incoming" and event.payload.get("method") == "session/update"
                for event in (await manager.store.events_async(execution_id=record.id)).events
            ):
                await asyncio.sleep(0.02)
            await manager.cancel_async(record.id)
            _, turn = await send
        assert turn.status == AgentTurnStatus.CANCELLED
        # An agent failure is a protocol-level error: the execution is closed, never reported as a reply.
        with pytest.raises(RequestError, match="Internal error"):
            await manager.send_async(
                configuration=AgentTargetConfiguration.from_profile(host_profile(fixture_directory)),
                target_id="target",
                conversation_id="wait",
                request_id="2",
                prompt="Now ERROR out",
                has_history=True,
            )
        failed = record.turns[-1]
        assert failed.status == AgentTurnStatus.UNKNOWN
        assert failed.error and "synthetic agent failure" in failed.error
        assert record.state == AgentExecutionState.CLOSED and record.close_reason == "operation_failed"


def test_factory_references_are_validated() -> None:
    assert load_factory(None).__name__ == "build_default_agent"
    assert load_factory(FIXTURE_FACTORY).__name__ == "build"
    with pytest.raises(ValueError, match="module:function"):
        load_factory("not-a-reference")
    with pytest.raises(ValueError, match="not callable"):
        load_factory("tests.unit.agent.fake_agent_framework:FIXTURE_CONSTANT_MISSING")


@pytest.mark.parametrize(
    ("tool_name", "exception"),
    [("write_file", None), ("nonexistent_tool", "Requested function not found"), ("write_file", "Invalid arguments")],
)
async def test_streamed_argument_fragments_are_reported_complete_on_the_final_update(
    tool_name: str, exception: str | None
) -> None:
    """Real models stream `name` with empty arguments first; the start must not freeze `{}` as the input."""
    updates: list[tuple[str, Any]] = []

    class RecordingClient:
        async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
            updates.append((session_id, update.model_dump(by_alias=True, exclude_none=True)))

    host = AgentFrameworkAcpHost(factory=lambda cwd: None)  # type: ignore[arg-type, return-value]
    host.on_connect(RecordingClient())  # type: ignore[arg-type]
    session = _Session(agent=None, session=None, cwd=".")  # type: ignore[arg-type]
    arguments = json.dumps({"path": "receipt.json", "content": '{"total": 31}'})
    stream = [
        FakeContent(type="function_call", call_id="c1", name=tool_name, arguments=""),
        FakeContent(type="function_call", call_id="c1", arguments=arguments[:7]),
        FakeContent(type="function_call", call_id="c1", arguments=arguments[7:]),
        FakeContent(type="function_result", call_id="c1", result=exception or "ok", exception=exception),
    ]
    for content in stream:
        await host._emit_content_async("s", session, content)
    start, final = (update for _, update in updates)
    assert start["sessionUpdate"] == "tool_call" and "rawInput" not in start
    assert start["_meta"] == {TOOL_NAME_META_KEY: tool_name} and start["status"] == "pending"
    assert final["sessionUpdate"] == "tool_call_update"
    assert final["status"] == ("failed" if exception else "completed")
    assert final["rawInput"] == json.loads(arguments) and final["rawOutput"] == (exception or "ok")
    execution_id, turn_id = uuid4(), uuid4()
    projection = project_agent_tools(
        events=[
            AgentExecutionEvent(
                execution_id=execution_id,
                turn_id=turn_id,
                sequence=index + 1,
                direction="incoming",
                payload={"method": "session/update", "params": {"update": update}},
            )
            for index, (_, update) in enumerate(updates)
        ],
        execution_id=execution_id,
        turn_id=turn_id,
        conversation_id="one",
    )
    if exception:
        assert projection.omitted == {"c1": "invocation_or_result_not_established"}
        assert len(projection.messages) == 1
    else:
        assert not projection.omitted
        assert len(projection.messages) == 2
    call = FunctionCallContent.model_validate_json(projection.messages[0].get_piece().original_value)
    assert call.name == tool_name and json.loads(call.arguments) == json.loads(arguments)
    updates.clear()
    await host._emit_content_async(
        "s", session, FakeContent(type="function_call", call_id="c2", name="list_directory", arguments="{bad")
    )
    await host._emit_content_async("s", session, FakeContent(type="function_result", call_id="c2", result=""))
    assert "rawInput" not in updates[-1][1]


def test_tool_kind_and_argument_parsing_are_conservative() -> None:
    assert AgentFrameworkAcpHost._tool_kind("read_file") == "read"
    assert AgentFrameworkAcpHost._tool_kind("run_command") == "execute"
    assert AgentFrameworkAcpHost._tool_kind("mystery") == "other"
    assert AgentFrameworkAcpHost._parse_arguments("") == {}
    assert AgentFrameworkAcpHost._parse_arguments('{"a": 1}') == {"a": 1}
    assert AgentFrameworkAcpHost._parse_arguments('{"a": ') is None
    assert AgentFrameworkAcpHost._parse_arguments("[1, 2]") is None
