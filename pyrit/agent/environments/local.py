# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local subprocess execution: a private workspace and home, but not a sandbox."""

import asyncio
import shutil
from pathlib import Path

from pyrit.agent.environment import ExecutionEnvironmentProvider, ExecutionSession, ExecutionSpec
from pyrit.agent.environments.subprocess_session import SubprocessSession
from pyrit.models.agent_environment import EnvironmentCapabilities, EnvironmentProviderInfo
from pyrit.models.agent_execution import AgentEnvironment, AgentExecution, AgentWorkspaceBase, EnvironmentTemplate


class LocalSession(SubprocessSession):
    """Run the harness as the backend's own OS user with a fresh empty workspace."""

    @property
    def harness_home(self) -> str:
        """The private host home directory."""
        return str(self.home)

    @property
    def agent_cwd(self) -> str:
        """The host workspace path, including the relative working directory."""
        return str(self.workspace / self.execution.profile.working_directory)

    async def launch_async(self, spec: ExecutionSpec) -> None:
        """Start the harness in the prepared workspace with a private home."""
        await self._prepare_workspace_async()
        environment = {
            **self._inherited_environment(),
            **spec.credentials,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "APPDATA": str(self.home / "AppData"),
            "LOCALAPPDATA": str(self.home / "LocalAppData"),
            **spec.environment,
        }
        await asyncio.to_thread(Path(self.agent_cwd).mkdir, parents=True, exist_ok=True)
        await self._spawn_async(command=spec.command, cwd=self.agent_cwd, environment=environment)

    async def close_async(self) -> None:
        """Stop the owned process tree, keep requested artifacts, and remove runtime directories."""
        await self._stop_owned_process_async(kill_group=True)
        # Artifacts exist only while the workspace does; a repeated close must not invent errors.
        if await asyncio.to_thread(self.workspace.exists):
            await self._collect_artifacts_async()
        await asyncio.to_thread(self._remove_runtime_directories)

    async def _collect_artifacts_async(self) -> None:
        for relative in self.execution.profile.artifact_paths:
            try:
                await asyncio.to_thread(self._copy_artifact, relative)
                self.execution.artifacts.append(relative)
            except (OSError, ValueError, RuntimeError) as error:
                self.execution.artifact_errors.append(f"{relative}: {error}")

    def _copy_artifact(self, relative: str) -> None:
        source = self.workspace / relative
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(self.workspace.resolve()):
            raise ValueError("Artifact escapes the workspace")
        self._validate_artifact(source)
        shutil.copyfile(source, self._artifact_destination(relative))


class LocalProvider(ExecutionEnvironmentProvider):
    """The first-party unsandboxed provider for development and CI."""

    @property
    def info(self) -> EnvironmentProviderInfo:
        """Local execution shares the backend user's access; consent is mandatory."""
        return EnvironmentProviderInfo(
            name=AgentEnvironment.LOCAL.value,
            display_name="Local process",
            description="Runs the harness on the backend host as the backend user. Not a sandbox.",
            capabilities=EnvironmentCapabilities(isolated=False),
        )

    def validate_template(self, template: EnvironmentTemplate) -> None:
        """
        Reject image-provided workspaces and settings this provider would silently ignore.

        Raises:
            ValueError: The template requests an image workspace or carries provider settings.
        """
        if template.workspace_base == AgentWorkspaceBase.IMAGE:
            raise ValueError("An image-provided workspace requires a Docker environment")
        if template.environment_settings:
            raise ValueError("The local provider is configured through typed template fields, not environment_settings")

    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        """
        Bind a local session.

        Returns:
            ExecutionSession: The unstarted session.
        """
        return LocalSession(execution=execution, directory=directory)
