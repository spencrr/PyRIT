# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Docker execution: the reference isolated provider, with optional stopped-filesystem checkpoints."""

import asyncio
import io
import json
import os
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from pyrit.agent.checkpoint_archive import CheckpointArchive
from pyrit.agent.environment import ExecutionEnvironmentProvider, ExecutionSession, ExecutionSpec
from pyrit.agent.environments.subprocess_session import SubprocessSession
from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_environment import EnvironmentCapabilities, EnvironmentProviderInfo
from pyrit.models.agent_execution import AgentEnvironment, AgentExecution, AgentWorkspaceBase, EnvironmentTemplate


class DockerSession(SubprocessSession):
    """One labeled container created from a pinned image; ACP flows over the attached stdio."""

    MAX_WORKSPACE_ARCHIVE_BYTES = 32 * 1024 * 1024
    HARNESS_HOME = "/home/agent"
    LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
    RESTORE_SUPERVISOR = (
        "/bin/sh",
        "-c",
        'while [ ! -f "$1" ]; do sleep 0.1; done; rm -- "$1"; shift; exec "$@"',
        "--",
    )

    @property
    def harness_home(self) -> str:
        """The in-container private home."""
        return self.HARNESS_HOME

    @property
    def container_workspace(self) -> str:
        """The container workspace root; empty workspaces never reuse image contents."""
        if self.execution.profile.workspace_base == AgentWorkspaceBase.EMPTY:
            return f"/pyrit-workspace-{self.execution.id}"
        return "/workspace"

    @property
    def agent_cwd(self) -> str:
        """The in-container workspace path, including the relative working directory."""
        return str(PurePosixPath(self.container_workspace) / self.execution.profile.working_directory)

    @property
    def _container(self) -> str:
        return f"pyrit-agent-{self.execution.id}"

    @property
    def _restore_marker(self) -> str:
        return f"/tmp/pyrit-restore-{self.execution.id}"

    def reserve(self) -> None:
        """Container names are deterministic, so a crash during create remains reconcilable."""
        self.execution.provider_id = self._container

    async def inference_endpoint_async(self, *, relay_url: str) -> str:
        """
        Require a relay address the container can reach; loopback belongs to the backend host.

        Returns:
            str: The unchanged relay URL.

        Raises:
            ValueError: The relay is advertised on a loopback address.
        """
        if (urlsplit(relay_url).hostname or "").lower() in self.LOOPBACK_HOSTS:
            raise ValueError(
                "Docker BYOK requires an explicitly reachable PYRIT_INFERENCE_RELAY_HOST and "
                "PYRIT_INFERENCE_RELAY_ADVERTISED_HOST (for example host.docker.internal on Docker Desktop)"
            )
        return relay_url

    async def launch_async(self, spec: ExecutionSpec) -> None:
        """
        Create the container from the pinned image, seed its filesystem, then attach to its stdio.

        Raises:
            ValueError: The image or starting state is unsupported.
            RuntimeError: A Docker operation failed.
        """
        self.reserve()
        await self._prepare_workspace_async()
        await self._create_container_async(spec)
        if spec.restore is not None:
            await self._restore_checkpoint_async(
                spec.restore.layout, workspace=spec.restore.workspace, state=spec.restore.state
            )
            command = ("docker", "attach", "--sig-proxy=false", self._container)
        else:
            command = ("docker", "start", "--attach", "--interactive", self._container)
        # The attach process is the docker CLI, not the harness: it needs the same daemon/context/config
        # environment the management commands use. Credentials were already passed at `docker create`.
        await self._spawn_async(command=command, cwd=self.workspace, environment=dict(os.environ))
        if spec.restore is not None:
            await self._docker_async("exec", self._container, "touch", "--", self._restore_marker)

    async def close_async(self) -> None:
        """Stop and remove exactly the owned container; leave evidence and artifacts intact."""
        if self.execution.provider_id:
            info = await self._inspect_owned_container_async()
            if info:
                if info["State"]["Running"]:
                    await self._docker_async("stop", "--time", "5", self._container)
                await self._collect_artifacts_async()
                await self._docker_async("rm", self._container)
        await self._stop_owned_process_async(kill_group=False)
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
        await self._docker_async("stop", "--time", "10", self._container)
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

    async def _create_container_async(self, spec: ExecutionSpec) -> None:
        profile = self.execution.profile
        image = await self._docker_async("image", "inspect", self.execution.image_id or str(profile.image))
        image_info = json.loads(image)[0]
        if spec.restore is not None and image_info["Config"].get("Volumes"):
            raise ValueError("Checkpoint restoration refuses images with declared volumes")
        self.execution.image_id = image_info["Id"]
        self.execution.provenance = {
            "image": str(profile.image),
            "image_id": image_info["Id"],
            "container": self._container,
        }
        args = [
            "create",
            "--name",
            self._container,
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
            f"HOME={self.HARNESS_HOME}",
            "--env",
            f"XDG_CONFIG_HOME={self.HARNESS_HOME}/.config",
            "--env",
            f"XDG_DATA_HOME={self.HARNESS_HOME}/.local/share",
            "--env",
            f"XDG_CACHE_HOME={self.HARNESS_HOME}/.cache",
        ]
        # Only names go on the command line; values travel through the docker CLI's own environment.
        for name in (*spec.credentials, *spec.environment):
            args.extend(["--env", name])
        command = (
            (*self.RESTORE_SUPERVISOR, self._restore_marker, *profile.command)
            if spec.restore is not None
            else profile.command
        )
        args.extend(["--entrypoint", command[0], self.execution.image_id, *command[1:]])
        await self._docker_async(*args, environment={**spec.credentials, **spec.environment})
        image_user = image_info.get("Config", {}).get("User", "")
        if (
            profile.workspace_base == AgentWorkspaceBase.EMPTY
            or profile.fixture_directory
            or profile.working_directory != "."
            or image_user
        ):
            uid, gid = await self._docker_user_async(image_user)
            archive = await asyncio.to_thread(self._workspace_archive, uid=uid, gid=gid)
            await self._docker_async("cp", "--archive", "-", f"{self._container}:/", input_data=archive)

    async def _restore_checkpoint_async(
        self, layout: HarnessCheckpointLayout, *, workspace: bytes, state: bytes
    ) -> None:
        info = await self._inspect_owned_container_async()
        if info is None or info.get("Mounts") or info["State"]["Running"]:
            raise ValueError("Restore requires a fresh stopped owned container without mounts")
        uid, gid = await self._docker_user_async(info["Config"].get("User", ""))
        await self._docker_async("start", self._container)
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
        await self._docker_async("exec", self._container, "/bin/sh", "-c", script)
        parents = await asyncio.to_thread(CheckpointArchive.parents, root=layout.state_root, uid=uid, gid=gid)
        for data in (parents, workspace, state):
            await self._docker_async("cp", "--archive", "-", f"{self._container}:/", input_data=data)

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
        directories: set[PurePosixPath] = {PurePosixPath(self.HARNESS_HOME.lstrip("/"))}
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
                if stream.tell() > self.MAX_WORKSPACE_ARCHIVE_BYTES:
                    raise ValueError("Workspace archive exceeds the preparation budget")
            for path in paths:
                if not path.is_file():
                    continue
                info = archive.gettarinfo(str(path), arcname=str(root / path.relative_to(self.workspace).as_posix()))
                info.uid, info.gid, info.uname, info.gname = uid, gid, "", ""
                if stream.tell() + info.size > self.MAX_WORKSPACE_ARCHIVE_BYTES:
                    raise ValueError("Workspace archive exceeds the preparation budget")
                with path.open("rb") as source:
                    archive.addfile(info, source)
        if stream.tell() > self.MAX_WORKSPACE_ARCHIVE_BYTES:
            raise ValueError("Workspace archive exceeds the preparation budget")
        return stream.getvalue()

    async def _inspect_owned_container_async(self) -> dict[str, Any] | None:
        output = await self._docker_async(
            "container", "ls", "--all", "--quiet", "--filter", f"name=^/{self._container}$"
        )
        if not output.strip():
            return None
        info = json.loads(await self._docker_async("inspect", self._container))[0]
        labels = info["Config"].get("Labels", {})
        if (
            labels.get("pyrit.execution") != str(self.execution.id)
            or labels.get("pyrit.owner") != self.execution.owner_id
        ):
            raise RuntimeError("Refusing to stop a container whose ownership labels do not match")
        return info

    async def _collect_artifacts_async(self) -> None:
        for relative in self.execution.profile.artifact_paths:
            try:
                data = await self._docker_archive_async(
                    path=f"{self.container_workspace}/{relative}",
                    maximum_bytes=self.execution.profile.max_artifact_bytes + 65536,
                )
                await asyncio.to_thread(self._save_tar_artifact, relative=relative, data=data)
                self.execution.artifacts.append(relative)
            except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
                self.execution.artifact_errors.append(f"{relative}: {error}")

    def _save_tar_artifact(self, *, relative: str, data: bytes) -> None:
        content = self._read_tar_file(data=data, maximum_bytes=self.execution.profile.max_artifact_bytes)
        self._artifact_destination(relative).write_bytes(content)

    async def _docker_archive_async(self, *, path: str, maximum_bytes: int) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "cp",
            f"{self._container}:{path}",
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

    async def _docker_async(
        self,
        *args: str,
        environment: dict[str, str] | None = None,
        input_data: bytes | None = None,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if input_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(environment or {})},
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


class DockerProvider(ExecutionEnvironmentProvider):
    """The first-party isolated reference provider."""

    @property
    def info(self) -> EnvironmentProviderInfo:
        """Containers from pinned images, with tested stopped-filesystem checkpoints."""
        return EnvironmentProviderInfo(
            name=AgentEnvironment.DOCKER.value,
            display_name="Docker container",
            description="Runs the harness in a fresh labeled container created from a pinned image.",
            capabilities=EnvironmentCapabilities(isolated=True, checkpoint=True),
        )

    def validate_template(self, template: EnvironmentTemplate) -> None:
        """
        Require an image and reject settings this provider would silently ignore.

        Raises:
            ValueError: No image is configured or the template carries provider settings.
        """
        if not template.image:
            raise ValueError("A Docker image is required")
        if template.environment_settings:
            raise ValueError(
                "The Docker provider is configured through typed template fields, not environment_settings"
            )

    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        """
        Bind a Docker session.

        Returns:
            ExecutionSession: The unstarted session.
        """
        return DockerSession(execution=execution, directory=directory)
