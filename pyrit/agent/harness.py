# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Trusted launch adapters; protocol and resource lifecycle remain independently owned."""

import os
from abc import ABC, abstractmethod
from threading import RLock
from typing import ClassVar

from pyrit.models.agent_execution import HarnessProfile
from pyrit.models.harness_adapter import HarnessAdapterInfo


class HarnessAdapter(ABC):
    """Translate model/launch settings only; do not execute tools or provision resources."""

    # Process identity and lookup paths belong to the execution, never to credential references.
    EXECUTION_RESERVED_ENVIRONMENT: ClassVar[frozenset[str]] = frozenset(
        {
            "HOME",
            "USERPROFILE",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "APPDATA",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
        }
    )

    @property
    @abstractmethod
    def info(self) -> HarnessAdapterInfo:
        """The immutable capability and authoring description."""

    @abstractmethod
    def launch_environment(self, *, home: str) -> dict[str, str]:
        """Supply harness-specific settings for its private home."""

    def validate_profile(self, *, harness: HarnessProfile) -> None:
        """
        Reject profiles this adapter cannot run, before any target or execution exists.

        Raises:
            ValueError: The profile is incompatible with this adapter.
        """
        return


class _CopilotAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="copilot",
            display_name="GitHub Copilot",
            default_command=("copilot", "--acp", "--stdio"),
            reserved_environment=("COPILOT_HOME",),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"COPILOT_HOME": f"{home}/.copilot"}


class _GenericAcpAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(name="acp", display_name="Custom ACP")

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {}


class HarnessAdapterRegistry:
    """Setup-owned trusted implementations, not import strings accepted from the UI."""

    _singleton: ClassVar["HarnessAdapterRegistry | None"] = None
    _singleton_lock: ClassVar[RLock] = RLock()

    def __init__(self) -> None:
        """Create a registry with the built-in adapters."""
        self._lock = RLock()
        adapters = (_CopilotAdapter(), _GenericAcpAdapter())
        self._adapters: dict[str, HarnessAdapter] = {adapter.info.name: adapter for adapter in adapters}

    @classmethod
    def get_registry_singleton(cls) -> "HarnessAdapterRegistry":
        """
        Get current setup adapter registrations.

        Returns:
            HarnessAdapterRegistry: Current registry, constructed lazily.
        """
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_registry_singleton(cls) -> None:
        """Drop custom registrations; historical evidence and resource cleanup need no adapter."""
        with cls._singleton_lock:
            cls._singleton = None

    def register(self, adapter: HarnessAdapter) -> None:
        """
        Install a trusted adapter before creating profiles/targets that refer to it.

        Raises:
            ValueError: An adapter already owns the requested name.
            TypeError: The implementation does not implement the adapter contract.
        """
        if not isinstance(adapter, HarnessAdapter):
            raise TypeError("Harness adapters must implement HarnessAdapter")
        name = adapter.info.name
        with self._lock:
            if name in self._adapters and self._adapters[name] is not adapter:
                raise ValueError(f"Harness adapter '{name}' is already registered")
            self._adapters[name] = adapter

    def get(self, name: str) -> HarnessAdapter:
        """
        Resolve an installed implementation; unknown names never silently use generic ACP.

        Returns:
            HarnessAdapter: The registered implementation.

        Raises:
            ValueError: No implementation is registered.
        """
        with self._lock:
            if name not in self._adapters:
                raise ValueError(f"Harness adapter '{name}' is unavailable; register it before creating this target")
            return self._adapters[name]

    def list_adapters(self) -> list[HarnessAdapterInfo]:
        """
        Describe registered implementations without launching or authenticating.

        Returns:
            list[HarnessAdapterInfo]: Immutable authoring metadata.
        """
        with self._lock:
            return [adapter.info.model_copy(deep=True) for _, adapter in sorted(self._adapters.items())]


def validate_harness_authentication(*, harness: HarnessProfile) -> None:
    """
    Validate credential delivery consistently across all caller surfaces.

    Raises:
        ValueError: Settings override execution configuration or conflict with the adapter.
    """
    adapter = HarnessAdapterRegistry.get_registry_singleton().get(harness.harness_kind)
    adapter.validate_profile(harness=harness)
    reserved = HarnessAdapter.EXECUTION_RESERVED_ENVIRONMENT | {
        name.upper() for name in adapter.info.reserved_environment
    }
    conflicts = {name for name in harness.credential_env if name.upper() in reserved}
    if conflicts:
        raise ValueError(
            f"Credential references cannot override execution configuration: {', '.join(sorted(conflicts))}"
        )


def resolve_credential_values(*, credential_env: tuple[str, ...]) -> dict[str, str]:
    """
    Resolve explicitly referenced credentials from the backend process, without persisting values.

    Returns:
        dict[str, str]: Named credential values for one launch.

    Raises:
        ValueError: A referenced credential is missing.
    """
    values: dict[str, str] = {}
    for name in credential_env:
        value = os.environ.get(name)
        if not value:
            raise ValueError(f"Required credential environment variable is missing: {name}")
        values[name] = value
    return values


def launch_environment(*, kind: str, home: str) -> dict[str, str]:
    """
    Build harness-specific settings using the installed adapter.

    Returns:
        dict[str, str]: Settings for the private execution environment.
    """
    return HarnessAdapterRegistry.get_registry_singleton().get(kind).launch_environment(home=home)
