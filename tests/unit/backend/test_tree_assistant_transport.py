# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise actual OpenAI wire formats and SDK tool dispatch without remote model calls."""

import copy
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

pytest.importorskip("agent_framework")

from openai import AsyncOpenAI

from pyrit.backend.models.tree_assistant import TreeAssistantContext
from pyrit.backend.services.tree_assistant_runtime import TreeAssistantError, create_agent_framework_runtime

from .test_tree_assistant import context_dict


@pytest.mark.parametrize("api", [None, "chat_completions", "responses"])
@pytest.mark.parametrize("auto_run", [False, True])
async def test_transport_tool_loop_and_local_history_async(*, api: str | None, auto_run: bool) -> None:
    """Both transports invoke tools, stage proposals and carry local history with storage disabled."""
    requests: list[dict[str, Any]] = []
    paths: list[str] = []
    replies = [
        ("inspect_tree", {}),
        ("inspect_selected_node", {}),
        ("inspect_subtree", {"root_node_id": "n1", "detail": "nodes"}),
        (
            "propose_action",
            {
                "summary": "Add a draft for review",
                "action": {"kind": "mutate", "commands": [{"type": "add", "parentId": "n1", "prompt": "A follow-up"}]},
            },
        ),
        ("", {}),
        ("", {}),
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        requests.append(json.loads(request.content))
        index = len(requests) - 1
        name, arguments = replies[index]
        text = "Pending approval. Nothing was applied." if index == 4 else "Your decision is recorded."
        if api == "responses":
            output = (
                [
                    {
                        "type": "function_call",
                        "id": f"fc_{index}",
                        "call_id": f"call_{index}",
                        "name": name,
                        "arguments": json.dumps(arguments),
                        "status": "completed",
                    }
                ]
                if name
                else [
                    {
                        "type": "message",
                        "id": f"msg_{index}",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}],
                    }
                ]
            )
            if index == 0:
                output.insert(
                    0,
                    {
                        "type": "reasoning",
                        "id": "rs_0",
                        "summary": [],
                        "encrypted_content": "opaque-reasoning",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": f"resp_{index}",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "assistant-deployment",
                    "output": output,
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "error": None,
                    "incomplete_details": None,
                },
            )
        assistant: dict[str, Any] = {"role": "assistant", "content": None if name else text}
        if name:
            assistant["tool_calls"] = [
                {
                    "type": "function",
                    "id": f"call_{index}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl_{index}",
                "object": "chat.completion",
                "created": 1,
                "model": "assistant-deployment",
                "choices": [{"index": 0, "message": assistant, "finish_reason": "tool_calls" if name else "stop"}],
            },
        )

    settings = {
        "PYRIT_TREE_ASSISTANT_MODEL": "assistant-deployment",
        "PYRIT_TREE_ASSISTANT_API_KEY": "local-test-only",
        "PYRIT_TREE_ASSISTANT_BASE_URL": "https://example.invalid/v1",
    }
    if api is not None:
        settings["PYRIT_TREE_ASSISTANT_API"] = api
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        with (
            patch.dict("os.environ", settings, clear=True),
            patch("openai.AsyncOpenAI", side_effect=lambda **kwargs: AsyncOpenAI(http_client=http_client, **kwargs)),
        ):
            runtime = create_agent_framework_runtime()
        try:
            context = TreeAssistantContext.model_validate(context_dict())
            context.settings.auto_run = auto_run
            result = await runtime.run_async(message="Suggest a follow-up", context=context, receipts=[])
            proposal = result.proposal
            assert "Pending approval" in result.reply
            assert proposal is not None and proposal.status == "pending"
            assert proposal.action.commands[0].parent_id == "n1"
            assert proposal.action.run is None
            assert [trace.name for trace in result.tool_calls] == [
                "inspect_tree",
                "inspect_selected_node",
                "inspect_subtree",
                "propose_action",
            ]
            assert json.loads(json.loads(result.tool_calls[1].result)["data"])["selected_node_id"] == "n1"
            assert context.nodes[0].prompt == "Hello"
            assert len(context.nodes) == 1
            next_result = await runtime.run_async(
                message="I rejected it",
                context=context,
                receipts=[{"proposal_id": proposal.id, "status": "rejected", "revision": 1, "detail": "No thanks"}],
            )
            assert next_result.proposal is None
            assert runtime.session.service_session_id is None
            history = requests[-1].get("input", requests[-1].get("messages"))
            assert "Pending approval" in json.dumps(history)
            assert "No thanks" in json.dumps(history)
            expected_path = "/v1/responses" if api == "responses" else "/v1/chat/completions"
            assert paths == [expected_path] * 6
            assert all(payload["store"] is False for payload in requests)
            assert all("previous_response_id" not in payload and "conversation" not in payload for payload in requests)
            if api == "responses":
                assert requests[0]["max_output_tokens"] == 4096
                assert "reasoning.encrypted_content" in requests[0]["include"]
                assert any(
                    item.get("type") == "function_call_output" and item["call_id"] == "call_0"
                    for item in requests[1]["input"]
                )
                assert any(item.get("encrypted_content") == "opaque-reasoning" for item in requests[1]["input"])
                assert {tool["name"] for tool in requests[0]["tools"]} >= {
                    "inspect_tree",
                    "inspect_selected_node",
                    "inspect_subtree",
                    "propose_action",
                }
        finally:
            await runtime.close_async()
        assert http_client.is_closed


@pytest.mark.parametrize("api", ["", "completions", "unknown"])
def test_invalid_transport_rejected_before_client_creation(api: str) -> None:
    """Invalid API settings fail explicitly instead of silently choosing a transport."""
    with patch.dict("os.environ", {"PYRIT_TREE_ASSISTANT_API": api}), patch("openai.AsyncOpenAI") as client:
        with pytest.raises(TreeAssistantError, match="chat_completions or responses"):
            create_agent_framework_runtime()
        client.assert_not_called()


async def test_responses_failure_does_not_commit_session_history_async() -> None:
    """Responses errors keep the same local rollback guarantees without automatic API fallback."""
    requests: list[str] = []

    def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(400, json={"error": {"message": "Unsupported deployment"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http_client:
        with (
            patch.dict(
                "os.environ",
                {
                    "PYRIT_TREE_ASSISTANT_API": "responses",
                    "PYRIT_TREE_ASSISTANT_MODEL": "assistant-deployment",
                    "PYRIT_TREE_ASSISTANT_API_KEY": "local-test-only",
                    "PYRIT_TREE_ASSISTANT_BASE_URL": "https://example.invalid/v1",
                },
            ),
            patch("openai.AsyncOpenAI", side_effect=lambda **kwargs: AsyncOpenAI(http_client=http_client, **kwargs)),
        ):
            runtime = create_agent_framework_runtime()
        try:
            before = copy.deepcopy(runtime.session.to_dict())
            with pytest.raises(Exception, match="Unsupported deployment"):
                await runtime.run_async(
                    message="Inspect",
                    context=TreeAssistantContext.model_validate(context_dict()),
                    receipts=[],
                )
            assert runtime.session.to_dict() == before
            assert runtime.tools.proposal is None
            assert requests == ["/v1/responses"]
        finally:
            await runtime.close_async()
