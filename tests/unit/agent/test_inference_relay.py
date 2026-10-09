# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pyrit.agent.inference_relay import InferenceLease, InferenceRelay
from pyrit.agent.model_binding import ResolvedModelBinding
from pyrit.models.model_inference import InferenceCapabilities, InferenceRequirements, InferenceWireApi
from pyrit.prompt_target import TextTarget
from pyrit.prompt_target.common.model_inference import InferenceResponse


class CustomInferenceTarget(TextTarget):
    """A non-OpenAI target proving eligibility is not class-name based."""

    @property
    def inference_capabilities(self) -> InferenceCapabilities:
        return InferenceCapabilities(
            wire_apis=frozenset({InferenceWireApi.CHAT_COMPLETIONS}),
            streaming=True,
            tool_calls=True,
            input_modalities=frozenset({"text"}),
        )

    @asynccontextmanager
    async def open_inference_async(
        self, *, body: dict[str, Any], requirements: InferenceRequirements, request_id: str
    ) -> AsyncGenerator[InferenceResponse, None]:
        async def chunks_async() -> AsyncIterator[bytes]:
            yield b'data: {"custom":true}\n\n'
            yield b"data: [DONE]\n\n"

        yield InferenceResponse(
            status_code=200, headers={"content-type": "text/event-stream", "set-cookie": "private"}, body=chunks_async()
        )


@pytest.fixture
def relay(patch_central_database: MagicMock) -> tuple[InferenceRelay, InferenceLease]:
    target = CustomInferenceTarget()
    binding = ResolvedModelBinding(
        target=target,
        identifier_hash=target.get_identifier().hash,
        model="bound-model",
        requirements=InferenceRequirements(),
    )
    relay = InferenceRelay()
    lease = InferenceLease(binding=binding, record=AsyncMock())
    relay.add(lease)
    return relay, lease


async def test_scoped_relay_calls_custom_target_and_retains_stream(
    relay: tuple[InferenceRelay, InferenceLease],
) -> None:
    instance, lease = relay
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance.app), base_url="http://relay") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {lease.token}"},
            json={"model": "bound-model", "messages": [], "stream": True},
        )
        assert response.status_code == 200
        assert response.content.endswith(b"data: [DONE]\n\n")
        assert "set-cookie" not in response.headers
        assert not lease.tasks
        assert lease.record.call_args_list[-1].args[0]["outcome"] == "completed"
        assert all("body" not in call.args[0] for call in lease.record.call_args_list)


@pytest.mark.parametrize(
    "body",
    [
        {"model": "another", "messages": []},
        {"model": "bound-model", "messages": [], "extra_headers": {"authorization": "override"}},
        {"model": "bound-model", "messages": [], "tools": [{"type": "web_search"}]},
        {"model": "bound-model", "messages": [], "previous_response_id": "foreign"},
    ],
)
async def test_invalid_binding_request_never_invokes_target(
    relay: tuple[InferenceRelay, InferenceLease], body: dict[str, Any]
) -> None:
    instance, lease = relay
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance.app), base_url="http://relay") as client:
        response = await client.post(
            "/v1/chat/completions", headers={"authorization": f"Bearer {lease.token}"}, json=body
        )
        assert response.status_code == 400
        lease.record.assert_not_awaited()


async def test_missing_revoked_and_exhausted_credentials(relay: tuple[InferenceRelay, InferenceLease]) -> None:
    instance, lease = relay
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance.app), base_url="http://relay") as client:
        assert (await client.post("/v1/chat/completions", json={})).status_code == 401
        lease.calls = lease.max_calls
        assert (
            await client.post("/v1/chat/completions", headers={"authorization": f"Bearer {lease.token}"}, json={})
        ).status_code == 429
        await instance.revoke_async(lease)
        assert (
            await client.post("/v1/chat/completions", headers={"authorization": f"Bearer {lease.token}"}, json={})
        ).status_code == 401


async def test_revoke_cancels_and_joins_inflight_work(relay: tuple[InferenceRelay, InferenceLease]) -> None:
    instance, lease = relay
    stopped = asyncio.Event()

    async def work_async() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    task = asyncio.create_task(work_async())
    await asyncio.sleep(0)
    lease.tasks.add(task)
    await instance.revoke_async(lease)
    assert task.cancelled()
    assert stopped.is_set()


async def test_stream_failure_does_not_synthesize_success_or_retry(
    relay: tuple[InferenceRelay, InferenceLease],
) -> None:
    from unittest.mock import patch

    instance, lease = relay
    calls = 0

    @asynccontextmanager
    async def broken_async(**kwargs: Any) -> AsyncGenerator[InferenceResponse, None]:
        nonlocal calls
        calls += 1

        async def chunks_async() -> AsyncIterator[bytes]:
            yield b"data: partial\n\n"
            raise ConnectionError("upstream closed")

        yield InferenceResponse(status_code=200, headers={"content-type": "text/event-stream"}, body=chunks_async())

    with patch.object(lease.binding.target, "open_inference_async", broken_async):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=instance.app), base_url="http://relay"
        ) as client:
            with pytest.raises(ConnectionError, match="upstream closed"):
                await client.post(
                    "/v1/chat/completions",
                    headers={"authorization": f"Bearer {lease.token}"},
                    json={"model": "bound-model", "messages": [], "stream": True},
                )
    assert calls == 1
    assert not lease.tasks
    assert lease.record.call_args_list[-1].args[0]["outcome"] == "failed"


async def test_oversized_request_does_not_leak_admission_slots(relay: tuple[InferenceRelay, InferenceLease]) -> None:
    instance, lease = relay
    instance.MAX_REQUEST_BYTES = 64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance.app), base_url="http://relay") as client:
        for _ in range(5):
            response = await client.post(
                "/v1/chat/completions",
                headers={"authorization": f"Bearer {lease.token}"},
                content=b"x" * 65,
            )
            assert response.status_code == 413
            assert not lease.tasks
        response = await client.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {lease.token}"},
            json={"model": "bound-model", "messages": []},
        )
        assert response.status_code == 200


async def test_provider_file_reference_is_rejected_before_dispatch(
    relay: tuple[InferenceRelay, InferenceLease],
) -> None:
    instance, lease = relay
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=instance.app), base_url="http://relay") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {lease.token}"},
            json={
                "model": "bound-model",
                "messages": [{"role": "user", "content": [{"type": "file", "file": {"file_id": "foreign"}}]}],
            },
        )
    assert response.status_code == 400
    lease.record.assert_not_awaited()


@pytest.mark.parametrize("item", [{"type": "item_reference", "id": "foreign"}, {"id": "foreign"}])
async def test_response_item_reference_cannot_cross_execution_scope(
    relay: tuple[InferenceRelay, InferenceLease], item: dict[str, Any]
) -> None:
    from starlette.requests import Request

    instance, lease = relay
    lease.binding = ResolvedModelBinding(
        target=lease.binding.target,
        identifier_hash=lease.binding.identifier_hash,
        model="bound-model",
        requirements=InferenceRequirements(wire_api=InferenceWireApi.RESPONSES),
    )
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "query_string": b"", "headers": []})
    with pytest.raises(ValueError, match="item references"):
        instance._validate_body(lease=lease, request=request, body={"model": "bound-model", "input": [item]})


async def test_repeated_revocation_joins_provider_cleanup_without_recancelling(
    relay: tuple[InferenceRelay, InferenceLease],
) -> None:
    from unittest.mock import patch

    instance, lease = relay
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    @asynccontextmanager
    async def pending_async(**kwargs: Any) -> AsyncGenerator[InferenceResponse, None]:
        async def chunks_async() -> AsyncIterator[bytes]:
            entered.set()
            await asyncio.sleep(30)
            yield b"unused"

        try:
            yield InferenceResponse(status_code=200, headers={}, body=chunks_async())
        finally:
            await asyncio.sleep(0.05)
            cleaned.set()

    with patch.object(lease.binding.target, "open_inference_async", pending_async):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=instance.app), base_url="http://relay"
        ) as client:
            send = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    headers={"authorization": f"Bearer {lease.token}"},
                    json={"model": "bound-model", "messages": [], "stream": True},
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=3)
            await asyncio.gather(instance.revoke_async(lease), instance.revoke_async(lease))
            await asyncio.gather(send, return_exceptions=True)
    assert cleaned.is_set()
    assert not lease.tasks
