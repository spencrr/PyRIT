# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic ACP peer for transport/lifecycle smoke tests; never substitutes for live validation."""

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

output_lock = threading.Lock()
cancelled = threading.Event()
session_id = str(uuid4())
permission_ready = threading.Event()
permission_reply: dict[str, Any] = {}


def emit(payload: dict[str, Any]) -> None:
    """Write a complete protocol frame."""
    with output_lock:
        print(json.dumps({"jsonrpc": "2.0", **payload}), flush=True)


def update(payload: dict[str, Any]) -> None:
    """Send a session update."""
    emit({"method": "session/update", "params": {"sessionId": session_id, "update": payload}})


def execute(*, request_id: int, prompt: str) -> None:
    """Execute the benign fixture or wait until protocol cancellation."""
    if prompt in ("ordered", "approve"):
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "I will read the order."}})
    update({"sessionUpdate": "tool_call", "toolCallId": "receipt", "title": "Read orders", "kind": "read"})
    if prompt == "approve":
        permission_ready.clear()
        permission_reply.clear()
        emit(
            {
                "id": "permission",
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": "receipt", "title": "Read orders.json"},
                    "options": [
                        {"optionId": "yes", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "no", "name": "Deny", "kind": "reject_once"},
                    ],
                },
            }
        )
        permission_ready.wait(30)
        if permission_reply.get("outcome", {}).get("optionId") != "yes":
            update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Permission was not granted."},
                }
            )
            emit({"id": request_id, "result": {"stopReason": "cancelled" if cancelled.is_set() else "end_turn"}})
            return
    if prompt == "wait":
        cancelled.wait(timeout=30)
        emit({"id": request_id, "result": {"stopReason": "cancelled"}})
        return
    orders = Path("orders.json")
    data = json.loads(orders.read_text(encoding="utf-8"))
    if "add one orange" in prompt.lower():
        data["items"][1]["quantity"] += 1
        orders.write_text(json.dumps(data), encoding="utf-8")
    total = sum(item["quantity"] * item["unit_price"] for item in data["items"])
    if prompt in ("ordered", "approve"):
        update({"sessionUpdate": "tool_call_update", "toolCallId": "receipt", "status": "completed"})
        update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Now I will write the receipt."},
            }
        )
        time.sleep(0.05)
        update({"sessionUpdate": "tool_call", "toolCallId": "write", "title": "Write receipt", "kind": "edit"})
    Path("receipt.json").write_text(json.dumps({"total": total}), encoding="utf-8")
    update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "write" if prompt in ("ordered", "approve") else "receipt",
            "status": "completed",
            "rawOutput": {"total": total},
        }
    )
    if prompt != "tool-only":
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"Receipt total: {total}"}})
    emit({"id": request_id, "result": {"stopReason": "end_turn"}})


def main() -> None:
    """Serve ACP v1 over stdio."""
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id", 0)
        if method == "initialize":
            emit(
                {
                    "id": request_id,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {},
                        "agentInfo": {"name": "pyrit-scripted-test-agent", "version": "1"},
                    },
                }
            )
        elif method == "session/new":
            emit({"id": request_id, "result": {"sessionId": session_id}})
        elif method == "session/prompt":
            cancelled.clear()
            threading.Thread(
                target=execute,
                kwargs={"request_id": request_id, "prompt": request["params"]["prompt"][0]["text"]},
                daemon=True,
            ).start()
        elif method == "session/cancel":
            cancelled.set()
            permission_ready.set()
        elif request_id == "permission" and "result" in request:
            permission_reply.update(request["result"])
            permission_ready.set()
        elif "id" in request:
            emit({"id": request_id, "error": {"code": -32601, "message": "Unsupported test method"}})


if __name__ == "__main__":
    main()
