# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Conservative ACP projection into PyRIT's existing tool-message evidence contract."""

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from pyrit.models import Message, MessagePiece, ToolExecutionMetadata
from pyrit.models.agent_execution import AgentExecutionEvent
from pyrit.models.messages.tool_content import FunctionCallContent, FunctionOutputContent
from pyrit.models.target.agent_tool_call import AgentToolCallProvenance

# ACP `_meta` is an unstructured implementation slot; only PyRIT's namespaced key carries trusted tool identity.
TOOL_NAME_META_KEY = "pyrit.tool_name"


@dataclass
class _Call:
    first: AgentExecutionEvent
    name: str | None = None
    arguments: dict[str, Any] | None = None
    invoked_at: int | None = None
    ambiguous: bool = False
    output: Any = None
    output_event: AgentExecutionEvent | None = None
    terminal: AgentExecutionEvent | None = None
    terminal_status: str | None = None


@dataclass
class AgentToolProjection:
    """Canonical evidence and explicit reasons some reports could not be represented."""

    messages: list[Message] = field(default_factory=list)
    omitted: dict[str, str] = field(default_factory=dict)


def project_agent_tools(
    *, events: list[AgentExecutionEvent], execution_id: UUID, turn_id: UUID, conversation_id: str
) -> AgentToolProjection:
    """
    Project explicit named calls/results without guessing tool names or invocation from claims.

    Returns:
        AgentToolProjection: Ordered canonical messages, retaining call/event provenance.

    Raises:
        ValueError: Input events cross executions, turns, or contain duplicate/out-of-order sequences.
    """
    calls: dict[str, _Call] = {}
    permissions: dict[str, tuple[str, set[str]]] = {}
    denied: set[str] = set()
    last_sequence = 0
    for event in events:
        if event.execution_id != execution_id or event.turn_id != turn_id or event.sequence <= last_sequence:
            raise ValueError("Tool call events must be ordered within one execution turn")
        last_sequence = event.sequence
        if event.direction == "lifecycle" and event.payload.get("type") == "permission.resolved":
            approval = event.payload.get("approval")
            if isinstance(approval, dict) and approval.get("decision") != "operator_allow":
                denied.add(str(approval.get("tool_call_id")))
            continue
        params = event.payload.get("params")
        if event.direction == "incoming" and event.payload.get("method") == "session/request_permission":
            tool = params.get("toolCall") if isinstance(params, dict) else None
            options = params.get("options") if isinstance(params, dict) else None
            if isinstance(tool, dict):
                reported_options = options if isinstance(options, list) else []
                permissions[str(event.payload.get("id"))] = (
                    str(tool.get("toolCallId")),
                    {
                        str(option.get("optionId"))
                        for option in reported_options
                        if isinstance(option, dict) and option.get("kind") in ("reject_once", "reject_always")
                    },
                )
            continue
        permission = permissions.get(str(event.payload.get("id")))
        result = event.payload.get("result")
        if event.direction == "outgoing" and permission and "error" in event.payload:
            denied.add(permission[0])
            continue
        if event.direction == "outgoing" and permission and isinstance(result, dict):
            outcome = result.get("outcome")
            if isinstance(outcome, dict) and (
                outcome.get("outcome") == "cancelled" or outcome.get("optionId") in permission[1]
            ):
                denied.add(permission[0])
            continue
        if (
            event.direction != "incoming"
            or event.payload.get("method") != "session/update"
            or not isinstance(params, dict)
        ):
            continue
        update = params.get("update")
        if not isinstance(update, dict) or update.get("sessionUpdate") not in ("tool_call", "tool_call_update"):
            continue
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            continue
        if update["sessionUpdate"] == "tool_call":
            if call_id in calls:
                calls[call_id].ambiguous = True
                continue
            calls[call_id] = _Call(first=event)
        call = calls.get(call_id)
        if call is None:
            calls[call_id] = _Call(first=event, ambiguous=True)
            continue
        name, arguments = update.get("name"), update.get("rawInput")
        meta = update.get("_meta")
        if name is None and isinstance(meta, dict):
            # PyRIT's own ACP hosts report the exact function that ran under a namespaced extension key.
            name = meta.get(TOOL_NAME_META_KEY)
        if isinstance(name, str) and name.strip():
            if call.name and call.name != name:
                call.ambiguous = True
            call.name = name
        if isinstance(arguments, dict):
            if call.arguments is not None and call.arguments != arguments:
                call.ambiguous = True
            call.arguments = arguments
        if update.get("status") in ("in_progress", "completed") and call.invoked_at is None:
            call.invoked_at = event.sequence
        status = update.get("status")
        if status in ("completed", "failed"):
            if call.terminal_status is not None and status != call.terminal_status:
                call.ambiguous = True
            call.terminal, call.terminal_status = event, str(status)
        if "rawOutput" in update:
            call.output, call.output_event = update["rawOutput"], event

    projection = AgentToolProjection()
    ordered: list[tuple[int, Message]] = []
    for source_id, call in calls.items():
        reason = (
            "ambiguous_call_reports"
            if call.ambiguous
            else "permission_not_granted"
            if source_id in denied
            else "name_not_reported"
            if not call.name
            else "arguments_not_reported_as_object"
            if call.arguments is None
            else None
        )
        if reason:
            projection.omitted[source_id] = reason
            continue
        call_id = f"{execution_id}:{turn_id}:{source_id}"
        content = FunctionCallContent(
            type="function_call",
            call_id=call_id,
            name=call.name,
            arguments=json.dumps(call.arguments, ensure_ascii=True),
        )
        content.validated_call_id()
        ordered.append(
            (
                call.first.sequence,
                _piece(
                    event=call.first,
                    source_id=source_id,
                    conversation_id=conversation_id,
                    value=content.model_dump_json(exclude_none=True),
                ).to_message(),
            )
        )
        if (
            call.terminal is not None
            and call.output_event is not None
            and call.invoked_at is not None
            and call.invoked_at <= call.terminal.sequence
        ):
            result_event = max((call.terminal, call.output_event), key=lambda event: event.sequence)
            output = FunctionOutputContent(type="function_call_output", call_id=call_id, output=call.output)
            ordered.append(
                (
                    result_event.sequence,
                    _piece(
                        event=result_event,
                        source_id=source_id,
                        conversation_id=conversation_id,
                        value=output.model_dump_json(),
                        invoked=True,
                    ).to_message(),
                )
            )
        else:
            projection.omitted[source_id] = "invocation_or_result_not_established"
    projection.messages = [message for _, message in sorted(ordered, key=lambda entry: entry[0])]
    return projection


def _piece(
    *, event: AgentExecutionEvent, source_id: str, conversation_id: str, value: str, invoked: bool = False
) -> MessagePiece:
    assert event.turn_id is not None
    reference = AgentToolCallProvenance(
        execution_id=event.execution_id, turn_id=event.turn_id, source_call_id=source_id, event_sequence=event.sequence
    )
    metadata = {
        **reference.to_metadata(),
        "agent_execution_id": str(event.execution_id),
        "agent_turn_id": str(event.turn_id),
    }
    if invoked:
        metadata.update(ToolExecutionMetadata(invoked=True).to_metadata())
    return MessagePiece(
        id=uuid5(event.execution_id, f"{event.turn_id}:{event.sequence}:{'output' if invoked else 'call'}"),
        conversation_id=conversation_id,
        role="tool" if invoked else "assistant",
        original_value=value,
        original_value_data_type="function_call_output" if invoked else "function_call",
        prompt_metadata=metadata,
    )
