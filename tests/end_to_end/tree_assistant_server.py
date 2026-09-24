# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local-only browser fixture: real API/Agent Framework/tools, deterministic model HTTP."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

# Standalone test runner: bootstrap repository and fixture imports first.
from mocks import MockPromptTarget  # noqa: E402

from pyrit.backend.main import app, lifespan  # noqa: E402
from pyrit.registry import TargetRegistry  # noqa: E402


@asynccontextmanager
async def fixture_lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Use isolated memory and a deterministic attack target."""
    async with lifespan(application):
        TargetRegistry.get_registry_singleton().instances.register(
            MockPromptTarget(), name="tree-assistant-test-target"
        )
        application.state.fail_next_receipt = False
        yield


@app.middleware("http")
async def lose_receipt_async(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Simulate a lost response after the real receipt was committed."""
    response = await call_next(request)
    if request.url.path.endswith("/result") and app.state.fail_next_receipt:
        app.state.fail_next_receipt = False
        return JSONResponse(status_code=503, content={"detail": "Receipt acknowledgement lost"})
    return response


@app.post("/api/tree-assistant-test/fail-receipt")
async def fail_receipt_async() -> dict[str, bool]:
    """Arm a single deterministic reporting failure."""
    app.state.fail_next_receipt = True
    return {"armed": True}


@app.post("/api/tree-assistant-test/expire-sessions")
async def expire_sessions_async() -> dict[str, bool]:
    """Simulate backend session loss without changing recorded attack evidence."""
    service = getattr(app.state, "tree_assistant_service", None)
    if service is not None:
        await service.close_async()
    return {"expired": True}


@app.post("/e2e-model/v1/chat/completions")
async def model_response_async(request: Request) -> dict[str, Any]:
    """Replace only provider inference, retaining real SDK serialization and tool dispatch."""
    body = await request.json()
    messages = body["messages"]
    user_index = max(index for index, message in enumerate(messages) if message["role"] == "user")
    raw = messages[user_index]["content"]
    if isinstance(raw, list):
        raw = "".join(part.get("text", "") for part in raw)
    prompt = json.loads(raw)["user_message"].lower()
    tools = [message for message in messages[user_index + 1 :] if message["role"] == "tool"]
    result: dict[str, Any] = {"role": "assistant"}
    name, args = "", {}
    if not tools:
        name = "inspect_tree"
    else:
        overview = json.loads(json.loads(tools[0]["content"])["data"])
        node_id = overview["selected_node_id"] or (overview["nodes"][0]["id"] if overview["nodes"] else None)
        if len(tools) == 1:
            if "overview only" in prompt:
                result["content"] = "Inspected the compact overview without modifying the workspace."
            elif "selected node only" in prompt:
                name = "inspect_selected_node"
            elif "subtree only" in prompt:
                name, args = "inspect_subtree", {"root_node_id": node_id}
            elif "evidence" in prompt:
                name, args = "inspect_evidence_async", {"node_id": node_id}
            else:
                name = "propose_action"
                autonomy = overview.get("autonomy")
                root = next((node for node in overview["nodes"] if node["id"] == node_id), None)
                if autonomy and root and root["status"] == "draft":
                    action = {"kind": "run", "node_ids": [node_id]}
                elif "multi-level" in prompt or "branching" in prompt or autonomy:
                    action = {
                        "kind": "plan",
                        "run": False if "drafts only" in prompt else True if autonomy else None,
                        "steps": [
                            {
                                "id": "probe-1",
                                "parent": {"node_id": node_id} if node_id else None,
                                "prompt": "First ordered probe",
                                "converters": [],
                            },
                            {
                                "id": "probe-2",
                                "parent": {"step_id": "probe-1"},
                                "prompt": "Second ordered probe",
                                "converters": [],
                            },
                            {
                                "id": "probe-3",
                                "parent": {"step_id": "probe-2"},
                                "prompt": "Third ordered probe",
                                "converters": [],
                            },
                        ],
                    }
                    if "branching" in prompt:
                        action["run"] = True
                        action["steps"][2]["parent"] = {"node_id": node_id}
                elif "run" in prompt:
                    action = {"kind": "run", "node_ids": [node_id]}
                else:
                    action = {
                        "kind": "mutate",
                        "run": False if "drafts only" in prompt else None,
                        "commands": [
                            {
                                "type": "add",
                                "parentId": node_id,
                                "prompt": "Harmless assistant follow-up",
                                "converters": [],
                            },
                        ],
                    }
                args = {"summary": "Review this bounded next step", "action": action}
        else:
            result["content"] = (
                "## Stored evidence\n\n" + tools[-1]["content"]
                if "evidence" in prompt
                else "## Read-only inspection\n\n" + tools[-1]["content"]
                if "only" in prompt and "drafts only" not in prompt
                else "A proposal is **pending your approval**. No tree action was applied."
            )
    if name:
        result["content"] = None
        result["tool_calls"] = [
            {
                "id": str(uuid4()),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ]
    await asyncio.sleep(0.5 if "stop while planning" in prompt else 0.02)
    return {
        "id": str(uuid4()),
        "object": "chat.completion",
        "created": 1,
        "model": "local-assistant",
        "choices": [{"index": 0, "message": result, "finish_reason": "tool_calls" if name else "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


if __name__ == "__main__":
    app.router.lifespan_context = fixture_lifespan
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PYRIT_E2E_BACKEND_PORT"]), log_level="warning")
