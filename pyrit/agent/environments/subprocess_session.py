# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared host-side workspace preparation and owned-process control for subprocess-backed sessions."""

import asyncio
import hashlib
import os
import shutil
import signal
import stat
from pathlib import Path

import psutil

from pyrit.agent.environment import AcpByteReader, AcpByteWriter, ExecutionSession
from pyrit.models.agent_execution import AgentExecution


class SubprocessSession(ExecutionSession):
    """A fresh host workspace plus one owned process whose pipes carry ACP; never an existing resource."""

    MAX_FIXTURE_BYTES = 16 * 1024 * 1024
    INHERITED_ENVIRONMENT = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")

    def __init__(self, *, execution: AgentExecution, directory: Path) -> None:
        """Bind the durable identity and the host directories this session may create."""
        super().__init__(execution=execution, directory=directory)
        self.workspace = directory / "workspace"
        self.home = directory / "home"
        self.process: asyncio.subprocess.Process | None = None

    @property
    def acp_reader(self) -> AcpByteReader:
        """The process's standard output."""
        process = self._require_process()
        assert process.stdout is not None
        return process.stdout

    @property
    def acp_writer(self) -> AcpByteWriter:
        """The process's standard input."""
        process = self._require_process()
        assert process.stdin is not None
        return process.stdin

    @property
    def stderr(self) -> AcpByteReader:
        """The process's standard error."""
        process = self._require_process()
        assert process.stderr is not None
        return process.stderr

    def _require_process(self) -> asyncio.subprocess.Process:
        if self.process is None:
            raise RuntimeError("Execution session has not been launched")
        return self.process

    async def _prepare_workspace_async(self) -> None:
        """
        Create fresh directories and validate/copy the fixture without following links.

        Raises:
            ValueError: A pinned fixture has changed.
        """
        self.execution.fixture_sha256 = await asyncio.to_thread(self._prepare_workspace)
        expected = self.execution.profile.expected_fixture_sha256
        if expected and expected != self.execution.fixture_sha256:
            raise ValueError("Workspace fixture changed; refusing to recreate a different starting environment")

    def _prepare_workspace(self) -> str:
        self.workspace.mkdir()
        self.home.mkdir()
        fixture = self.execution.profile.fixture_directory
        digest = hashlib.sha256()
        if not fixture:
            return digest.hexdigest()
        source = Path(fixture).resolve(strict=True)
        if not source.is_dir():
            raise ValueError("fixture_directory must be a directory")
        total = 0
        for path in sorted(source.rglob("*")):
            if self._is_link(path):
                raise ValueError("Fixtures must not contain symbolic links or junctions")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("Fixtures may contain only regular files and directories")
            total += path.stat().st_size
            if total > self.MAX_FIXTURE_BYTES:
                raise ValueError("MVP fixtures must not exceed 16 MiB")
            relative = path.relative_to(source)
            content = path.read_bytes()
            digest.update(str(relative).encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            destination = self.workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        return digest.hexdigest()

    def _inherited_environment(self) -> dict[str, str]:
        return {name: value for name in self.INHERITED_ENVIRONMENT if (value := os.environ.get(name)) is not None}

    async def _spawn_async(self, *, command: tuple[str, ...], cwd: Path | str, environment: dict[str, str]) -> None:
        """Launch stdio without a shell or TTY and record the owned process identity."""
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            start_new_session=os.name != "nt",
        )
        pid = self.process.pid
        self.execution.process_id = pid
        self.execution.process_created_at = await asyncio.to_thread(self._process_created_at, pid)

    @staticmethod
    def _process_created_at(pid: int) -> float | None:
        try:
            created: float = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            return None
        return created

    async def _stop_owned_process_async(self, *, kill_group: bool) -> None:
        if self.execution.process_id is not None:
            await asyncio.to_thread(self._stop_process, kill_group)
        if self.process is not None:
            await asyncio.wait_for(self.process.wait(), timeout=10)

    def _stop_process(self, kill_group: bool) -> None:
        try:
            if self.execution.process_id is None:
                return
            process = psutil.Process(self.execution.process_id)
            if process.create_time() != self.execution.process_created_at:
                raise RuntimeError("Process identity changed; refusing to terminate a reused PID")
            processes = [*process.children(recursive=True), process]
            if kill_group and os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                for child in processes:
                    child.terminate()
            _, alive = psutil.wait_procs(processes, timeout=3)
            for child in alive:
                child.kill()
            _, alive = psutil.wait_procs(alive, timeout=3)
            if alive:
                raise RuntimeError("Owned agent processes did not terminate")
        except psutil.NoSuchProcess:
            return

    def _remove_runtime_directories(self) -> None:
        for path in (self.workspace, self.home):
            if path.exists():
                if self._is_link(path):
                    raise RuntimeError("Runtime directory was replaced with a link; refusing recursive cleanup")
                shutil.rmtree(path)

    def _validate_artifact(self, path: Path) -> None:
        if self._is_link(path) or not path.is_file():
            raise ValueError("Artifact is not a regular file")
        if path.stat().st_size > self.execution.profile.max_artifact_bytes:
            raise ValueError("Artifact exceeds max_artifact_bytes")

    def _artifact_destination(self, relative: str) -> Path:
        destination = self.directory / "artifacts" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    @staticmethod
    def _is_link(path: Path) -> bool:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
