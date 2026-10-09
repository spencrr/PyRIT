# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A bounded inference-only listener whose execution credentials never grant provider access."""

import asyncio
import base64
import codecs
import hashlib
import json
import logging
import secrets
import socket
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from pyrit.agent.model_binding import ResolvedModelBinding
from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.model_inference import InferenceWireApi
from pyrit.prompt_target.common.model_inference import InferenceAdmissionError

logger = logging.getLogger(__name__)


class _RelayServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


@dataclass
class InferenceLease:
    """Limited model-use authority for one execution; no upstream credential is exported."""

    binding: ResolvedModelBinding
    record: Callable[[dict[str, Any]], Awaitable[None]]
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    active: bool = True
    calls: int = 0
    max_calls: int = 100
    max_response_bytes: int = 16 * 1024 * 1024
    timeout_seconds: float = 180
    capture_content: bool = False
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


class InferenceRelay:
    """Share a listener, not authority, between executions. No generic forwarding routes exist."""

    MAX_REQUEST_BYTES = 8 * 1024 * 1024

    def __init__(self, *, host: str = "127.0.0.1", advertised_host: str = "127.0.0.1") -> None:
        """Configure an explicitly reachable listener; the default is local-only."""
        self.host = host
        self.advertised_host = advertised_host
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route("/v1/chat/completions", self._handle_async, methods=["POST"])
        self.app.add_api_route("/v1/responses", self._handle_async, methods=["POST"])
        self._leases: dict[str, InferenceLease] = {}
        self._lock = asyncio.Lock()
        self._server: _RelayServer | None = None
        self._serving: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self.url = ""

    async def start_async(self) -> None:
        """
        Start the inference listener with bounded startup and no application signal handlers.

        Raises:
            RuntimeError: The listener stopped before becoming ready.
        """
        async with self._lock:
            if self._server is not None:
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((self.host, 0))
                sock.listen(128)
                sock.setblocking(False)
                self._socket = sock
                self._server = _RelayServer(
                    uvicorn.Config(
                        self.app,
                        host=self.host,
                        log_level="error",
                        access_log=False,
                        lifespan="off",
                        timeout_graceful_shutdown=5,
                    )
                )
                self._serving = asyncio.create_task(self._server.serve(sockets=[sock]))
                async with asyncio.timeout(10):
                    while not self._server.started:
                        if self._serving.done():
                            await self._serving
                            raise RuntimeError("Inference relay stopped during startup")
                        await asyncio.sleep(0.01)
                self.url = f"http://{self.advertised_host}:{sock.getsockname()[1]}/v1"
            except BaseException:
                if self._server:
                    self._server.should_exit = True
                if self._serving:
                    await await_task_completion_async(self._serving)
                sock.close()
                self._server = None
                raise

    def add(self, lease: InferenceLease) -> None:
        """Register one opaque, bounded execution credential."""
        self._leases[hashlib.sha256(lease.token.encode()).hexdigest()] = lease

    async def revoke_async(self, lease: InferenceLease) -> None:
        """Revoke admission and join all cancelled in-flight model requests."""
        lease.active = False
        self._leases.pop(hashlib.sha256(lease.token.encode()).hexdigest(), None)
        tasks = tuple(lease.tasks)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:

            async def join_async() -> None:
                await asyncio.gather(*tasks, return_exceptions=True)

            await await_task_completion_async(asyncio.create_task(join_async()))

    async def _handle_async(self, request: Request) -> Response:
        credential = request.headers.get("authorization", "")
        digest = hashlib.sha256(credential.removeprefix("Bearer ").encode()).hexdigest()
        lease = self._leases.get(digest) if credential.startswith("Bearer ") else None
        if lease is None or not lease.active:
            return JSONResponse({"error": {"message": "Invalid or expired execution credential"}}, status_code=401)
        task = asyncio.current_task()
        assert task is not None
        if lease.calls >= lease.max_calls or len(lease.tasks) >= 4:
            return JSONResponse({"error": {"message": "Execution inference budget exhausted"}}, status_code=429)
        lease.tasks.add(task)
        lease.calls += 1
        try:
            data = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > self.MAX_REQUEST_BYTES:
                        lease.tasks.discard(task)
                        return JSONResponse({"error": {"message": "Inference request too large"}}, status_code=413)
            body = json.loads(data)
            if not isinstance(body, dict):
                raise ValueError("Inference request must be an object")
            self._validate_body(lease=lease, request=request, body=body)
            # The response owns the task registration until streaming and upstream cleanup finish.
            return _InferenceResponse(relay=self, lease=lease, body=body, original_task=task)
        except (ValueError, TimeoutError) as error:
            lease.tasks.discard(task)
            return JSONResponse({"error": {"message": str(error)}}, status_code=400)
        except BaseException:
            lease.tasks.discard(task)
            raise

    @staticmethod
    def _validate_body(*, lease: InferenceLease, request: Request, body: dict[str, Any]) -> None:
        protocol = lease.binding.requirements.wire_api
        expected = "/v1/chat/completions" if protocol == InferenceWireApi.CHAT_COMPLETIONS else "/v1/responses"
        if request.url.path != expected or request.url.query:
            raise ValueError("Request does not match the bound inference protocol")
        if body.get("model") != lease.binding.model:
            raise ValueError("Model does not match this execution binding")
        if any(key in body for key in ("api_key", "base_url", "headers", "extra_headers", "extra_query", "extra_body")):
            raise ValueError("Provider routing and authentication overrides are not allowed")
        if body.get("previous_response_id") or body.get("conversation") or body.get("background"):
            raise ValueError("Server-side continuation and background execution are not supported by this binding")
        if not isinstance(body.get("stream", False), bool):
            raise ValueError("stream must be boolean")
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") not in ("function", "custom") for tool in tools
        ):
            raise ValueError("Only harness-executed tool declarations are supported")
        content = body.get("messages" if protocol == InferenceWireApi.CHAT_COMPLETIONS else "input")
        if content is None:
            raise ValueError("Inference request is missing conversation content")
        InferenceRelay._reject_provider_files(content)
        if protocol == InferenceWireApi.RESPONSES and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and (
                    item.get("type") == "item_reference" or ("id" in item and set(item) <= {"id", "type"})
                ):
                    raise ValueError("Provider-side item references are not scoped to this execution")

    @staticmethod
    def _reject_provider_files(content: Any) -> None:
        if isinstance(content, list):
            for item in content:
                InferenceRelay._reject_provider_files(item)
        elif isinstance(content, dict):
            if isinstance(content.get("file_id"), str):
                raise ValueError("Provider-side file references are not scoped to this execution")
            for value in content.values():
                InferenceRelay._reject_provider_files(value)

    async def close_async(self) -> None:
        """Revoke bindings before closing the owned listener."""
        for lease in tuple(self._leases.values()):
            await self.revoke_async(lease)
        if self._server:
            self._server.should_exit = True
        if self._serving:
            await await_task_completion_async(self._serving)
        if self._socket:
            self._socket.close()
        self._server = None


class _InferenceResponse(Response):
    """ASGI streaming ownership lasts through provider close, including peer disconnects."""

    def __init__(
        self,
        *,
        relay: InferenceRelay,
        lease: InferenceLease,
        body: dict[str, Any],
        original_task: asyncio.Task[Any],
    ) -> None:
        super().__init__(content=b"")
        self.relay, self.lease, self._request_body, self.original_task = relay, lease, body, original_task
        self._terminal_seen = False
        self._sse_buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _observe_terminal(self, chunk: bytes) -> None:
        if not self._request_body.get("stream"):
            return
        self._sse_buffer += self._decoder.decode(chunk)
        while "\n\n" in self._sse_buffer:
            frame, self._sse_buffer = self._sse_buffer.split("\n\n", 1)
            payload = "\n".join(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
            if payload == "[DONE]":
                self._terminal_seen = True
            elif payload.startswith("{"):
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    logger.debug("Inference SSE frame is not a JSON observation")
                else:
                    if isinstance(event, dict) and event.get("type") in (
                        "response.completed",
                        "response.failed",
                        "response.incomplete",
                    ):
                        self._terminal_seen = True
        self._sse_buffer = self._sse_buffer[-65536:]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def disconnect_async() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        forwarding = asyncio.create_task(self._forward_async(send))
        disconnected = asyncio.create_task(disconnect_async())
        try:
            done, _ = await asyncio.wait((forwarding, disconnected), return_when=asyncio.FIRST_COMPLETED)
            if forwarding in done:
                await forwarding
            elif self._terminal_seen:
                # Clients may close as soon as [DONE] arrives; allow bounded provider/evidence finalization.
                await asyncio.wait_for(asyncio.shield(forwarding), timeout=5)
        finally:
            if not forwarding.done() and not forwarding.cancelling():
                forwarding.cancel()
            disconnected.cancel()

            async def join_async() -> None:
                await asyncio.gather(forwarding, disconnected, return_exceptions=True)

            try:
                await await_task_completion_async(asyncio.create_task(join_async()))
            finally:
                self.lease.tasks.discard(self.original_task)

    async def _forward_async(self, send: Any) -> None:
        lease = self.lease
        request_id = str(uuid4())
        started = False
        count = 0
        outcome = "unknown"
        record: dict[str, Any] = {
            "type": "inference.started",
            "request_id": request_id,
            "target_hash": lease.binding.identifier_hash,
            "wire_api": lease.binding.requirements.wire_api.value,
            "turn_correlation": "active_execution_turn_window",
        }
        if lease.capture_content:
            record["body"] = self._request_body
        try:
            await lease.record(record)
            if not lease.active or lease.binding.target.get_identifier().hash != lease.binding.identifier_hash:
                raise ValueError("Execution binding is revoked or its source target changed")
            async with asyncio.timeout(lease.timeout_seconds):
                async with lease.binding.target.open_inference_async(
                    body=self._request_body,
                    requirements=lease.binding.requirements,
                    request_id=request_id,
                ) as response:
                    headers = {
                        key.lower(): value
                        for key, value in response.headers.items()
                        if key.lower() in ("content-type", "retry-after", "x-request-id")
                    }
                    await lease.record(
                        {
                            "type": "inference.response",
                            "request_id": request_id,
                            "status_code": response.status_code,
                            "headers": headers,
                        }
                    )
                    await send(
                        {
                            "type": "http.response.start",
                            "status": response.status_code,
                            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                        }
                    )
                    started = True
                    async for chunk in response.body:
                        count += len(chunk)
                        if count > lease.max_response_bytes:
                            raise ValueError("Inference response exceeded its byte budget")
                        if lease.capture_content:
                            await lease.record(
                                {
                                    "type": "inference.chunk",
                                    "request_id": request_id,
                                    "base64": base64.b64encode(chunk).decode("ascii"),
                                }
                            )
                        self._observe_terminal(chunk)
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    outcome = "completed" if 200 <= response.status_code < 300 else "provider_error"
            await lease.record(
                {"type": "inference.finished", "request_id": request_id, "outcome": outcome, "bytes": count}
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except BaseException as error:
            outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
            logger.warning("Inference %s ended %s (%s)", request_id, outcome, type(error).__name__)
            await lease.record(
                {
                    "type": "inference.finished",
                    "request_id": request_id,
                    "outcome": outcome,
                    "error_type": type(error).__name__,
                    "bytes": count,
                }
            )
            if started or isinstance(error, asyncio.CancelledError):
                raise
            status = (
                429 if isinstance(error, InferenceAdmissionError) else 400 if isinstance(error, ValueError) else 502
            )
            message = (
                str(error) if isinstance(error, (InferenceAdmissionError, ValueError)) else "Target inference failed"
            )
            response = JSONResponse({"error": {"message": message}}, status_code=status)
            if status == 429:
                response.headers["retry-after"] = "5"
            await send({"type": "http.response.start", "status": status, "headers": response.raw_headers})
            await send({"type": "http.response.body", "body": response.body})
