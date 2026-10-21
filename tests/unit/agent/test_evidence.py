# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import io
import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pyrit.agent.evidence import AgentEvidenceExporter, EvidenceBudgetError, EvidenceNotSettledError
from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.routes import executions
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    Message,
    MessagePiece,
    MessageScorable,
    ScoringExpectation,
    ToolCallRequirement,
    ToolExecutionMetadata,
    ToolsCalled,
)
from pyrit.models.agent_evidence import AgentEvidenceSnapshot, EvidenceReadiness
from pyrit.models.agent_execution import (
    AgentEventPage,
    AgentExecution,
    AgentExecutionEvent,
    AgentExecutionState,
    AgentProfile,
    AgentTurn,
    AgentTurnStatus,
)
from pyrit.score import MessageToolCallScorer

pytestmark = pytest.mark.usefixtures("patch_central_database")


async def prepare_async(*, root: Path, memory: SQLiteMemory) -> tuple[AgentEvidenceExporter, AgentExecution]:
    manager = AgentExecutionManager(root=root)
    conversation = str(uuid4())
    request = MessagePiece(role="user", original_value="Read order", conversation_id=conversation)
    turn = AgentTurn(
        request_id=str(request.id), prompt="Read order", status=AgentTurnStatus.COMPLETED, capture_complete=True
    )
    record = AgentExecution(
        owner_id="test",
        target_id="target-hash",
        conversation_id=conversation,
        profile=AgentProfile(environment="docker", image="test"),
        turns=[turn],
        state=AgentExecutionState.CLOSED,
        event_count=3,
        artifacts=["receipt.json"],
    )
    manager.records[record.id] = record
    await manager.store.save_async(record)
    for sequence, payload in enumerate(
        [
            {
                "method": "session/update",
                "params": {
                    "update": {"sessionUpdate": "tool_call", "toolCallId": "one", "title": "<script>bad()</script>"}
                },
            },
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "one",
                        "status": "completed",
                        "rawOutput": 31,
                    }
                },
            },
            {"type": "turn_finished", "status": "completed"},
        ],
        1,
    ):
        await manager.store.append_async(
            AgentExecutionEvent(
                execution_id=record.id,
                turn_id=turn.id,
                sequence=sequence,
                payload=payload,
                direction="lifecycle" if sequence == 3 else "incoming",
            )
        )
    artifact = manager.store.directory(record.id) / "artifacts" / "receipt.json"
    await asyncio.to_thread(artifact.parent.mkdir)
    await asyncio.to_thread(artifact.write_bytes, b'{"total":31}')
    pieces = [
        request,
        MessagePiece(
            role="assistant",
            original_value='{"call_id":"one","name":"read","arguments":"{}"}',
            original_value_data_type="function_call",
            conversation_id=conversation,
        ),
        MessagePiece(
            role="tool",
            original_value='{"call_id":"one","output":31}',
            original_value_data_type="function_call_output",
            conversation_id=conversation,
            prompt_metadata=ToolExecutionMetadata(invoked=True).to_metadata(),
        ),
    ]
    for piece in pieces:
        await memory.add_message_to_memory_async(request=piece.to_message())
    await MessageToolCallScorer().score_async(
        scorable=MessageScorable.from_message(pieces[-1].to_message()),
        expectation=ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="read"),)),)),
    )
    return AgentEvidenceExporter(manager=manager, memory=memory), record


async def test_package_has_exact_file_hashes_scope_and_safe_offline_recording_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    before = record.model_dump_json()
    with patch.object(exporter.manager, "send_async", new_callable=AsyncMock) as send:
        data = await exporter.archive_async(record.id)
    send.assert_not_awaited()
    assert record.model_dump_json() == before
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert set(archive.namelist()) == {
            "manifest.json",
            "recording.html",
            "execution.json",
            "events.jsonl",
            "messages.json",
            "scores.json",
            "artifacts/receipt.json",
        }
        assert manifest["execution_id"] == str(record.id)
        assert manifest["conversation_id"] == record.conversation_id
        assert manifest["target_id"] == record.target_id
        assert manifest["event_cursor"] == 3
        for entry in manifest["files"]:
            contents = archive.read(entry["path"])
            assert len(contents) == entry["bytes"]
            assert hashlib.sha256(contents).hexdigest() == entry["sha256"]
        assert json.loads(archive.read("artifacts/receipt.json")) == {"total": 31}
        assert len(json.loads(archive.read("messages.json"))) == 3
        assert json.loads(archive.read("scores.json"))[0]["score_value"] == "true"
        page = archive.read("recording.html").decode()
        assert "<script>bad()" not in page and "&lt;script&gt;bad()" in page
        assert "default-src 'none'" in page
        assert manifest["assessment"]["recreate"]["status"] == "unverified"
        assert manifest["assessment"]["resume"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "state",
    [
        AgentExecutionState.IDLE,
        AgentExecutionState.WORKING,
        AgentExecutionState.CLOSING,
        AgentExecutionState.CLEANUP_FAILED,
    ],
)
async def test_export_does_not_close_or_extend_live_executions_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
    state: AgentExecutionState,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    record.state = state
    with patch.object(exporter.manager, "close_execution_async", new_callable=AsyncMock) as close:
        with pytest.raises(EvidenceNotSettledError, match="Close the execution"):
            await exporter.archive_async(record.id)
    close.assert_not_awaited()
    assert record.state == state


async def test_partial_capture_and_missing_artifacts_are_explicit_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    record.capture_error = "connection interrupted"
    record.artifacts.append("absent.json")
    data = await exporter.archive_async(record.id)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["assessment"]["playback"]["status"] == "partial"
        assert any("absent.json" in omission for omission in manifest["omissions"])
        assert "artifacts/absent.json" not in archive.namelist()


@pytest.mark.parametrize("relative", ["../private", "/absolute", "C:/private", "nested\\private", "Receipt.json"])
async def test_invalid_or_colliding_artifact_paths_fail_explicitly_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
    relative: str,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    record.artifacts.append(relative)
    with pytest.raises(ValueError, match="artifact path"):
        await exporter.archive_async(record.id)


async def test_linked_artifact_not_followed_async(tmp_path: Path, sqlite_instance: SQLiteMemory) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    original = Path.is_symlink
    with patch.object(Path, "is_symlink", lambda path: path.name == "receipt.json" or original(path)):
        data = await exporter.archive_async(record.id)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "artifacts/receipt.json" not in archive.namelist()
        assert any("receipt.json" in text for text in json.loads(archive.read("manifest.json"))["omissions"])


async def test_corrupt_journal_and_budget_fail_instead_of_partial_success_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    record.event_count = 4
    with pytest.raises(ValueError, match="ends before"):
        await exporter.snapshot_async(record.id)
    record.event_count = 3
    with patch.object(exporter, "MAX_BYTES", 100), pytest.raises(EvidenceBudgetError):
        await exporter.archive_async(record.id)
    with patch.object(exporter, "MAX_EVENTS", 1), pytest.raises(EvidenceBudgetError):
        await exporter.snapshot_async(record.id)


async def test_snapshot_detects_concurrent_execution_revision_change_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    original = exporter.memory.get_conversation_messages_async

    async def read_async(*, conversation_id: str) -> list[Message]:
        record.transcript_revision += 1
        return await original(conversation_id=conversation_id)

    with patch.object(exporter.memory, "get_conversation_messages_async", side_effect=read_async):
        with pytest.raises(EvidenceNotSettledError, match="changed"):
            await exporter.snapshot_async(record.id)


async def test_event_cursor_excludes_later_frames_and_foreign_memory_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    await exporter.manager.store.append_async(
        AgentExecutionEvent(
            execution_id=record.id,
            sequence=4,
            direction="stderr",
            payload={"text": "after cutoff"},
        )
    )
    await sqlite_instance.add_message_to_memory_async(
        request=MessagePiece(
            role="user",
            original_value="foreign",
            conversation_id=str(uuid4()),
        ).to_message()
    )
    snapshot = await exporter.snapshot_async(record.id)
    assert [event.sequence for event in snapshot.events] == [1, 2, 3]
    assert not any(message.get_value() == "foreign" for message in snapshot.messages)
    assert snapshot.assessment.rescore.status == EvidenceReadiness.PARTIAL


async def test_archive_enforces_exact_inclusive_byte_and_file_limits_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    snapshot = await exporter.snapshot_async(record.id)
    data = await asyncio.to_thread(exporter._archive, snapshot)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = sum(entry.file_size for entry in archive.infolist())
    with patch.object(exporter, "MAX_BYTES", total):
        assert await asyncio.to_thread(exporter._archive, snapshot)
    with patch.object(exporter, "MAX_BYTES", total - 1), pytest.raises(EvidenceBudgetError):
        await asyncio.to_thread(exporter._archive, snapshot)
    with patch.object(exporter, "MAX_FILES", 0), pytest.raises(EvidenceBudgetError, match="Too many"):
        await asyncio.to_thread(exporter._archive, snapshot)


async def test_missing_entire_journal_prefix_fails_without_looping_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    page = await exporter.manager.store.events_async(execution_id=record.id)
    page.events[0].sequence = 4
    with patch.object(exporter.manager.store, "events_async", new_callable=AsyncMock, return_value=page):
        with pytest.raises(ValueError, match="ends before"):
            await exporter.snapshot_async(record.id)


async def test_cancellation_joins_archive_worker_before_releasing_admission_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def archive(snapshot: AgentEvidenceSnapshot) -> bytes:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5)
        return b"package"

    with patch.object(exporter, "_archive", side_effect=archive):
        task = asyncio.create_task(exporter.archive_async(record.id))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_cancelled_snapshot_requests_hold_capacity_until_journal_workers_finish_async(
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    original = exporter.manager.store._events
    loop = asyncio.get_running_loop()
    started: asyncio.Queue[None] = asyncio.Queue()
    release = threading.Event()
    slots = asyncio.Semaphore(2)

    def read_events(*, execution_id: UUID, after: int, limit: int) -> AgentEventPage:
        loop.call_soon_threadsafe(started.put_nowait, None)
        assert release.wait(timeout=5)
        return original(execution_id=execution_id, after=after, limit=limit)

    with (
        patch.object(executions, "_evidence_slots", slots),
        patch.object(executions, "get_agent_execution_manager", return_value=exporter.manager),
        patch.object(exporter.manager, "start_async", new_callable=AsyncMock),
        patch.object(exporter.manager.store, "_events", side_effect=read_events),
    ):
        tasks = [
            asyncio.create_task(executions._evidence_async(execution_id=record.id, archive=False)) for _ in range(2)
        ]
        try:
            for _ in tasks:
                await asyncio.wait_for(started.get(), timeout=5)
            for task in tasks:
                task.cancel()
            await asyncio.sleep(0)
            assert slots.locked()
            with pytest.raises(HTTPException) as rejected:
                await executions._evidence_async(execution_id=record.id, archive=False)
            assert rejected.value.status_code == 429
        finally:
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert not slots.locked()


def test_evidence_routes_require_admin() -> None:
    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(executions.router)
    with TestClient(app) as client:
        for ending in ("evidence", "evidence.zip"):
            assert client.get(f"/executions/{uuid4()}/{ending}").status_code == 403


async def test_evidence_api_download_and_status_errors_async(tmp_path: Path, sqlite_instance: SQLiteMemory) -> None:
    exporter, record = await prepare_async(root=tmp_path, memory=sqlite_instance)
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[require_admin] = lambda: None
    with (
        patch.object(executions, "get_agent_execution_manager", return_value=exporter.manager),
        patch.object(exporter.manager, "start_async", new_callable=AsyncMock),
    ):
        import httpx

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/executions/{record.id}/evidence.zip")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/zip"
            assert response.headers["cache-control"] == "no-store"
            assert zipfile.is_zipfile(io.BytesIO(response.content))
            snapshot = await client.get(f"/executions/{record.id}/evidence")
            assert snapshot.json()["event_cursor"] == 3
            record.state = AgentExecutionState.IDLE
            assert (await client.get(f"/executions/{record.id}/evidence.zip")).status_code == 409
            assert (await client.get(f"/executions/{uuid4()}/evidence")).status_code == 404
            record.state = AgentExecutionState.CLOSED
            with patch.object(AgentEvidenceExporter, "MAX_EVENTS", 0):
                assert (await client.get(f"/executions/{record.id}/evidence")).status_code == 413
            with patch.object(executions, "_evidence_slots", asyncio.Semaphore(0)):
                assert (await client.get(f"/executions/{record.id}/evidence")).status_code == 429
            with patch.object(
                AgentEvidenceExporter,
                "snapshot_async",
                new_callable=AsyncMock,
                side_effect=ValueError("private corruption detail"),
            ):
                response = await client.get(f"/executions/{record.id}/evidence")
                assert response.status_code == 500
                assert "private corruption detail" not in response.text
