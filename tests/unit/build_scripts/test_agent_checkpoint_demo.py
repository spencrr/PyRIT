# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

pytest.importorskip("acp")

from build_scripts.agent_checkpoint_demo import CheckpointOracle, CheckpointProbe
from pyrit.agent.environment import ExecutionEnvironment
from pyrit.agent.inference_relay import InferenceLease
from pyrit.agent.model_binding import ResolvedModelBinding
from pyrit.models.agent_execution import AgentExecution, AgentProfile
from pyrit.models.model_inference import InferenceRequirements

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def probe(tmp_path: Path) -> CheckpointProbe:
    return CheckpointProbe(
        root=tmp_path / "proof",
        harness="opencode",
        image="fixture",
        relay_host="127.0.0.1",
        advertised_host="host.docker.internal",
    )


def archive_bytes(
    *,
    name: str = "workspace/receipt.json",
    data: bytes = b'{"total":31}',
    kind: bytes = tarfile.REGTYPE,
    pax_headers: dict[str, str] | None = None,
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.uid, entry.gid = 1000, 1001
        entry.mode = 0o600
        entry.linkname = "/private" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
        entry.pax_headers = pax_headers or {}
        entry.size = len(data) if kind == tarfile.REGTYPE else 0
        archive.addfile(entry, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return stream.getvalue()


def test_portable_archive_preserves_owned_state_without_host_extraction(probe: CheckpointProbe) -> None:
    data = probe._portable_archive(
        raw=archive_bytes(name="opencode/db", pax_headers={"path": "opencode/db"}),
        root=probe.STATE_ROOTS["opencode"],
    )
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        member = archive.getmembers()[0]
        assert member.name == "home/agent/.local/share/opencode/db"
        assert (member.uid, member.gid, member.mode) == (1000, 1001, 0o600)
        stream = archive.extractfile(member)
        assert stream is not None and stream.read() == b'{"total":31}'


@pytest.mark.parametrize(
    ("harness", "parents"),
    [
        ("opencode", ["home/agent/.local", "home/agent/.local/share"]),
        ("copilot", ["home/agent/.copilot"]),
    ],
)
def test_fresh_native_state_parents_belong_to_the_image_user(
    probe: CheckpointProbe,
    harness: str,
    parents: list[str],
) -> None:
    probe.harness = harness
    data = probe._state_parent_archive(uid=1000, gid=1001)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getnames() == parents
        assert all(entry.isdir() and (entry.uid, entry.gid, entry.mode) == (1000, 1001, 0o700) for entry in archive)


@pytest.mark.parametrize("name", ["/workspace/x", "workspace/../x", "workspace\\x", "workspace/C:x", "other/x"])
def test_checkpoint_rejects_escaped_roots(probe: CheckpointProbe, name: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        probe._portable_archive(raw=archive_bytes(name=name), root="/workspace")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_checkpoint_rejects_links_and_special_files(probe: CheckpointProbe, kind: bytes) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        probe._portable_archive(raw=archive_bytes(kind=kind), root="/workspace")


def test_checkpoint_enforces_inclusive_byte_and_file_limits(probe: CheckpointProbe) -> None:
    raw = archive_bytes()
    with patch.object(probe, "MAX_ARCHIVE_BYTES", len(raw)), patch.object(probe, "MAX_ARCHIVE_FILES", 1):
        assert probe._portable_archive(raw=raw, root="/workspace")
    with patch.object(probe, "MAX_ARCHIVE_BYTES", len(raw) - 1), pytest.raises(ValueError, match="byte budget"):
        probe._portable_archive(raw=raw, root="/workspace")
    with patch.object(probe, "MAX_ARCHIVE_FILES", 0), pytest.raises(ValueError, match="entry budget"):
        probe._portable_archive(raw=raw, root="/workspace")


def test_relay_credentials_are_not_checkpoint_data(probe: CheckpointProbe) -> None:
    lease = InferenceLease(
        binding=ResolvedModelBinding(
            target=probe.oracle,
            identifier_hash=probe.oracle.get_identifier().hash,
            model="test",
            requirements=InferenceRequirements(),
        ),
        record=AsyncMock(),
    )
    probe.leases.append(lease)
    with pytest.raises(ValueError, match="relay credential"):
        probe._portable_archive(raw=archive_bytes(data=lease.token.encode()), root="/workspace")


def test_saved_archive_integrity_is_checked_before_use(tmp_path: Path) -> None:
    path = tmp_path / "state.tar"
    data = archive_bytes()
    path.write_bytes(data)
    expected = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert CheckpointProbe._read_saved(path=path, expected=expected) == data
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ValueError, match="changed"):
        CheckpointProbe._read_saved(path=path, expected=expected)


async def test_existing_output_directory_is_not_owned_or_changed_async(probe: CheckpointProbe) -> None:
    await asyncio.to_thread(probe.root.mkdir)
    with pytest.raises(FileExistsError):
        await probe.run_async()
    assert not probe.owns_output


@pytest.mark.parametrize(
    "state",
    [
        {"Running": True, "ExitCode": 0, "OOMKilled": False},
        {"Running": False, "ExitCode": 137, "OOMKilled": False},
        {"Running": False, "ExitCode": 0, "OOMKilled": True},
    ],
)
async def test_unsettled_or_force_killed_process_does_not_produce_checkpoint_async(
    probe: CheckpointProbe,
    state: dict[str, object],
) -> None:
    environment = ExecutionEnvironment(
        execution=AgentExecution(
            owner_id="test", target_id="target", conversation_id="c", profile=AgentProfile(image="test")
        ),
        directory=probe.root,
    )
    with (
        patch.object(
            environment, "_inspect_owned_container_async", new_callable=AsyncMock, side_effect=[{}, {"State": state}]
        ),
        patch.object(environment, "_docker_async", new_callable=AsyncMock),
        patch.object(probe.relay, "revoke_async", new_callable=AsyncMock) as revoke,
    ):
        with pytest.raises(RuntimeError, match="stop gracefully"):
            await probe._stop_async(environment)
    revoke.assert_not_awaited()


@pytest.mark.parametrize(
    ("binding", "destination"),
    [
        ("0.0.0.0", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("192.0.2.10", "192.0.2.10"),
    ],
)
async def test_revocation_probe_uses_configured_local_interface_without_ambient_proxy_async(
    probe: CheckpointProbe,
    binding: str,
    destination: str,
) -> None:
    probe.relay.host = binding
    probe.relay.url = "http://host.docker.internal:12345/v1"
    lease = InferenceLease(
        binding=ResolvedModelBinding(
            target=probe.oracle,
            identifier_hash="test",
            model="test",
            requirements=InferenceRequirements(),
        ),
        record=AsyncMock(),
    )
    probe.leases.append(lease)
    environment = ExecutionEnvironment(
        execution=AgentExecution(
            owner_id="test", target_id="target", conversation_id="c", profile=AgentProfile(image="test")
        ),
        directory=probe.root,
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch.object(httpx, "AsyncClient", return_value=client) as constructor,
        patch.object(
            environment,
            "_inspect_owned_container_async",
            new_callable=AsyncMock,
            side_effect=[{}, {"State": {"Running": False, "ExitCode": 0, "OOMKilled": False}}],
        ),
        patch.object(environment, "_docker_async", new_callable=AsyncMock),
        patch.object(probe.relay, "revoke_async", new_callable=AsyncMock) as revoke,
    ):
        await probe._stop_async(environment)
    assert requests[0].url == f"http://{destination}:12345/v1/chat/completions"
    assert constructor.call_args.kwargs["trust_env"] is False
    revoke.assert_awaited_once()


def continuation(oracle: CheckpointOracle) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "SEED_CHECKPOINT"},
            {"role": "tool", "content": '{"total":31,"writes":1}'},
            {"role": "assistant", "content": oracle.history_token},
            {"role": "user", "content": "CONTINUE_CHECKPOINT"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }


def test_oracle_requires_native_history_not_just_a_plausible_reply(probe: CheckpointProbe) -> None:
    oracle = probe.oracle
    oracle.phase = "paired"
    body = continuation(oracle)
    reply, finish = oracle._reply(body)
    assert finish == "tool_calls"
    assert json.loads(reply["tool_calls"][0]["function"]["arguments"])["command"].startswith("node -e ")
    assert oracle.history_token not in json.dumps(reply)
    body["messages"] = body["messages"][-1:]
    with pytest.raises(ValueError, match="assistant-only token"):
        oracle._reply(body)


def test_oracle_requires_original_tool_result_and_current_filesystem_result(probe: CheckpointProbe) -> None:
    oracle = probe.oracle
    oracle.phase = "paired"
    body = continuation(oracle)
    body["messages"].pop(1)
    with pytest.raises(ValueError, match="original tool result"):
        oracle._reply(body)
    body = continuation(oracle)
    body["messages"].append({"role": "tool", "content": "ENOENT: receipt.json"})
    assert oracle._reply(body) == ({"role": "assistant", "content": "WORKSPACE_NOT_RESTORED"}, "stop")
