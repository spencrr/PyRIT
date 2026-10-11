# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Owned subprocess/container resources, independent of agent protocol and attack logic."""

import asyncio
import hashlib
import io
import json
import os
import shlex
import shutil
import signal
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

import psutil

from pyrit.agent.checkpoint_archive import CheckpointArchive
from pyrit.agent.harness import launch_environment
from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_execution import AgentEnvironment, AgentExecution, AgentWorkspaceBase


class ExecutionEnvironment:
    """A single fresh workspace and harness process, never an arbitrary existing resource."""

    _MAX_WORKSPACE_ARCHIVE_BYTES = 32 * 1024 * 1024
    _RESTORE_SUPERVISOR = (
        "/bin/sh",
        "-c",
        'while [ ! -f "$1" ]; do sleep 0.1; done; rm -- "$1"; shift; exec "$@"',
        "--",
    )

    def __init__(self, *, execution: AgentExecution, directory: Path) -> None:
        """Bind the durable identity before acquiring any resources."""
        self.execution = execution
        self.directory = directory
        self.workspace = directory / "workspace"
        self.home = directory / "home"
        self.process: asyncio.subprocess.Process | None = None
        self.runtime_environment: dict[str, str] = {}

    @property
    def container_workspace(self) -> str:
        """The container workspace root; empty workspaces never reuse image contents."""
        if self.execution.profile.workspace_base == AgentWorkspaceBase.EMPTY:
            return f"/pyrit-workspace-{self.execution.id}"
        return "/workspace"

    def _launch_environment(self) -> dict[str, str]:
        home = "/home/agent" if self.execution.profile.environment == AgentEnvironment.DOCKER else str(self.home)
        return {**launch_environment(kind=self.execution.profile.harness_kind, home=home), **self.runtime_environment}

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
        image = await self._docker_async("image", "inspect", self.execution.image_id or str(profile.image))
        image_info = json.loads(image)[0]
        if self.execution.restored_from_checkpoint_id and image_info["Config"].get("Volumes"):
            raise ValueError("Checkpoint restoration refuses images with declared volumes")
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
            self.agent_cwd,
            "--env",
            "HOME=/home/agent",
            "--env",
            "XDG_CONFIG_HOME=/home/agent/.config",
            "--env",
            "XDG_DATA_HOME=/home/agent/.local/share",
            "--env",
            "XDG_CACHE_HOME=/home/agent/.cache",
        ]
        for name in profile.credential_env:
            args.extend(["--env", name])
        for name in self._launch_environment():
            args.extend(["--env", name])
        command = (
            (*self._RESTORE_SUPERVISOR, f"/tmp/pyrit-restore-{self.execution.id}", *profile.command)
            if self.execution.restored_from_checkpoint_id
            else profile.command
        )
        args.extend(["--entrypoint", command[0], self.execution.image_id, *command[1:]])
        await self._docker_async(*args, credentials=True)
        image_user = image_info.get("Config", {}).get("User", "")
        if (
            profile.workspace_base == AgentWorkspaceBase.EMPTY
            or profile.fixture_directory
            or profile.working_directory != "."
            or image_user
        ):
            uid, gid = await self._docker_user_async(image_user)
            archive = await asyncio.to_thread(self._workspace_archive, uid=uid, gid=gid)
            await self._docker_async(
                "cp",
                "--archive",
                "-",
                f"{self.execution.provider_id}:/",
                input_data=archive,
            )

    async def _docker_user_async(self, user: str) -> tuple[int, int]:
        if not user:
            return 0, 0
        name, _, group = user.partition(":")
        uid = int(name) if name.isdecimal() else None
        if uid is not None and group.isdecimal():
            return uid, int(group)
        passwd = await self._image_accounts_async("/etc/passwd")
        account = next(
            (
                fields
                for fields in passwd
                if len(fields) == 7 and (fields[0] == name or (uid is not None and fields[2] == str(uid)))
            ),
            None,
        )
        if uid is None:
            if account is None:
                raise ValueError(f"Image user '{name}' was not found in /etc/passwd")
            uid = int(account[2])
        gid = int(account[3]) if account else 0
        if group:
            if group.isdecimal():
                gid = int(group)
            else:
                groups = await self._image_accounts_async("/etc/group")
                entry = next((fields for fields in groups if len(fields) == 4 and fields[0] == group), None)
                if entry is None:
                    raise ValueError(f"Image group '{group}' was not found in /etc/group")
                gid = int(entry[2])
        return uid, gid

    async def _image_accounts_async(self, path: str) -> list[list[str]]:
        try:
            data = await self._docker_archive_async(path=path, maximum_bytes=65536)
        except RuntimeError as error:
            raise ValueError(
                "Cannot resolve image workspace ownership; use an image with numeric USER uid:gid"
            ) from error
        content = self._read_tar_file(data=data, maximum_bytes=65536)
        return [line.split(":") for line in content.decode("utf-8").splitlines()]

    def _workspace_archive(self, *, uid: int, gid: int) -> bytes:
        root = PurePosixPath(self.container_workspace.lstrip("/"))
        paths = sorted(self.workspace.rglob("*"))
        directories: set[PurePosixPath] = {PurePosixPath("home/agent")}
        if self.execution.profile.workspace_base == AgentWorkspaceBase.EMPTY:
            directories.add(root)
        working = PurePosixPath(self.execution.profile.working_directory)
        directories.update(root / parent for parent in (*working.parents, working) if parent != PurePosixPath("."))
        for path in paths:
            if self._is_link(path) or not (path.is_dir() or path.is_file()):
                raise ValueError("Workspace imports must contain only regular files and directories")
            if path.is_dir():
                directories.add(root / path.relative_to(self.workspace).as_posix())
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for directory in sorted(directories, key=lambda path: (len(path.parts), str(path))):
                info = tarfile.TarInfo(str(directory))
                info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, uid, gid
                archive.addfile(info)
                if stream.tell() > self._MAX_WORKSPACE_ARCHIVE_BYTES:
                    raise ValueError("Workspace archive exceeds the preparation budget")
            for path in paths:
                if not path.is_file():
                    continue
                info = archive.gettarinfo(str(path), arcname=str(root / path.relative_to(self.workspace).as_posix()))
                info.uid, info.gid, info.uname, info.gname = uid, gid, "", ""
                if stream.tell() + info.size > self._MAX_WORKSPACE_ARCHIVE_BYTES:
                    raise ValueError("Workspace archive exceeds the preparation budget")
                with path.open("rb") as source:
                    archive.addfile(info, source)
        if stream.tell() > self._MAX_WORKSPACE_ARCHIVE_BYTES:
            raise ValueError("Workspace archive exceeds the preparation budget")
        return stream.getvalue()

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
            command = (
                ("docker", "attach", "--sig-proxy=false", str(self.execution.provider_id))
                if self.execution.restored_from_checkpoint_id
                else ("docker", "start", "--attach", "--interactive", str(self.execution.provider_id))
            )
        else:
            environment.update(self.credential_values())
            environment.update(
                HOME=str(self.home),
                USERPROFILE=str(self.home),
                XDG_CONFIG_HOME=str(self.home / ".config"),
                XDG_DATA_HOME=str(self.home / ".local" / "share"),
                XDG_CACHE_HOME=str(self.home / ".cache"),
                APPDATA=str(self.home / "AppData"),
                LOCALAPPDATA=str(self.home / "LocalAppData"),
            )
            environment.update(self._launch_environment())
            command = profile.command
            await asyncio.to_thread((self.workspace / profile.working_directory).mkdir, parents=True, exist_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.workspace if profile.environment == AgentEnvironment.DOCKER else self.agent_cwd,
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
        if self.execution.restored_from_checkpoint_id:
            await self._docker_async(
                "exec",
                str(self.execution.provider_id),
                "touch",
                "--",
                f"/tmp/pyrit-restore-{self.execution.id}",
            )
        return self.process

    @property
    def agent_cwd(self) -> str:
        """The workspace path in the harness's filesystem namespace."""
        relative = self.execution.profile.working_directory
        if self.execution.profile.environment == AgentEnvironment.DOCKER:
            return str(PurePosixPath(self.container_workspace) / relative)
        return str(self.workspace / relative)

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

    async def capture_checkpoint_async(
        self,
        *,
        layout: HarnessCheckpointLayout,
        secrets: tuple[str, ...],
    ) -> tuple[bytes, bytes]:
        """
        Stop the exact owned container, then capture one consistent filesystem cut.

        Returns:
            tuple[bytes, bytes]: Workspace and native state archives.

        Raises:
            ValueError: Storage or archive layout is unsupported.
            RuntimeError: The container did not stop cleanly.
        """
        info = await self._inspect_owned_container_async()
        if info is None or info.get("Mounts"):
            raise ValueError("Checkpoint capture requires an owned container without mounted filesystems")
        await self._docker_async("stop", "--time", "10", str(self.execution.provider_id))
        stopped = await self._inspect_owned_container_async()
        if (
            not stopped
            or stopped["State"]["Running"]
            or stopped["State"]["OOMKilled"]
            or stopped["State"]["ExitCode"] not in (0, 143)
        ):
            raise RuntimeError("Harness did not stop cleanly; no checkpoint was captured")
        if self.process:
            await asyncio.wait_for(self.process.wait(), timeout=15)
        archives = []
        for path in ("/workspace", layout.state_root):
            raw = await self._docker_archive_async(path=path, maximum_bytes=CheckpointArchive.MAX_BYTES)
            archives.append(await asyncio.to_thread(CheckpointArchive.normalize, raw=raw, root=path, secrets=secrets))
        return archives[0], archives[1]

    async def restore_checkpoint_async(
        self,
        *,
        layout: HarnessCheckpointLayout,
        workspace: bytes,
        state: bytes,
    ) -> None:
        """
        Restore validated native state before starting a newly allocated harness.

        Raises:
            ValueError: The destination is not a fresh stopped unmounted container.
        """
        info = await self._inspect_owned_container_async()
        if info is None or info.get("Mounts") or info["State"]["Running"]:
            raise ValueError("Restore requires a fresh stopped owned container without mounts")
        uid, gid = await self._docker_user_async(info["Config"].get("User", ""))
        await self._docker_async("start", str(self.execution.provider_id))
        root = PurePosixPath(layout.state_root)
        checked = [
            "/workspace",
            *(str(path) for path in reversed(root.parents) if path != PurePosixPath("/")),
            str(root),
        ]
        script = (
            "set -eu; "
            + "; ".join(f"test ! -L {shlex.quote(path)}" for path in checked)
            + "; test -d /workspace; find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; "
            + f"rm -rf -- {shlex.quote(layout.state_root)}"
        )
        await self._docker_async("exec", str(self.execution.provider_id), "/bin/sh", "-c", script)
        parents = await asyncio.to_thread(CheckpointArchive.parents, root=layout.state_root, uid=uid, gid=gid)
        for data in (parents, workspace, state):
            await self._docker_async("cp", "--archive", "-", f"{self.execution.provider_id}:/", input_data=data)

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
        data = await self._docker_archive_async(
            path=f"{self.container_workspace}/{relative}",
            maximum_bytes=self.execution.profile.max_artifact_bytes + 65536,
        )
        await asyncio.to_thread(self._save_tar_artifact, relative=relative, data=data)

    async def _docker_archive_async(self, *, path: str, maximum_bytes: int) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "cp",
            f"{self.execution.provider_id}:{path}",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        try:
            async with asyncio.timeout(15):
                data = await process.stdout.readexactly(maximum_bytes + 1)
                raise ValueError("Container archive exceeds its read budget")
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
        return data

    def _save_tar_artifact(self, *, relative: str, data: bytes) -> None:
        content = self._read_tar_file(data=data, maximum_bytes=self.execution.profile.max_artifact_bytes)
        destination = self.directory / "artifacts" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    @staticmethod
    def _read_tar_file(*, data: bytes, maximum_bytes: int) -> bytes:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile():
                raise ValueError("Artifact must be a single regular file, not a link or directory")
            if members[0].size > maximum_bytes:
                raise ValueError("Archive member exceeds its read budget")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ValueError("Artifact has no file content")
            return stream.read()

    async def _docker_async(self, *args: str, credentials: bool = False, input_data: bytes | None = None) -> str:
        environment = dict(os.environ)
        if credentials:
            environment.update(self.credential_values())
            environment.update(self._launch_environment())
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if input_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate(input_data)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"Docker {args[0]} failed: {stderr.decode('utf-8', errors='replace')[:2000]}")
        return stdout.decode("utf-8")
