# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded, read-only snapshots and evidence archives for settled executions."""

import asyncio
import hashlib
import html
import io
import json
import logging
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_evidence import (
    AgentEvidenceManifest,
    AgentEvidenceSnapshot,
    AgentReplayAssessment,
    EvidenceFile,
    EvidenceOperation,
    EvidenceReadiness,
)
from pyrit.models.agent_execution import AgentExecution, AgentExecutionState, AgentTurnStatus

if TYPE_CHECKING:
    from pyrit.agent.execution_manager import AgentExecutionManager
    from pyrit.memory import MemoryInterface

logger = logging.getLogger(__name__)


class EvidenceNotSettledError(ValueError):
    """The selected execution changed or has not finished releasing its resources."""


class EvidenceBudgetError(ValueError):
    """The requested evidence cannot be represented within the export budget."""


class AgentEvidenceExporter:
    """Capture journal and stored evidence without launching, extending, or closing executions."""

    MAX_BYTES = 64 * 1024 * 1024
    MAX_EVENTS = 10000
    MAX_FILES = 1000

    def __init__(self, *, manager: "AgentExecutionManager", memory: "MemoryInterface") -> None:
        """Use caller-owned sources; exporting never assumes ownership of the runtime."""
        self.manager = manager
        self.memory = memory

    async def snapshot_async(self, execution_id: UUID) -> AgentEvidenceSnapshot:
        """
        Capture a closed execution through its recorded cursor.

        Returns:
            AgentEvidenceSnapshot: Retained events and current stored message/score data.

        Raises:
            EvidenceNotSettledError: Resources/turns are active or the record changes during acquisition.
            EvidenceBudgetError: The evidence exceeds a configured export ceiling.
            ValueError: Journal identity or sequence integrity is invalid.
        """
        return await await_task_completion_async(asyncio.create_task(self._snapshot_async(execution_id)))

    async def _snapshot_async(self, execution_id: UUID) -> AgentEvidenceSnapshot:
        record = self.manager.records[execution_id].model_copy(deep=True)
        self._require_settled(record)
        if record.evidence_bytes > self.MAX_BYTES or record.event_count > self.MAX_EVENTS:
            raise EvidenceBudgetError("Execution exceeds the evidence export limit (64 MiB or 10,000 events)")
        events = []
        cursor, size = 0, 0
        while cursor < record.event_count:
            page = await self.manager.store.events_async(execution_id=execution_id, after=cursor, limit=500)
            if not page.events:
                raise ValueError("Execution journal ends before its recorded event cursor")
            for event in page.events:
                if event.sequence > record.event_count:
                    if cursor != record.event_count:
                        raise ValueError("Execution journal ends before its recorded event cursor")
                    break
                if event.execution_id != execution_id or event.sequence != cursor + 1:
                    raise ValueError("Execution journal identity or sequence gap prevents a complete snapshot")
                if event.turn_id is not None and not any(turn.id == event.turn_id for turn in record.turns):
                    raise ValueError("Execution journal refers to an unknown turn")
                size += len(event.model_dump_json().encode("utf-8"))
                if size > self.MAX_BYTES:
                    raise EvidenceBudgetError("Execution journal exceeds the export byte limit")
                events.append(event)
                cursor = event.sequence
        messages = list(await self.memory.get_conversation_messages_async(conversation_id=record.conversation_id))
        if any(
            piece.conversation_id != record.conversation_id for message in messages for piece in message.message_pieces
        ):
            raise ValueError("Memory returned messages outside the execution conversation")
        piece_ids = {piece.id for message in messages for piece in message.message_pieces}
        scores = list(await self.memory.get_prompt_scores_async(conversation_id=record.conversation_id))
        if any(score.message_piece_id not in piece_ids for score in scores):
            raise EvidenceNotSettledError("Conversation evidence changed while scores were acquired; retry export")
        omissions = [
            (
                "External observation/trace payloads and message attachment bytes are not embedded; "
                "their stored references remain."
            ),
            "Provider credentials, runtime home directories, and unretained workspace files are not collected.",
        ]
        if not messages:
            omissions.append("No stored conversation messages were available; the journal is still retained.")
        missing_requests = [turn for turn in record.turns if turn.request_id not in {str(value) for value in piece_ids}]
        if missing_requests:
            omissions.append(
                f"{len(missing_requests)} recorded turn request(s) are absent from the current conversation."
            )
        if record.capture_error:
            omissions.append("ACP capture was incomplete: " + record.capture_error)
        incomplete_turns = sum(not turn.capture_complete for turn in record.turns)
        if incomplete_turns:
            omissions.append(f"{incomplete_turns} turn(s) have incomplete ACP capture.")
        omissions.extend(record.artifact_errors)
        snapshot = AgentEvidenceSnapshot(
            execution=record,
            event_cursor=record.event_count,
            events=events,
            messages=messages,
            scores=scores,
            assessment=self._assess(record=record, has_messages=bool(messages)),
            omissions=omissions,
        )
        if len(snapshot.model_dump_json().encode("utf-8")) > self.MAX_BYTES:
            raise EvidenceBudgetError("Combined execution and conversation evidence exceeds the export byte limit")
        if self.manager.records[execution_id] != record:
            raise EvidenceNotSettledError("Execution changed during acquisition; retry export")
        return snapshot

    async def archive_async(self, execution_id: UUID) -> bytes:
        """
        Package a stable journal snapshot and only explicitly retained regular artifacts.

        Returns:
            bytes: ZIP containing a manifest, machine-readable evidence, and an offline HTML record.
        """
        snapshot = await self.snapshot_async(execution_id)
        return await await_task_completion_async(asyncio.create_task(asyncio.to_thread(self._archive, snapshot)))

    @staticmethod
    def _require_settled(record: AgentExecution) -> None:
        if record.state != AgentExecutionState.CLOSED or any(
            turn.status == AgentTurnStatus.RUNNING for turn in record.turns
        ):
            raise EvidenceNotSettledError("Close the execution and wait for its turns to settle before exporting")
        if any(approval.decision is None for approval in record.approvals):
            raise EvidenceNotSettledError("Execution still has unresolved permissions; evidence is not settled")

    @staticmethod
    def _assess(*, record: AgentExecution, has_messages: bool) -> AgentReplayAssessment:
        incomplete = record.capture_error is not None or any(not turn.capture_complete for turn in record.turns)
        return AgentReplayAssessment(
            playback=EvidenceOperation(
                status=EvidenceReadiness.PARTIAL if incomplete else EvidenceReadiness.AVAILABLE,
                detail="Read-only playback of retained ACP events; internal harness coverage is not guaranteed.",
            ),
            rescore=EvidenceOperation(
                status=EvidenceReadiness.PARTIAL if has_messages else EvidenceReadiness.UNAVAILABLE,
                detail="Stored messages can be re-scored with compatible PyRIT scorers. "
                "External observations, model judges, "
                "and missing tool names may require additional sources; no automatic score replay is implied.",
            ),
            recreate=EvidenceOperation(
                status=EvidenceReadiness.UNVERIFIED,
                detail="Configuration is recorded, but image/binary availability, fixture contents, external state, "
                "credentials, and model behavior are not revalidated. A new run has new tool effects.",
            ),
            resume=EvidenceOperation(
                status=EvidenceReadiness.UNAVAILABLE,
                detail="No coordinated harness/environment checkpoint was captured. "
                "Copying configuration starts fresh.",
            ),
        )

    def _archive(self, snapshot: AgentEvidenceSnapshot) -> bytes:
        record = snapshot.execution
        files = {
            "execution.json": record.model_dump_json(indent=2).encode("utf-8"),
            "events.jsonl": b"".join((event.model_dump_json() + "\n").encode("utf-8") for event in snapshot.events),
            "messages.json": self._json([message.model_dump(mode="json") for message in snapshot.messages]),
            "scores.json": self._json([score.model_dump(mode="json") for score in snapshot.scores]),
            "recording.html": self._html(snapshot).encode("utf-8"),
        }
        size = sum(len(data) for data in files.values())
        omissions = list(snapshot.omissions)
        if len(record.artifacts) > self.MAX_FILES:
            raise EvidenceBudgetError("Too many retained artifacts to export")
        root = self.manager.store.directory(record.id) / "artifacts"
        names: set[str] = set()
        for relative in record.artifacts:
            path = PurePosixPath(relative)
            if (
                not relative
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in relative
                or ":" in relative
                or relative in (".", "")
                or str(path).casefold() in names
            ):
                raise ValueError("Invalid or colliding retained artifact path")
            names.add(str(path).casefold())
            try:
                data = self._read_artifact(root=root, relative=path, maximum=record.profile.max_artifact_bytes)
            except (OSError, ValueError) as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                omissions.append(f"Artifact {relative} was not included: {reason}")
                logger.warning("Could not include retained artifact %s for %s: %s", relative, record.id, reason)
                continue
            size += len(data)
            if size > self.MAX_BYTES:
                raise EvidenceBudgetError("Evidence package exceeds the total export limit")
            files[f"artifacts/{path.as_posix()}"] = data
        if size > self.MAX_BYTES:
            raise EvidenceBudgetError("Evidence package exceeds the total export limit")
        manifest = AgentEvidenceManifest(
            execution_id=record.id,
            conversation_id=record.conversation_id,
            target_id=record.target_id,
            captured_at=snapshot.captured_at,
            event_cursor=snapshot.event_cursor,
            source_coverage=record.source_coverage,
            assessment=snapshot.assessment,
            omissions=omissions,
            files=[
                EvidenceFile(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                for name, data in files.items()
            ],
        )
        manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
        if size + len(manifest_bytes) > self.MAX_BYTES:
            raise EvidenceBudgetError("Evidence manifest and files exceed the total export limit")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for name, data in files.items():
                archive.writestr(name, data)
        if buffer.tell() > self.MAX_BYTES:
            raise EvidenceBudgetError("Compressed evidence package exceeds the download limit")
        return buffer.getvalue()

    @staticmethod
    def _read_artifact(*, root: Path, relative: PurePosixPath, maximum: int) -> bytes:
        candidate = root.parent
        for part in ("", root.name, *relative.parts):
            candidate = candidate / part
            info = candidate.lstat()
            if candidate.is_symlink() or bool(
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Artifact path contains a symbolic link or junction")
        if not candidate.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise ValueError("Artifact escapes retained artifact directory")
        if not candidate.is_file() or candidate.stat().st_size > maximum:
            raise ValueError("Artifact is not a bounded regular file")
        with candidate.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Artifact exceeded its retained size limit")
        return data

    @staticmethod
    def _json(value: Any) -> bytes:
        return json.dumps(value, indent=2, ensure_ascii=True).encode("utf-8")

    @staticmethod
    def _html(snapshot: AgentEvidenceSnapshot) -> str:
        events = "".join(
            f"<details><summary>{event.sequence} | {html.escape(event.direction)} | "
            f"{html.escape(str(event.payload.get('method', event.payload.get('type', 'response'))))}</summary>"
            f"<pre>{html.escape(json.dumps(event.payload, indent=2, ensure_ascii=True))}</pre></details>"
            for event in snapshot.events
        )
        limitations = "".join(f"<li>{html.escape(value)}</li>" for value in snapshot.omissions)
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>PyRIT recorded execution</title><style>body{font-family:system-ui;max-width:70rem;margin:2rem auto}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:1rem 0}</style>"
            f"<h1>Recorded execution {snapshot.execution.id}</h1>"
            f"<p>Through event {snapshot.event_cursor}. Read-only retained reports; no tools run.</p>"
            "<p>Use manifest.json for file integrity, scope, and artifact omissions. "
            "This is not a resumable checkpoint.</p>"
            f"<ul>{limitations}</ul>{events}</html>"
        )
