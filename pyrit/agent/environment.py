# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The execution-environment seam: providers own resources, the runtime owns policy.

A provider allocates and releases the place where a harness runs and exposes its ACP byte
streams. It never decides timeouts, budgets, cancellation, approvals, redaction, or what the
harness does; those stay with ``AgentExecutionManager``.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from threading import RLock
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict

from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_environment import EnvironmentProviderInfo
from pyrit.models.agent_execution import AgentExecution, EnvironmentTemplate


class AcpByteReader(Protocol):
    """The reading half of an ACP byte stream, satisfied by asyncio and SSH stream readers."""

    async def readline(self) -> bytes:  # pyrit-async-suffix-exempt
        """Read one newline-terminated frame, or empty bytes at end of stream."""
        ...

    async def read(self, n: int = -1) -> bytes:  # pyrit-async-suffix-exempt
        """Read up to ``n`` bytes, or empty bytes at end of stream."""
        ...


class AcpByteWriter(Protocol):
    """The writing half of an ACP byte stream."""

    def write(self, data: bytes) -> None:
        """Buffer bytes for transmission."""
        ...

    async def drain(self) -> None:  # pyrit-async-suffix-exempt
        """Wait until buffered bytes are flushed."""
        ...

    def close(self) -> None:
        """Close the stream; the harness sees end of input."""
        ...


class CheckpointRestore(BaseModel):
    """Validated archives to place into a fresh environment before the harness starts."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    layout: HarnessCheckpointLayout
    workspace: bytes
    state: bytes


class ExecutionSpec(BaseModel):
    """
    Everything a provider needs to start one harness; built by the runtime, never persisted.

    ``environment`` carries adapter launch settings and the scoped inference connection.
    ``credentials`` are explicit credential values; providers pass them to the harness and
    must never log, journal, or store them.
    """

    model_config = ConfigDict(frozen=True)
    command: tuple[str, ...]
    environment: dict[str, str]
    credentials: dict[str, str]
    restore: CheckpointRestore | None = None


class ExecutionSession(ABC):
    """One allocation's resources, bound to its durable record before anything is acquired."""

    def __init__(self, *, execution: AgentExecution, directory: Path) -> None:
        """Bind the durable identity; subclasses must not acquire resources here."""
        self.execution = execution
        self.directory = directory

    @property
    @abstractmethod
    def harness_home(self) -> str:
        """The private home path as the harness will see it."""

    @property
    @abstractmethod
    def agent_cwd(self) -> str:
        """The workspace path in the harness's filesystem namespace."""

    @property
    @abstractmethod
    def acp_reader(self) -> AcpByteReader:
        """The harness's ACP output; available after a successful launch."""

    @property
    @abstractmethod
    def acp_writer(self) -> AcpByteWriter:
        """The harness's ACP input; available after a successful launch."""

    @property
    @abstractmethod
    def stderr(self) -> AcpByteReader:
        """The harness's diagnostic output; available after a successful launch."""

    def reserve(self) -> None:
        """Record deterministic resource identifiers so a crash during allocation stays reconcilable."""
        return

    async def inference_endpoint_async(self, *, relay_url: str) -> str:
        """
        Translate the backend relay URL into one the harness can reach.

        Returns:
            str: The relay base URL inside the environment's network namespace.

        Raises:
            ValueError: The relay is not reachable from this environment as configured.
        """
        return relay_url

    @abstractmethod
    async def launch_async(self, spec: ExecutionSpec) -> None:
        """
        Prepare the workspace, acquire resources, and start the harness.

        Raises:
            ValueError: The specification or starting state is unsupported.
            RuntimeError: Resource acquisition failed.
        """

    @abstractmethod
    async def close_async(self) -> None:
        """Release exactly the owned resources, collect artifacts, and stay idempotent."""

    async def capture_checkpoint_async(
        self,
        *,
        layout: HarnessCheckpointLayout,
        secrets: tuple[str, ...],
    ) -> tuple[bytes, bytes]:
        """
        Stop the harness and capture one consistent filesystem cut.

        Returns:
            tuple[bytes, bytes]: Workspace and native state archives.

        Raises:
            NotImplementedError: The provider declares no checkpoint capability.
        """
        raise NotImplementedError(
            f"Environment provider '{self.execution.profile.environment}' has no checkpoint capability"
        )


class ExecutionEnvironmentProvider(ABC):
    """Trusted implementation code for one kind of place to run a harness."""

    @property
    @abstractmethod
    def info(self) -> EnvironmentProviderInfo:
        """The immutable capability and authoring description."""

    def validate_template(self, template: EnvironmentTemplate) -> None:
        """
        Reject templates this provider cannot honor, before any target or execution exists.

        Raises:
            ValueError: The template is incompatible with this provider.
        """
        return

    @abstractmethod
    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        """
        Bind a session to a record without acquiring resources.

        Returns:
            ExecutionSession: The unstarted session, also used to release interrupted allocations.
        """


class EnvironmentProviderRegistry:
    """Setup-owned trusted providers, not import strings accepted from the UI."""

    _singleton: ClassVar["EnvironmentProviderRegistry | None"] = None
    _singleton_lock: ClassVar[RLock] = RLock()

    def __init__(self) -> None:
        """Create a registry with the first-party providers."""
        from pyrit.agent.environments.docker import DockerProvider
        from pyrit.agent.environments.local import LocalProvider

        self._lock = RLock()
        providers: tuple[ExecutionEnvironmentProvider, ...] = (DockerProvider(), LocalProvider())
        self._providers: dict[str, ExecutionEnvironmentProvider] = {
            provider.info.name: provider for provider in providers
        }

    @classmethod
    def get_registry_singleton(cls) -> "EnvironmentProviderRegistry":
        """
        Get current setup provider registrations.

        Returns:
            EnvironmentProviderRegistry: Current registry, constructed lazily.
        """
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_registry_singleton(cls) -> None:
        """Drop custom registrations; historical records need no provider to be read."""
        with cls._singleton_lock:
            cls._singleton = None

    def register(self, provider: ExecutionEnvironmentProvider) -> None:
        """
        Install a trusted provider before creating templates/targets that refer to it.

        Raises:
            ValueError: A provider already owns the requested name.
            TypeError: The implementation does not implement the provider contract.
        """
        if not isinstance(provider, ExecutionEnvironmentProvider):
            raise TypeError("Environment providers must implement ExecutionEnvironmentProvider")
        name = provider.info.name
        with self._lock:
            if name in self._providers and self._providers[name] is not provider:
                raise ValueError(f"Environment provider '{name}' is already registered")
            self._providers[name] = provider

    def get(self, name: str) -> ExecutionEnvironmentProvider:
        """
        Resolve an installed provider; unknown names never silently fall back.

        Returns:
            ExecutionEnvironmentProvider: The registered implementation.

        Raises:
            ValueError: No provider is registered.
        """
        with self._lock:
            if name not in self._providers:
                raise ValueError(
                    f"Environment provider '{name}' is unavailable; register it before creating this target"
                )
            return self._providers[name]

    def list_providers(self) -> list[EnvironmentProviderInfo]:
        """
        Describe installed providers for authoring surfaces.

        Returns:
            list[EnvironmentProviderInfo]: Immutable authoring metadata.
        """
        with self._lock:
            return [provider.info.model_copy(deep=True) for _, provider in sorted(self._providers.items())]


def validate_environment_template(template: EnvironmentTemplate, *, require_consent: bool = True) -> None:
    """
    Validate a template against its installed provider; consent belongs to composition, not registration.

    Raises:
        ValueError: The provider is unavailable, rejects the template, or lacks required consent.
    """
    provider = EnvironmentProviderRegistry.get_registry_singleton().get(template.environment)
    if require_consent and not provider.info.capabilities.isolated and not template.local_execution_acknowledged:
        raise ValueError(
            f"Environment provider '{template.environment}' is NOT sandboxed; "
            "set local_execution_acknowledged=True explicitly"
        )
    provider.validate_template(template)


def create_execution_session(*, execution: AgentExecution, directory: Path) -> ExecutionSession:
    """
    Bind a session for a record through its installed provider.

    Returns:
        ExecutionSession: The unstarted session.

    Raises:
        ValueError: The record's provider is not installed.
    """
    provider = EnvironmentProviderRegistry.get_registry_singleton().get(execution.profile.environment)
    return provider.create_session(execution=execution, directory=directory)
