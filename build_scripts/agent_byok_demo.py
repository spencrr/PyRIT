# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real Copilot -> scoped relay -> registered target -> synthetic provider smoke test."""

import argparse
import asyncio
import json
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile, ModelBinding
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget
from pyrit.registry import TargetRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async


async def run_async(args: argparse.Namespace) -> None:
    """Verify tool-round payloads and provider credential isolation without calling a real model."""
    await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[], load_defaults=False, silent=True)
    root = Path(args.results).resolve()
    provider_secret = secrets.token_urlsafe(32)
    calls: list[dict[str, Any]] = []
    app = FastAPI()
    manager = AgentExecutionManager(root=root)

    @app.post("/v1/chat/completions")
    async def complete_async(request: Request) -> Any:
        if request.headers.get("authorization") != f"Bearer {provider_secret}":
            return JSONResponse({"error": {"message": "Wrong provider credential"}}, status_code=401)
        body = await request.json()
        calls.append(body)
        assert body["model"] == "test-deployment"
        has_result = any(message.get("role") == "tool" for message in body.get("messages", []))
        view = next(
            (tool["function"] for tool in body["tools"] if tool.get("function", {}).get("name") == "view"), None
        )
        assert view is not None, "Copilot did not advertise its view tool"
        record = next(iter(manager.records.values()))
        fixture = (
            "/workspace/orders.json"
            if args.docker
            else str(manager.store.directory(record.id) / "workspace" / "orders.json")
        )
        delta = {"role": "assistant", "content": "Receipt total: 31"}
        finish = "stop"
        if not has_result:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "receipt-view",
                        "type": "function",
                        "function": {"name": view["name"], "arguments": json.dumps({"path": fixture})},
                    }
                ],
            }
            finish = "tool_calls"

        async def stream_async() -> AsyncIterator[str]:
            for content, reason in ((delta, None), ({}, finish)):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "synthetic-receipt",
                            "object": "chat.completion.chunk",
                            "model": "test-deployment",
                            "choices": [{"index": 0, "delta": content, "finish_reason": reason}],
                            "vendor_field_retained": True,
                        }
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_async(), media_type="text/event-stream")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    cleanup = AsyncExitStack()
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        source = TargetRegistry.get_registry_singleton().create_named_instance(
            name="receipt-model",
            type_name="OpenAIChatTarget",
            params={
                "endpoint": f"http://127.0.0.1:{port}/v1",
                "model_name": "test-deployment",
                "underlying_model": "gpt-4.1",
                "api_key": provider_secret,
            },
        )
        cleanup.push_async_callback(source.cleanup_target_async)
        configuration = AgentTargetConfiguration(
            name="copilot-byok",
            model_binding=ModelBinding(target_registry_name="receipt-model"),
            harness_profile=HarnessProfile(permission_policy="allow_once"),
            environment_template=EnvironmentTemplate(
                environment="docker" if args.docker else "local",
                local_execution_acknowledged=not args.docker,
                image=args.image if args.docker else None,
                fixture_directory=str(Path(__file__).resolve().parents[1] / "assets" / "agent_receipt"),
            ),
        )
        async with manager:
            target = AgentTarget(agent_configuration=configuration)
            target.bind_execution_manager(manager)
            response = await PromptNormalizer().send_prompt_async(
                target=target,
                message=MessagePiece(
                    role="user", original_value="Read orders.json with the view tool and report the total."
                ).to_message(),
            )
            assert response.get_piece().converted_value == "Receipt total: 31"
        assert len(calls) >= 2 and any(
            message.get("role") == "tool" for call in calls for message in call.get("messages", [])
        )
        assert all(call["stream"] is True for call in calls)
        for path in root.glob("*/*.json*"):
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            assert provider_secret not in content, "Provider credential leaked into execution evidence"
        print(
            f"PASS: {len(calls)} target-owned streaming requests, "
            "Copilot tool roundtrip, no provider credential in evidence."
        )
        print(f"Evidence: {root}")
    finally:
        try:
            await cleanup.aclose()
        finally:
            server.should_exit = True
            await serving
            sock.close()


def main() -> None:
    """Run locally or in an explicitly configured Docker relay network."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--image", default="pyrit-copilot-receipt:1.0.93")
    asyncio.run(run_async(parser.parse_args()))


if __name__ == "__main__":
    main()
