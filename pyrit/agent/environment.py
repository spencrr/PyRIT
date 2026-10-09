# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Owned subprocess/container resources, independent of agent protocol and attack logic."""

import asyncio
import hashlib
import io
import json
import os
import shutil
import signal
import stat
import tarfile
from pathlib import Path
from typing import Any

import psutil

from pyrit.models.agent_execution import AgentEnvironment, AgentExecution


class ExecutionEnvironment:
    """A single fresh workspace and harness process, never an arbitrary existing resource."""

    def __init__(self, *, execution: AgentExecution, directory: Path) -> None:
        """Bind the durable identity before acquiring any resources."""
        self.execution = execution
        self.directory = directory
        self.workspace = directory / "workspace"
        self.home = directory / "home"
        self.process: asyncio.subprocess.Process | None = None
        self.runtime_environment: dict[str, str] = {}

    async def prepare_async(self) -> None:
        """
        Create fresh directories and validate/copy the fixture without following links.

        Raises:
            ValueError: A pinned fixture has changed.
        """
        self.execution.fixture_sha256 = await asyncio.to_thread(self._prepare)
        expected = self.execution.profile.expected_fixture_sha256
        if expected and expected != self.execution.fixture_sha256:
            raise ValueError("Workspace fixture changed; refusing to recreate a different starting environment")
        if self.execution.profile.environment == AgentEnvironment.DOCKER:
            await self._prepare_docker_async()

    def _prepare(self) -> str:
        self.workspace.mkdir()
        self.home.mkdir()
        fixture = self.execution.profile.fixture_directory
        digest = hashlib.sha256()
        if fixture:
            source = Path(fixture).resolve(strict=True)
            if not source.is_dir():
                raise ValueError("fixture_directory must be a directory")
            paths = sorted(source.rglob("*"))
            total = 0
            for path in paths:
                if self._is_link(path):
                    raise ValueError("Fixtures must not contain symbolic links or junctions")
                if path.is_file():
                    total += path.stat().st_size
                    if total > 16 * 1024 * 1024:
                        raise ValueError("MVP fixtures must not exceed 16 MiB")
                    relative = path.relative_to(source)
                    content = path.read_bytes()
                    digest.update(str(relative).encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(content)
                    destination = self.workspace / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                elif not path.is_dir():
                    raise ValueError("Fixtures may contain only regular files and directories")
        return digest.hexdigest()

    def credential_values(self) -> dict[str, str]:
        """
        Resolve explicitly selected credentials without persisting their values.

        Returns:
            dict[str, str]: Named credentials.

        Raises:
            ValueError: A configured credential is missing.
        """
        values = {}
        for name in self.execution.profile.credential_env:
            value = os.environ.get(name)
            if not value:
                raise ValueError(f"Required credential environment variable is missing: {name}")
            values[name] = value
        return values

    async def _prepare_docker_async(self) -> None:
        profile = self.execution.profile
        image = await self._docker_async("image", "inspect", str(profile.image))
        image_info = json.loads(image)[0]
        self.execution.image_id = image_info["Id"]
        self.execution.provider_id = f"pyrit-agent-{self.execution.id}"
        args = [
            "create",
            "--name",
            self.execution.provider_id,
            "--label",
            f"pyrit.execution={self.execution.id}",
            "--label",
            f"pyrit.owner={self.execution.owner_id}",
            "--init",
            "--interactive",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory",
            profile.docker_memory,
            "--cpus",
            str(profile.docker_cpus),
            "--network",
            profile.docker_network,
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/home/agent",
            "--env",
            "COPILOT_HOME=/home/agent/.copilot",
        ]
        for name in profile.credential_env:
            args.extend(["--env", name])
        for name in self.runtime_environment:
            args.extend(["--env", name])
        args.extend(["--entrypoint", profile.command[0], self.execution.image_id, *profile.command[1:]])
        await self._docker_async(*args, credentials=True)
        # Only a fresh, private copy is mounted/copied; never the operator's repository or home.
        await self._docker_async("cp", str(self.workspace) + os.sep + ".", f"{self.execution.provider_id}:/workspace")

    async def launch_async(self) -> asyncio.subprocess.Process:
        """
        Launch stdio without a shell or TTY.

        Returns:
            asyncio.subprocess.Process: The owned process.
        """
        profile = self.execution.profile
        environment = {
            name: value
            for name in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if (value := os.environ.get(name)) is not None
        }
        if profile.environment == AgentEnvironment.DOCKER:
            command = ("docker", "start", "--attach", "--interactive", str(self.execution.provider_id))
        else:
            environment.update(self.credential_values())
            environment.update(self.runtime_environment)
            environment.update(
                HOME=str(self.home),
                USERPROFILE=str(self.home),
                COPILOT_HOME=str(self.home / ".copilot"),
                XDG_CONFIG_HOME=str(self.home / ".config"),
                XDG_DATA_HOME=str(self.home / ".local"),
                APPDATA=str(self.home / "AppData"),
                LOCALAPPDATA=str(self.home / "LocalAppData"),
            )
            command = profile.command
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.workspace,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            start_new_session=os.name != "nt",
        )
        self.execution.process_id = self.process.pid
        self.execution.process_created_at = await asyncio.to_thread(
            lambda: psutil.Process(self.process.pid).create_time() if self.process else None
        )
        return self.process

    @property
    def agent_cwd(self) -> str:
        """The workspace path in the harness's filesystem namespace."""
        return "/workspace" if self.execution.profile.environment == AgentEnvironment.DOCKER else str(self.workspace)

    async def close_async(self) -> None:
        """Stop and verify exact owned resources; leave evidence and artifacts intact."""
        if self.execution.profile.environment == AgentEnvironment.DOCKER and self.execution.provider_id:
            info = await self._inspect_owned_container_async()
            if info:
                if info["State"]["Running"]:
                    await self._docker_async("stop", "--time", "5", str(self.execution.provider_id))
                await self._collect_artifacts_async()
                await self._docker_async("rm", str(self.execution.provider_id))
        if self.execution.process_id is not None:
            await asyncio.to_thread(self._stop_process)
        if self.process is not None:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        if self.execution.profile.environment == AgentEnvironment.LOCAL:
            await self._collect_artifacts_async()
        await asyncio.to_thread(self._remove_runtime_directories)

    def _remove_runtime_directories(self) -> None:
        for path in (self.workspace, self.home):
            if path.exists():
                if self._is_link(path):
                    raise RuntimeError("Runtime directory was replaced with a link; refusing recursive cleanup")
                shutil.rmtree(path)

    async def _inspect_owned_container_async(self) -> dict[str, Any] | None:
        output = await self._docker_async(
            "container", "ls", "--all", "--quiet", "--filter", f"name=^/{self.execution.provider_id}$"
        )
        if not output.strip():
            return None
        info = json.loads(await self._docker_async("inspect", str(self.execution.provider_id)))[0]
        labels = info["Config"].get("Labels", {})
        if (
            labels.get("pyrit.execution") != str(self.execution.id)
            or labels.get("pyrit.owner") != self.execution.owner_id
        ):
            raise RuntimeError("Refusing to stop a container whose ownership labels do not match")
        return info

    def _stop_process(self) -> None:
        try:
            if self.execution.process_id is None:
                return
            process = psutil.Process(self.execution.process_id)
            if process.create_time() != self.execution.process_created_at:
                raise RuntimeError("Process identity changed; refusing to terminate a reused PID")
            children = process.children(recursive=True)
            processes = [*children, process]
            if os.name != "nt" and self.execution.profile.environment == AgentEnvironment.LOCAL:
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

    async def _collect_artifacts_async(self) -> None:
        for relative in self.execution.profile.artifact_paths:
            try:
                if self.execution.profile.environment == AgentEnvironment.DOCKER:
                    await self._copy_docker_artifact_async(relative)
                else:
                    await asyncio.to_thread(self._copy_local_artifact, relative)
                self.execution.artifacts.append(relative)
            except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
                self.execution.artifact_errors.append(f"{relative}: {error}")

    def _validate_artifact(self, path: Path) -> None:
        if self._is_link(path) or not path.is_file():
            raise ValueError("Artifact is not a regular file")
        if path.stat().st_size > self.execution.profile.max_artifact_bytes:
            raise ValueError("Artifact exceeds max_artifact_bytes")

    def _copy_local_artifact(self, relative: str) -> None:
        source = self.workspace / relative
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(self.workspace.resolve()):
            raise ValueError("Artifact escapes the execution workspace")
        self._validate_artifact(source)
        destination = self.directory / "artifacts" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    @staticmethod
    def _is_link(path: Path) -> bool:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    async def _copy_docker_artifact_async(self, relative: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "cp",
            f"{self.execution.provider_id}:/workspace/{relative}",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        maximum = self.execution.profile.max_artifact_bytes + 65536
        try:
            async with asyncio.timeout(15):
                data = await process.stdout.readexactly(maximum + 1)
                raise ValueError("Artifact archive exceeds the retention budget")
        except asyncio.IncompleteReadError as end:
            data = end.partial
            stderr = await process.stderr.read(4096)
            await process.wait()
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace")) from end
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await asyncio.to_thread(self._save_tar_artifact, relative=relative, data=data)

    def _save_tar_artifact(self, *, relative: str, data: bytes) -> None:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile():
                raise ValueError("Artifact must be a single regular file, not a link or directory")
            if members[0].size > self.execution.profile.max_artifact_bytes:
                raise ValueError("Artifact exceeds max_artifact_bytes")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ValueError("Artifact has no file content")
            destination = self.directory / "artifacts" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(stream.read())

    async def _docker_async(self, *args: str, credentials: bool = False) -> str:
        environment = dict(os.environ)
        if credentials:
            environment.update(self.credential_values())
            environment.update(self.runtime_environment)
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"Docker {args[0]} failed: {stderr.decode('utf-8', errors='replace')[:2000]}")
        return stdout.decode("utf-8")
