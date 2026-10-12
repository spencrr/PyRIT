# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pyrit.models.model_inference import InferenceRequirements, InferenceWireApi
from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget, TextTarget


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "target_type,wire,path",
    [
        (OpenAIChatTarget, InferenceWireApi.CHAT_COMPLETIONS, "/v1/chat/completions"),
        (OpenAIResponseTarget, InferenceWireApi.RESPONSES, "/v1/responses"),
    ],
)
async def test_native_payload_stream_and_auth_are_preserved(
    target_type: type[OpenAIChatTarget] | type[OpenAIResponseTarget], wire: InferenceWireApi, path: str
) -> None:
    calls: list[httpx.Request] = []
    raw = b'data: {"vendor_field":7,"tool_call":{"id":"one"}}\n\ndata: [DONE]\n\n'

    async def upstream_async(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"})

    refresh = AsyncMock(side_effect=["provider-secret-1", "provider-secret-2"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_async)) as http:
        target = target_type(
            endpoint="https://provider.test/v1",
            model_name="deployment",
            api_key=refresh,
            httpx_client_kwargs={"http_client": http},
        )
        for _ in range(2):
            async with target.open_inference_async(
                body={"model": "harness-model", "messages": [], "input": [], "tools": [], "stream": True},
                requirements=InferenceRequirements(wire_api=wire),
                request_id="request",
            ) as response:
                assert b"".join([chunk async for chunk in response.body]) == raw
        assert [request.url.path for request in calls] == [path, path]
        assert [request.headers["authorization"] for request in calls] == [
            "Bearer provider-secret-1",
            "Bearer provider-secret-2",
        ]
        assert json.loads(calls[0].content)["model"] == "deployment"
        assert json.loads(calls[0].content)["stream"] is True
        refresh.assert_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_error_bytes_and_status_are_not_converted_to_chat_text() -> None:
    raw = b'{"error":{"code":"custom_error","detail":{"retained":true}}}'
    calls = 0

    async def upstream_async(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=raw, headers={"retry-after": "5"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_async)) as http:
        target = OpenAIChatTarget(
            endpoint="https://provider.test/v1",
            model_name="model",
            api_key="test",
            httpx_client_kwargs={"http_client": http},
        )
        async with target.open_inference_async(
            body={"messages": []}, requirements=InferenceRequirements(), request_id="id"
        ) as response:
            assert response.status_code == 429
            assert b"".join([chunk async for chunk in response.body]) == raw
            assert response.headers["retry-after"] == "5"
    assert calls == 1


@pytest.mark.usefixtures("patch_central_database")
async def test_inference_does_not_run_responses_tool_loop() -> None:
    target = OpenAIResponseTarget(endpoint="https://provider.test/v1", model_name="model", api_key="test")
    with patch.object(target, "_run_tool_call_loop_async", AsyncMock()) as loop:
        assert target.inference_capabilities.tool_calls is True
        loop.assert_not_awaited()
    configured = OpenAIResponseTarget(
        endpoint="https://provider.test/v1",
        model_name="model",
        api_key="test",
        custom_functions={"tool": lambda arguments: "result"},
    )
    assert configured.inference_capabilities.blocked_reason
    assert TextTarget().inference_capabilities is None


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("target_type", "wire", "limit"),
    [
        (OpenAIChatTarget, InferenceWireApi.CHAT_COMPLETIONS, "max_completion_tokens"),
        (OpenAIResponseTarget, InferenceWireApi.RESPONSES, "max_output_tokens"),
    ],
)
async def test_bound_target_settings_override_harness_suggestions_without_mutation_async(
    target_type: type[OpenAIChatTarget] | type[OpenAIResponseTarget],
    wire: InferenceWireApi,
    limit: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[dict] = []

    async def upstream_async(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=b'data: {"ok":true}\n\ndata: [DONE]\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_async)) as http:
        target = target_type(
            endpoint="https://provider.test/v1",
            model_name="configured-model",
            api_key="test",
            temperature=0.35,
            top_p=0.8,
            **{limit: 512},
            httpx_client_kwargs={"http_client": http},
        )
        body = {
            "model": "hint",
            "messages": [{"role": "user", "content": "benign"}],
            "input": "benign",
            "temperature": 1,
            "top_p": 1,
            limit: 8192,
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
            "vendor_extension": {"retained": True},
        }
        if wire == InferenceWireApi.CHAT_COMPLETIONS:
            body["max_tokens"] = 1000
        original = deepcopy(body)
        identity = target.get_identifier().hash
        async with target.open_inference_async(
            body=body, requirements=InferenceRequirements(wire_api=wire), request_id="id"
        ) as response:
            assert b"".join([chunk async for chunk in response.body]).endswith(b"[DONE]\n\n")
        assert body == original and target.get_identifier().hash == identity
        assert calls[0]["temperature"] == 0.35 and calls[0]["top_p"] == 0.8 and calls[0][limit] == 512
        assert calls[0]["model"] == "configured-model" and calls[0]["tools"] == body["tools"]
        assert calls[0]["vendor_extension"] == body["vendor_extension"] and calls[0]["stream"] is True
        assert "max_tokens" not in calls[0]
        assert "override harness inference parameters" in caplog.text and "temperature" in caplog.text


@pytest.mark.usefixtures("patch_central_database")
async def test_unconfigured_target_parameters_remain_harness_owned_async() -> None:
    target = OpenAIChatTarget(endpoint="https://provider.test/v1", model_name="model", api_key="test")
    body = {"messages": [], "temperature": 0.4, "max_tokens": 222, "seed": 3}
    assert target._inference_body(body) == {**body, "model": "model"}
    await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_multiple_completion_request_is_still_rejected_async() -> None:
    target = OpenAIChatTarget(endpoint="https://provider.test/v1", model_name="model", api_key="test", n=1)
    with pytest.raises(ValueError, match="parameter 'n'"):
        async with target.open_inference_async(
            body={"messages": [], "n": 2}, requirements=InferenceRequirements(), request_id="id"
        ):
            pytest.fail("Multiple completions must not dispatch")
    await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("configured", "requested", "expected"),
    [
        ({"reasoning_summary": "auto"}, {"effort": "high"}, {"effort": "high", "summary": "auto"}),
        ({"reasoning_effort": "low"}, {"effort": "high", "summary": "auto"}, {"effort": "low", "summary": "auto"}),
    ],
)
async def test_reasoning_precedence_preserves_unconfigured_nested_fields_async(
    configured: dict,
    requested: dict,
    expected: dict,
) -> None:
    target = OpenAIResponseTarget(endpoint="https://provider.test/v1", model_name="model", api_key="test", **configured)
    body = {"input": "hello", "reasoning": requested}
    before = deepcopy(body)
    effective = target._inference_body(body)
    assert effective["reasoning"] == expected
    assert body == before
    effective["reasoning"]["summary"] = "concise"
    assert body == before and target._inference_defaults()["reasoning"] != effective["reasoning"]
    await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("status", [200, 429, 500])
async def test_error_and_success_bodies_are_lazily_consumed_and_closed(status: int) -> None:
    class Stream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.count = 0
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(100):
                self.count += 1
                yield b"x" * 16

        async def aclose(self) -> None:  # pyrit-async-suffix-exempt
            self.closed = True

    stream = Stream()

    async def upstream_async(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_async)) as http:
        target = OpenAIChatTarget(
            endpoint="https://provider.test/v1",
            model_name="model",
            api_key="test",
            httpx_client_kwargs={"http_client": http},
        )
        async with target.open_inference_async(
            body={"messages": []},
            requirements=InferenceRequirements(),
            request_id="id",
        ) as response:
            assert stream.count == 0
            assert response.status_code == status
            async for _ in response.body:
                break
        assert stream.count == 1
        assert stream.closed


@pytest.mark.usefixtures("patch_central_database")
async def test_target_cleanup_closes_shared_transport_idempotently() -> None:
    target = OpenAIChatTarget(endpoint="https://provider.test/v1", model_name="model", api_key="test")
    transport = target._provider_http_client
    await target.cleanup_target_async()
    await target.cleanup_target_async()
    assert transport.is_closed
