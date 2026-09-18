# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Ephemeral proposal-only assistant endpoints. No route applies mutations or executes PyRIT."""

import os
from collections.abc import Callable, Coroutine
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.tree_assistant import (
    CreateTreeAssistantSession,
    TreeAssistantMessage,
    TreeAssistantProposal,
    TreeAssistantReceipt,
    TreeAssistantSession,
    TreeAssistantTurn,
)
from pyrit.backend.services.tree_assistant_runtime import TreeAssistantError
from pyrit.backend.services.tree_assistant_service import TreeAssistantService


class _AssistantRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle_async(request: Request) -> Response:
            try:
                if request.method == "POST":
                    limit = CreateTreeAssistantSession.MAX_BYTES if self.path.endswith("/sessions") else 600_000
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > limit:
                            raise TreeAssistantError(status=413, detail=f"Assistant request exceeds {limit} bytes.")
                    request._body = bytes(body)
                return await handler(request)
            except TreeAssistantError as exc:
                problem = ProblemDetail(
                    type="/errors/tree-assistant",
                    title="Tree Assistant Error",
                    status=exc.status,
                    detail=exc.detail,
                    instance=request.url.path,
                )
                return JSONResponse(status_code=exc.status, content=problem.model_dump(exclude_none=True))

        return handle_async


router = APIRouter(prefix="/tree-assistant", route_class=_AssistantRoute)


def get_tree_assistant_service(request: Request) -> TreeAssistantService:
    """
    Get the application's in-process service, replaceable by tests.

    Returns:
        TreeAssistantService: The application's ephemeral service.
    """
    service = getattr(request.app.state, "tree_assistant_service", None)
    if service is None:
        service = TreeAssistantService()
        request.app.state.tree_assistant_service = service
    return cast("TreeAssistantService", service)


def _owner(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    if isinstance(user, AuthenticatedUser):
        return user.oid
    if any(os.getenv(key) for key in ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_ALLOWED_GROUP_IDS")):
        raise TreeAssistantError(status=401, detail="Authenticated user identity is required.")
    return None


@router.post("/sessions", response_model=TreeAssistantSession, status_code=201)
async def create_session_async(*, request: Request, body: CreateTreeAssistantSession) -> TreeAssistantSession:
    """
    Create a fresh assistant session, optionally restoring an untrusted portable transcript.

    Returns:
        TreeAssistantSession: The session with non-executable client-reported history, if supplied.
    """
    return await get_tree_assistant_service(request).create_session_async(
        workspace_id=body.workspace_id, owner=_owner(request), history=body.history
    )


@router.get("/sessions/{session_id}", response_model=TreeAssistantSession)
async def get_session_async(*, request: Request, session_id: str) -> TreeAssistantSession:
    """
    Recover the latest 50 completed turns, including their current proposal receipts.

    Returns:
        TreeAssistantSession: The rolling public history window for this session.
    """
    return await get_tree_assistant_service(request).get_session_async(session_id=session_id, owner=_owner(request))


@router.post("/sessions/{session_id}/messages", response_model=TreeAssistantTurn)
async def send_message_async(*, request: Request, session_id: str, body: TreeAssistantMessage) -> TreeAssistantTurn:
    """
    Send an explicit user message.

    Returns:
        TreeAssistantTurn: A reply and optional pending proposal.
    """
    return await get_tree_assistant_service(request).send_message_async(
        session_id=session_id, owner=_owner(request), request=body
    )


@router.post("/sessions/{session_id}/proposals/{proposal_id}/result", response_model=TreeAssistantProposal)
async def record_result_async(
    *, request: Request, session_id: str, proposal_id: str, body: TreeAssistantReceipt
) -> TreeAssistantProposal:
    """
    Record the browser's reported human decision without invoking the assistant.

    Returns:
        TreeAssistantProposal: The updated proposal.
    """
    return await get_tree_assistant_service(request).record_result_async(
        session_id=session_id, owner=_owner(request), proposal_id=proposal_id, receipt=body
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_async(*, request: Request, session_id: str) -> Response:
    """
    Dispose an idle chat and its model client.

    Returns:
        Response: Empty HTTP 204 response.
    """
    await get_tree_assistant_service(request).delete_session_async(session_id=session_id, owner=_owner(request))
    return Response(status_code=204)
