# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-process ephemeral session ownership, idempotency and lifecycle management."""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from pyrit.backend.models.tree_assistant import (
    TreeAssistantMessage,
    TreeAssistantProposal,
    TreeAssistantReceipt,
    TreeAssistantSession,
    TreeAssistantTurn,
)
from pyrit.backend.services.tree_assistant_runtime import (
    AssistantRuntime,
    TreeAssistantError,
    create_agent_framework_runtime,
)

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    public: TreeAssistantSession
    owner: str | None
    runtime: AssistantRuntime
    last_used: float
    busy: bool = False
    fingerprints: dict[str, str] = field(default_factory=dict)
    latest_revision: int = 0
    target: tuple[str, str] | None = None
    task: asyncio.Task[tuple[str, TreeAssistantProposal | None]] | None = None


class TreeAssistantService:
    """Bounded in-memory sessions; session IDs are capabilities only in unauthenticated development."""

    MAX_SESSIONS = 64
    MAX_TURNS = 50
    MAX_REQUEST_IDS = 100
    TTL_SECONDS = 3_600
    TURN_TIMEOUT_SECONDS = 90

    def __init__(
        self,
        *,
        runtime_factory: Callable[[], AssistantRuntime] = create_agent_framework_runtime,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Inject runtime construction and a monotonic clock for deterministic tests."""
        self._runtime_factory = runtime_factory
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._creating = 0

    async def create_session_async(self, *, workspace_id: str, owner: str | None) -> TreeAssistantSession:
        """
        Create a bounded owned session without making a model request.

        Returns:
            TreeAssistantSession: A new empty chat session.
        """
        await self._expire_async()
        if len(self._sessions) + self._creating >= self.MAX_SESSIONS:
            raise TreeAssistantError(
                status=429, detail="Assistant session capacity reached; close a session and retry."
            )
        self._creating += 1
        runtime: AssistantRuntime | None = None
        construction = asyncio.create_task(asyncio.to_thread(self._runtime_factory))
        try:
            runtime = await asyncio.shield(construction)
            public = TreeAssistantSession(
                session_id=secrets.token_urlsafe(32), workspace_id=workspace_id, model=runtime.model
            )
            self._sessions[public.session_id] = _Session(
                public=public, owner=owner, runtime=runtime, last_used=self._clock()
            )
            return public.model_copy(deep=True)
        except asyncio.CancelledError:
            try:
                runtime = await construction
                await self._close_runtime_async(runtime)
            finally:
                raise
        except TreeAssistantError:
            raise
        except Exception as exc:
            if runtime is not None:
                await self._close_runtime_async(runtime)
            logger.warning("Tree assistant initialization failed (%s)", type(exc).__name__)
            raise TreeAssistantError(
                status=503, detail="The assistant could not initialize. Check server configuration."
            ) from None
        finally:
            self._creating -= 1

    async def get_session_async(self, *, session_id: str, owner: str | None) -> TreeAssistantSession:
        """
        Get completed turns without exposing another owner's session.

        Returns:
            TreeAssistantSession: A copy of the recoverable session.
        """
        session = await self._get_async(session_id=session_id, owner=owner)
        return session.public.model_copy(deep=True)

    async def send_message_async(
        self, *, session_id: str, owner: str | None, request: TreeAssistantMessage
    ) -> TreeAssistantTurn:
        """
        Execute one exclusive turn or replay a completed identical request.

        Returns:
            TreeAssistantTurn: Completed reply and pending proposal, without applying actions.
        """
        session = await self._get_async(session_id=session_id, owner=owner)
        fingerprint = sha256(
            json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        known = session.fingerprints.get(request.request_id)
        if known is not None and known != fingerprint:
            raise TreeAssistantError(
                status=409, detail="request_id was already used for a different message or context."
            )
        for turn in session.public.turns:
            if turn.request_id == request.request_id:
                return turn.model_copy(deep=True)
        self._require_idle(session)
        self._validate_message(session=session, request=request)
        session.busy = True
        session.fingerprints[request.request_id] = fingerprint
        try:
            session.task = asyncio.create_task(
                session.runtime.run_async(
                    message=request.message, context=request.context, receipts=self._receipts(session)
                )
            )
            reply, proposal = await asyncio.wait_for(session.task, timeout=self.TURN_TIMEOUT_SECONDS)
            turn = TreeAssistantTurn(
                request_id=request.request_id,
                message=request.message,
                reply=reply,
                proposals=[proposal] if proposal is not None else [],
            )
            session.public.turns.append(turn)
            session.latest_revision = request.context.revision
            session.target = (request.context.target_registry_name, request.context.target_identifier_hash)
            return turn.model_copy(deep=True)
        except Exception as exc:
            logger.warning("Tree assistant turn failed (%s)", type(exc).__name__)
            raise TreeAssistantError(
                status=503,
                detail="Assistant turn failed or timed out. No proposal was retained and no action was applied.",
            ) from None
        finally:
            session.busy = False
            session.task = None
            session.last_used = self._clock()

    async def record_result_async(
        self, *, session_id: str, owner: str | None, proposal_id: str, receipt: TreeAssistantReceipt
    ) -> TreeAssistantProposal:
        """
        Record a human/browser receipt idempotently, without any model invocation.

        Returns:
            TreeAssistantProposal: The proposal with its human-reported outcome.
        """
        session = await self._get_async(session_id=session_id, owner=owner)
        self._require_idle(session)
        proposal = next(
            (proposal for turn in session.public.turns for proposal in turn.proposals if proposal.id == proposal_id),
            None,
        )
        if proposal is None:
            raise TreeAssistantError(status=404, detail="Assistant proposal not found.")
        if proposal.result is not None:
            if proposal.result != receipt:
                raise TreeAssistantError(status=409, detail="Proposal already has a different result.")
            return proposal.model_copy(deep=True)
        if receipt.revision < proposal.base_revision:
            raise TreeAssistantError(status=409, detail="Receipt revision predates the proposal.")
        proposal.result = receipt.model_copy(deep=True)
        proposal.status = receipt.status
        session.latest_revision = max(session.latest_revision, receipt.revision)
        return proposal.model_copy(deep=True)

    async def delete_session_async(self, *, session_id: str, owner: str | None) -> None:
        """Delete an idle session and close its owned model client."""
        session = await self._get_async(session_id=session_id, owner=owner)
        self._require_idle(session)
        del self._sessions[session_id]
        await self._close_runtime_async(session.runtime)

    async def close_async(self) -> None:
        """Cancel active turns and dispose all runtimes during application shutdown."""
        sessions, self._sessions = list(self._sessions.values()), {}
        tasks = [session.task for session in sessions if session.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for session in sessions:
            await self._close_runtime_async(session.runtime)

    async def _expire_async(self) -> None:
        expired = [
            key
            for key, session in self._sessions.items()
            if not session.busy and self._clock() - session.last_used >= self.TTL_SECONDS
        ]
        sessions = [self._sessions.pop(key) for key in expired]
        for session in sessions:
            await self._close_runtime_async(session.runtime)

    async def _get_async(self, *, session_id: str, owner: str | None) -> _Session:
        await self._expire_async()
        session = self._sessions.get(session_id)
        if session is None or session.owner != owner:
            raise TreeAssistantError(status=404, detail="Assistant session not found or expired.")
        session.last_used = self._clock()
        return session

    def _validate_message(self, *, session: _Session, request: TreeAssistantMessage) -> None:
        context = request.context
        if context.workspace_id != session.public.workspace_id:
            raise TreeAssistantError(status=409, detail="Context belongs to a different workspace.")
        if context.revision < session.latest_revision:
            raise TreeAssistantError(status=409, detail="Context revision is stale.")
        target = (context.target_registry_name, context.target_identifier_hash)
        if session.target is not None and session.target != target:
            raise TreeAssistantError(status=409, detail="Workspace target changed; create a new assistant session.")
        if len(session.public.turns) >= self.MAX_TURNS:
            raise TreeAssistantError(status=429, detail="Assistant turn limit reached; create a new session.")
        if request.request_id not in session.fingerprints and len(session.fingerprints) >= self.MAX_REQUEST_IDS:
            raise TreeAssistantError(status=429, detail="Assistant request limit reached; create a new session.")

    @staticmethod
    def _require_idle(session: _Session) -> None:
        if session.busy:
            raise TreeAssistantError(
                status=409, detail="An assistant turn is already in progress; recover it with GET."
            )

    @staticmethod
    def _receipts(session: _Session) -> list[dict[str, Any]]:
        return [
            {"proposal_id": proposal.id, **proposal.result.model_dump()}
            for turn in session.public.turns
            for proposal in turn.proposals
            if proposal.result is not None
        ]

    @staticmethod
    async def _close_runtime_async(runtime: AssistantRuntime) -> None:
        try:
            await runtime.close_async()
        except Exception as exc:
            logger.warning("Tree assistant cleanup failed (%s)", type(exc).__name__)
