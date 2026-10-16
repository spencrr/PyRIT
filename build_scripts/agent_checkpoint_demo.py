# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Disposable Docker/ACP restoration proof, not a production execution-resume API."""

import argparse
import asyncio
import hashlib
import io
import json
import secrets
import shlex
import tarfile
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx
from acp import RequestError
from acp.schema import ClientCapabilities, Implementation

from pyrit.agent.acp_connection import AcpConnection, RecordingTransport
from pyrit.agent.environment import AcpByteReader, CheckpointRestore, ExecutionSpec
from pyrit.agent.environments.docker import DockerSession
from pyrit.agent.harness import HarnessAdapterRegistry, inference_environment, launch_environment
from pyrit.agent.inference_relay import InferenceLease, InferenceRelay
from pyrit.agent.model_binding import ResolvedModelBinding
from pyrit.models.agent_execution import AgentExecution, AgentPermissionPolicy, AgentProfile
from pyrit.models.model_inference import InferenceCapabilities, InferenceRequirements, InferenceWireApi
from pyrit.prompt_target import TextTarget
from pyrit.prompt_target.common.model_inference import InferenceResponse
from pyrit.setup import IN_MEMORY, initialize_pyrit_async


class CheckpointOracle(TextTarget):
    """Synthetic inference: require native history and verify real tool effects, without a provider call."""

    MAX_CALLS = 12

    def __init__(self, *, history_token: str) -> None:
        """Configure synthetic expectations, never inject missing history into the harness."""
        super().__init__()
        self.history_token = history_token
        self.calls: list[dict[str, Any]] = []
        self.phase = "seed"

    @property
    def inference_capabilities(self) -> InferenceCapabilities:
        """The synthetic model's harness-owned tool-loop contract."""
        return InferenceCapabilities(
            wire_apis=frozenset({InferenceWireApi.CHAT_COMPLETIONS}),
            streaming=True,
            tool_calls=True,
            input_modalities=frozenset({"text"}),
        )

    def _reply(self, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        messages = body.get("messages", [])
        users = [i for i, message in enumerate(messages) if message.get("role") == "user"]
        if not users:
            raise ValueError("Synthetic inference requires a user prompt")
        current = users[-1]
        prompt = json.dumps(messages[current].get("content"))
        # Harness title/summary requests are not the conversation continuation being tested.
        if not body.get("tools"):
            return {"role": "assistant", "content": "Checkpoint probe"}, "stop"
        if self.phase == "seed" and "SEED_CHECKPOINT" not in prompt:
            raise ValueError("Unexpected seed prompt")
        if self.phase != "seed":
            history = messages[:current]
            if "CONTINUE_CHECKPOINT" not in prompt:
                raise ValueError("Unexpected continuation prompt")
            if not any(
                message.get("role") == "assistant" and self.history_token in json.dumps(message.get("content"))
                for message in history
            ):
                raise ValueError("Native harness history is missing the assistant-only token")
            if not any(
                message.get("role") == "tool" and '"total":31' in json.dumps(message).replace("\\", "")
                for message in history
            ):
                raise ValueError("Native harness history is missing the original tool result")
        results = [message for message in messages[current + 1 :] if message.get("role") == "tool"]
        if results:
            serialized = json.dumps(results).replace("\\", "")
            expected = '"total":31' if self.phase == "seed" else '"total":36'
            if expected not in serialized:
                return {"role": "assistant", "content": "WORKSPACE_NOT_RESTORED"}, "stop"
            content = self.history_token if self.phase == "seed" else "CONTINUATION_VERIFIED"
            return {"role": "assistant", "content": content}, "stop"
        tools = {tool.get("function", {}).get("name") for tool in body["tools"]}
        if "bash" not in tools:
            raise ValueError("Fixture harness did not advertise its bash tool")
        if self.phase == "seed":
            script = (
                "const fs=require('fs');"
                "if(fs.existsSync('effects.log'))throw Error('seed was repeated');"
                "const r={total:31,writes:1};"
                "fs.writeFileSync('receipt.json',JSON.stringify(r));"
                "fs.appendFileSync('effects.log','seed\\n');console.log(JSON.stringify(r));"
            )
        else:
            script = (
                "const fs=require('fs');const r=JSON.parse(fs.readFileSync('receipt.json'));"
                "if(r.total!==31||r.writes!==1)throw Error('wrong checkpoint state');"
                "r.total+=5;r.writes+=1;fs.writeFileSync('receipt.json',JSON.stringify(r));"
                "fs.appendFileSync('effects.log','continued\\n');console.log(JSON.stringify(r));"
            )
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"checkpoint-{self.phase}",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps(
                            {
                                "command": "node -e " + shlex.quote(script),
                                "description": "Write and verify the disposable checkpoint receipt",
                            }
                        ),
                    },
                }
            ],
        }, "tool_calls"

    @asynccontextmanager
    async def open_inference_async(
        self,
        *,
        body: dict[str, Any],
        requirements: InferenceRequirements,
        request_id: str,
    ) -> AsyncGenerator[InferenceResponse, None]:
        """Return synthetic wire events while recording what the real harness supplied."""
        if len(self.calls) >= self.MAX_CALLS:
            raise ValueError("Checkpoint proof exceeded its synthetic inference budget")
        self.calls.append({"phase": self.phase, "request": body})
        delta, finish = self._reply(body)

        async def chunks_async() -> AsyncIterator[bytes]:
            for content, reason in ((delta, None), ({}, finish)):
                payload = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "model": "checkpoint-model",
                    "choices": [{"index": 0, "delta": content, "finish_reason": reason}],
                }
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        yield InferenceResponse(status_code=200, headers={"content-type": "text/event-stream"}, body=chunks_async())


class CheckpointProbe:
    """Own only disposable fixture executions and a credential-scoped synthetic relay."""

    MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
    MAX_ARCHIVE_FILES = 2000
    STATE_ROOTS = {"copilot": "/home/agent/.copilot/session-state"}

    def __init__(self, *, root: Path, harness: str, image: str, relay_host: str, advertised_host: str) -> None:
        """Bind explicit Docker/relay settings without launching or reading operator credentials."""
        self.root, self.harness, self.image = root, harness, image
        self.oracle = CheckpointOracle(history_token=f"history-only-{secrets.token_hex(16)}")
        self.relay = InferenceRelay(host=relay_host, advertised_host=advertised_host)
        self.events: list[dict[str, Any]] = []
        self.stderr: dict[str, str] = {}
        self.leases: list[InferenceLease] = []
        self.resources: list[AgentExecution] = []
        self.session_id = ""
        self.capabilities: dict[str, Any] = {}
        self.owns_output = False

    async def run_async(self) -> dict[str, Any]:
        """Run paired and negative-control restores; fail rather than claiming unsupported success."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=False)
        self.owns_output = True
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(self.oracle.cleanup_target_async)
            cleanup.push_async_callback(self.relay.close_async)
            await self.relay.start_async()
            source, connection = await self._launch_async(cleanup=cleanup, phase="seed")
            self.image = str(source.execution.image_id)
            self.capabilities = await connection.initialize_async(cwd=source.agent_cwd)
            self.session_id = connection.session_id or ""
            if not self.capabilities["initialize"]["agentCapabilities"].get("loadSession"):
                raise ValueError("Harness does not advertise session/load; coordinated restore remains unsupported")
            stop, response = await connection.prompt_async("SEED_CHECKPOINT: create the disposable receipt.")
            if stop != "end_turn" or response != self.oracle.history_token:
                raise ValueError("Seed did not complete with verified tool effects and native-history token")
            await self._stop_async(source)
            workspace = await self._capture_async(environment=source, path="/workspace")
            state = await self._capture_async(environment=source, path=self.STATE_ROOTS[self.harness])
            if self.oracle.history_token.encode() in workspace:
                raise ValueError("History-only token unexpectedly entered the workspace")
            archives = {
                name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in (("workspace.tar", workspace), ("harness-state.tar", state))
            }
            checkpoint = {
                "harness": self.harness,
                "image_id": self.image,
                "session_id": self.session_id,
                "workspace": "/workspace",
                "state_root": self.STATE_ROOTS[self.harness],
                "archives": archives,
                "source_execution_id": str(source.execution.id),
            }
            await asyncio.to_thread((self.root / "workspace.tar").write_bytes, workspace)
            await asyncio.to_thread((self.root / "harness-state.tar").write_bytes, state)
            await asyncio.to_thread(
                (self.root / "checkpoint.json").write_text,
                json.dumps(checkpoint, indent=2),
                encoding="utf-8",
            )
            await source.close_async()
            await connection.close_async()
            if await source._inspect_owned_container_async() is not None:
                raise ValueError("Original container still exists before restoration")
            # The restore consumes persisted bytes, not a retained workspace or live source process.
            workspace = await asyncio.to_thread(
                self._read_saved,
                path=self.root / "workspace.tar",
                expected=archives["workspace.tar"],
            )
            state = await asyncio.to_thread(
                self._read_saved,
                path=self.root / "harness-state.tar",
                expected=archives["harness-state.tar"],
            )
            controls = {}
            for phase, with_workspace, with_state in [
                ("neither", False, False),
                ("workspace_only", True, False),
                ("session_only", False, True),
                ("paired", True, True),
            ]:
                self.oracle.phase = phase
                controls[phase] = await self._restore_async(
                    cleanup=cleanup,
                    phase=phase,
                    workspace=workspace if with_workspace else None,
                    state=state if with_state else None,
                )
            report = {
                "status": "passed",
                "harness": self.harness,
                "image_id": source.execution.image_id,
                "session_id": self.session_id,
                "initialize": self.capabilities["initialize"],
                "method": "session/load",
                "process_memory_restored": False,
                "original_container_removed": True,
                "controls": controls,
                "archives": archives,
                "state_root": self.STATE_ROOTS[self.harness],
                "limitations": [
                    "Fixture-specific feasibility proof, not a production checkpoint/restore API.",
                    "Completed turns only; no process memory, background jobs, sockets, mounts, or external effects.",
                    "Private state can contain sensitive data; only synthetic fixture data was used.",
                    "No fresh execution record was treated as the original allocation.",
                ],
            }
        report["all_resources_removed"] = all(
            [
                await environment._inspect_owned_container_async() is None
                for environment in [
                    DockerSession(execution=record, directory=self.root / str(record.id)) for record in self.resources
                ]
            ]
        )
        if not report["all_resources_removed"]:
            raise RuntimeError("Checkpoint proof left an owned Docker resource")
        await asyncio.to_thread((self.root / "report.json").write_text, json.dumps(report, indent=2), encoding="utf-8")
        return report

    async def _launch_async(
        self,
        *,
        cleanup: AsyncExitStack,
        phase: str,
        workspace: bytes | None = None,
        state: bytes | None = None,
    ) -> tuple[DockerSession, AcpConnection]:
        profile = AgentProfile(
            harness_kind=self.harness, image=self.image, permission_policy=AgentPermissionPolicy.ALLOW_ONCE
        )
        record = AgentExecution(
            owner_id="checkpoint-probe", conversation_id=str(uuid4()), target_id="fixture", profile=profile
        )
        self.resources.append(record)
        directory = self.root / str(record.id)
        await asyncio.to_thread(directory.mkdir)
        environment = DockerSession(execution=record, directory=directory)
        cleanup.push_async_callback(environment.close_async)

        async def inference_record_async(payload: dict[str, Any]) -> None:
            self.events.append({"phase": phase, "direction": "inference", "payload": payload})

        lease = InferenceLease(
            binding=ResolvedModelBinding(
                target=self.oracle,
                identifier_hash=self.oracle.get_identifier().hash,
                model="checkpoint-model",
                requirements=InferenceRequirements(),
            ),
            record=inference_record_async,
            max_calls=12,
            timeout_seconds=60,
        )
        self.leases.append(lease)
        self.relay.add(lease)
        cleanup.push_async_callback(self.relay.revoke_async, lease)
        image_info = json.loads(await environment._docker_async("image", "inspect", self.image))[0]
        if image_info["Config"].get("Volumes"):
            raise ValueError("Checkpoint proof refuses images declaring volumes before allocating a container")
        record.profile = record.profile.model_copy(update={"image": image_info["Id"]})
        restore = None
        if workspace is not None and state is not None:
            layout = HarnessAdapterRegistry.get_registry_singleton().get(self.harness).checkpoint_layout
            if layout is None or layout.state_root != self.STATE_ROOTS[self.harness]:
                raise ValueError("Installed adapter layout does not match the proof's native state root")
            restore = CheckpointRestore(layout=layout, workspace=workspace, state=state)
        spec = ExecutionSpec(
            command=profile.command,
            environment={
                **launch_environment(kind=self.harness, home=environment.harness_home),
                **inference_environment(
                    kind=self.harness,
                    model=lease.binding.model,
                    wire_api=InferenceWireApi.CHAT_COMPLETIONS,
                    base_url=self.relay.url,
                    token=lease.token,
                ),
            },
            credentials={},
            restore=restore,
        )
        await environment.launch_async(spec)
        info = json.loads(await environment._docker_async("inspect", str(record.provider_id)))[0]
        if info.get("Mounts"):
            raise ValueError("Checkpoint proof requires a fixture image/container without volumes or mounts")
        stderr = asyncio.create_task(self._drain_async(stream=environment.stderr, phase=phase))
        cleanup.push_async_callback(self._join_stderr_async, environment=environment, task=stderr)

        async def capture_async(direction: str, payload: dict[str, Any]) -> None:
            self.events.append({"phase": phase, "direction": direction, "payload": payload})

        connection = AcpConnection(
            transport=RecordingTransport(
                reader=environment.acp_reader, writer=environment.acp_writer, record=capture_async
            ),
            permission_policy=AgentPermissionPolicy.ALLOW_ONCE,
        )
        cleanup.push_async_callback(connection.close_async)
        return environment, connection

    async def _stop_async(self, environment: DockerSession) -> None:
        info = await environment._inspect_owned_container_async()
        if info is None:
            raise RuntimeError("Owned container disappeared before capture")
        await environment._docker_async("stop", "--time", "10", str(environment.execution.provider_id))
        stopped = await environment._inspect_owned_container_async()
        if (
            stopped is None
            or stopped["State"]["Running"]
            or stopped["State"]["OOMKilled"]
            or stopped["State"]["ExitCode"] not in (0, 143)
        ):
            raise RuntimeError("Harness did not stop gracefully; do not claim a coordinated checkpoint")
        if environment.process:
            await asyncio.wait_for(environment.process.wait(), timeout=15)
        for lease in self.leases:
            await self.relay.revoke_async(lease)
        async with httpx.AsyncClient(trust_env=False) as client:
            port = self.relay.url.rsplit(":", 1)[1].split("/", 1)[0]
            host = "127.0.0.1" if self.relay.host in ("", "0.0.0.0") else self.relay.host
            response = await client.post(
                f"http://{host}:{port}/v1/chat/completions",
                headers={"authorization": "Bearer " + self.leases[-1].token},
                json={},
            )
            if response.status_code != 401:
                raise ValueError("Original execution authority survived checkpoint capture")

    async def _capture_async(self, *, environment: DockerSession, path: str) -> bytes:
        raw = await environment._docker_archive_async(path=path, maximum_bytes=self.MAX_ARCHIVE_BYTES)
        return await asyncio.to_thread(self._portable_archive, raw=raw, root=path)

    def _portable_archive(self, *, raw: bytes, root: str) -> bytes:
        """Validate and re-root Docker tar entries without extracting onto the host."""
        if len(raw) > self.MAX_ARCHIVE_BYTES:
            raise ValueError("Checkpoint exceeds fixture archive byte budget")
        destination = io.BytesIO()
        prefix = PurePosixPath(root)
        with tarfile.open(fileobj=io.BytesIO(raw)) as source, tarfile.open(fileobj=destination, mode="w") as target:
            names: set[str] = set()
            for index, entry in enumerate(source):
                if index >= self.MAX_ARCHIVE_FILES:
                    raise ValueError("Checkpoint exceeds fixture archive entry budget")
                path = PurePosixPath(entry.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.name
                    or ":" in entry.name
                    or not path.parts
                    or path.parts[0] != prefix.name
                    or str(path) in names
                    or not (entry.isfile() or entry.isdir())
                ):
                    raise ValueError(f"Unsafe or unsupported checkpoint entry: {entry.name}")
                names.add(str(path))
                if entry.size > self.MAX_ARCHIVE_BYTES:
                    raise ValueError("Checkpoint file exceeds fixture size budget")
                content = source.extractfile(entry) if entry.isfile() else None
                entry.name = str(prefix.relative_to("/") / PurePosixPath(*path.parts[1:]))
                entry.pax_headers = {}
                target.addfile(entry, content)
        result = destination.getvalue()
        if len(result) > self.MAX_ARCHIVE_BYTES or any(lease.token.encode() in result for lease in self.leases):
            raise ValueError("Checkpoint is oversized or contains an execution relay credential")
        return result

    @classmethod
    def _read_saved(cls, *, path: Path, expected: dict[str, Any]) -> bytes:
        with path.open("rb") as stream:
            data = stream.read(cls.MAX_ARCHIVE_BYTES + 1)
        if (
            len(data) > cls.MAX_ARCHIVE_BYTES
            or len(data) != expected["bytes"]
            or hashlib.sha256(data).hexdigest() != expected["sha256"]
        ):
            raise ValueError("Saved checkpoint bytes changed; refusing restoration")
        return data

    async def _restore_async(
        self,
        *,
        cleanup: AsyncExitStack,
        phase: str,
        workspace: bytes | None,
        state: bytes | None,
    ) -> dict[str, Any]:
        environment, connection = await self._launch_async(
            cleanup=cleanup, phase=phase, workspace=workspace, state=state
        )
        result = await connection.connection.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="pyrit-checkpoint-proof", version="1"),
        )
        if result.protocol_version != 1 or not result.agent_capabilities or not result.agent_capabilities.load_session:
            raise ValueError("Restored harness no longer advertises the same ACP load contract")
        before = len(self.oracle.calls)
        try:
            await connection.connection.load_session(
                cwd=environment.agent_cwd, session_id=self.session_id, mcp_servers=[]
            )
        except RequestError:
            if state is not None:
                raise
            if len(self.oracle.calls) != before:
                raise ValueError("Loading a missing session unexpectedly made model calls") from None
            await environment.close_async()
            return {"load_rejected": True, "model_calls": len(self.oracle.calls) - before}
        if state is None:
            raise ValueError("Harness loaded a missing session; cold controls must fail explicitly")
        if len(self.oracle.calls) != before:
            raise ValueError("Loading native session unexpectedly made model calls")
        connection.session_id = self.session_id
        stop, response = await connection.prompt_async("CONTINUE_CHECKPOINT: advance the previous receipt once.")
        expected = "CONTINUATION_VERIFIED" if workspace is not None else "WORKSPACE_NOT_RESTORED"
        if stop != "end_turn" or response != expected:
            raise ValueError(f"Unexpected continuation outcome for {phase}: {stop}, {response}")
        await self._stop_async(environment)
        if workspace is not None:
            for name, expected_bytes in (
                ("receipt.json", b'{"total":36,"writes":2}'),
                ("effects.log", b"seed\ncontinued\n"),
            ):
                archive = await environment._docker_archive_async(path=f"/workspace/{name}", maximum_bytes=65536)
                actual = await asyncio.to_thread(environment._read_tar_file, data=archive, maximum_bytes=4096)
                if actual != expected_bytes:
                    raise ValueError(f"Trusted file verification failed for {name}")
        await environment.close_async()
        return {
            "load_rejected": False,
            "native_history_verified": True,
            "workspace_verified": workspace is not None,
            "response": response,
            "model_calls": len(self.oracle.calls) - before,
            "execution_id": str(environment.execution.id),
        }

    async def _drain_async(self, *, stream: AcpByteReader, phase: str) -> None:
        while data := await stream.read(65536):
            self.stderr[phase] = (self.stderr.get(phase, "") + data.decode(errors="replace"))[-65536:]

    @staticmethod
    async def _join_stderr_async(*, environment: DockerSession, task: asyncio.Task[None]) -> None:
        await environment.close_async()
        await asyncio.wait_for(task, timeout=15)


async def run_async(args: argparse.Namespace) -> None:
    """Run only synthetic fixture model traffic and scoped local Docker resources."""
    await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[], load_defaults=False, silent=True)
    probe = CheckpointProbe(
        root=Path(args.results).resolve(),
        harness=args.harness,
        image=args.image,
        relay_host=args.relay_host,
        advertised_host=args.advertised_host,
    )
    try:
        async with asyncio.timeout(300):
            report = await probe.run_async()
    finally:
        diagnostics = json.dumps(
            {"events": probe.events, "inference": probe.oracle.calls, "stderr": probe.stderr}, indent=2
        )
        for lease in probe.leases:
            diagnostics = diagnostics.replace(lease.token, "[REDACTED RELAY CREDENTIAL]")
        if probe.owns_output:
            await asyncio.to_thread((probe.root / "diagnostics.json").write_text, diagnostics, encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> None:
    """Parse the explicitly selected Docker fixture and reachable relay interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=("copilot",), default="copilot")
    parser.add_argument("--image", required=True, help="Disposable receipt fixture image; never an existing container")
    parser.add_argument(
        "--results", required=True, help="New output directory for sensitive fixture state and evidence"
    )
    parser.add_argument("--relay-host", required=True, help="Explicit reachable bind interface, e.g. 0.0.0.0")
    parser.add_argument(
        "--advertised-host", required=True, help="Docker-reachable relay host, e.g. host.docker.internal"
    )
    asyncio.run(run_async(parser.parse_args()))


if __name__ == "__main__":
    main()
