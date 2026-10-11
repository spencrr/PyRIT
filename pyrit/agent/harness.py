# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Trusted launch adapters; protocol and resource lifecycle remain independently owned."""

import json
from abc import ABC, abstractmethod
from threading import RLock
from typing import ClassVar

from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_execution import HarnessProfile
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.models.model_inference import InferenceWireApi


class HarnessAdapter(ABC):
    """Translate model/launch settings only; do not execute tools or provision resources."""

    @property
    @abstractmethod
    def info(self) -> HarnessAdapterInfo:
        """The immutable capability and authoring description."""

    @property
    def checkpoint_layout(self) -> HarnessCheckpointLayout | None:
        """The tested native-state layout, or no checkpoint support."""
        return None

    @abstractmethod
    def launch_environment(self, *, home: str) -> dict[str, str]:
        """Supply harness-specific settings for its private home."""

    def inference_environment(
        self,
        *,
        model: str,
        wire_api: InferenceWireApi,
        base_url: str,
        token: str,
    ) -> dict[str, str]:
        """
        Translate a scoped relay connection, without access to provider credentials.

        Raises:
            ValueError: The adapter does not support target-backed model access.
        """
        raise ValueError("This harness needs a provider-configuration adapter for target-backed BYOK")


class _CopilotAdapter(HarnessAdapter):
    @property
    def checkpoint_layout(self) -> HarnessCheckpointLayout:
        return HarnessCheckpointLayout(
            version="copilot-session-state-v1",
            agent_versions=("1.0.93",),
            state_root="/home/agent/.copilot/session-state",
        )

    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="copilot",
            display_name="GitHub Copilot",
            default_command=("copilot", "--acp", "--stdio"),
            wire_apis=(InferenceWireApi.CHAT_COMPLETIONS, InferenceWireApi.RESPONSES),
            native_model_credentials=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"COPILOT_HOME": f"{home}/.copilot"}

    def inference_environment(
        self,
        *,
        model: str,
        wire_api: InferenceWireApi,
        base_url: str,
        token: str,
    ) -> dict[str, str]:
        return {
            "COPILOT_PROVIDER_BASE_URL": base_url,
            "COPILOT_PROVIDER_TYPE": "openai",
            "COPILOT_PROVIDER_WIRE_API": wire_api.value,
            "COPILOT_PROVIDER_API_KEY": token,
            "COPILOT_MODEL": model,
        }


class _OpenCodeAdapter(HarnessAdapter):
    @property
    def checkpoint_layout(self) -> HarnessCheckpointLayout:
        return HarnessCheckpointLayout(
            version="opencode-sqlite-v1",
            agent_versions=("1.18.35",),
            state_root="/home/agent/.local/share/opencode",
        )

    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="opencode",
            display_name="OpenCode",
            default_command=("opencode", "acp"),
            wire_apis=(InferenceWireApi.CHAT_COMPLETIONS,),
            native_model_credentials=(
                "OPENCODE_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_GENERATIVE_AI_API_KEY",
            ),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": "ask", "autoupdate": False, "share": "disabled"})}

    def inference_environment(
        self,
        *,
        model: str,
        wire_api: InferenceWireApi,
        base_url: str,
        token: str,
    ) -> dict[str, str]:
        config = {
            "model": f"pyrit/{model}",
            "small_model": f"pyrit/{model}",
            "enabled_providers": ["pyrit"],
            "permission": "ask",
            "autoupdate": False,
            "share": "disabled",
            "provider": {
                "pyrit": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "PyRIT execution model",
                    "options": {"baseURL": base_url.rstrip("/"), "apiKey": "{env:PYRIT_RELAY_TOKEN}"},
                    "models": {model: {"name": model, "limit": {"context": 128000, "output": 8192}}},
                }
            },
        }
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(config), "PYRIT_RELAY_TOKEN": token}


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
        adapters = (_CopilotAdapter(), _OpenCodeAdapter(), _GenericAcpAdapter())
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


def validate_harness_authentication(*, harness: HarnessProfile, target_backed: bool) -> None:
    """
    Validate credential delivery consistently across all caller surfaces.

    Raises:
        ValueError: Settings override execution configuration or conflict with the adapter.
    """
    adapter = HarnessAdapterRegistry.get_registry_singleton().get(harness.harness_kind)
    reserved = {
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
        "PYRIT_RELAY_TOKEN",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "COPILOT_HOME",
        "COPILOT_PROVIDER_BASE_URL",
        "COPILOT_PROVIDER_TYPE",
        "COPILOT_PROVIDER_WIRE_API",
        "COPILOT_PROVIDER_API_KEY",
        "COPILOT_MODEL",
    }
    conflicts = {name for name in harness.credential_env if name.upper() in reserved}
    if conflicts:
        raise ValueError(
            f"Credential references cannot override execution configuration: {', '.join(sorted(conflicts))}"
        )
    if not target_backed:
        return
    if harness.authentication_method is not None:
        raise ValueError("The current BYOK adapters do not support ACP authentication_method; remove it explicitly")
    native = {name.upper() for name in adapter.info.native_model_credentials}
    conflicts = {name for name in harness.credential_env if name.upper() in native}
    if conflicts:
        raise ValueError(
            "BYOK cannot forward native model credentials: "
            f"{', '.join(sorted(conflicts))}. Use separately scoped, tool-specific references."
        )


def validate_inference_adapter(*, kind: str, wire_api: InferenceWireApi) -> None:
    """
    Reject unsupported model access before provisioning.

    Raises:
        ValueError: No compatible model adapter is installed.
    """
    adapter = HarnessAdapterRegistry.get_registry_singleton().get(kind)
    if not adapter.info.wire_apis:
        raise ValueError("This harness requires a provider-configuration adapter for target-backed BYOK")
    if wire_api not in adapter.info.wire_apis:
        raise ValueError(f"{adapter.info.display_name} adapter does not support {wire_api.value} model inference")


def launch_environment(*, kind: str, home: str) -> dict[str, str]:
    """
    Build harness-specific settings using the installed adapter.

    Returns:
        dict[str, str]: Settings for the private execution environment.
    """
    return HarnessAdapterRegistry.get_registry_singleton().get(kind).launch_environment(home=home)


def inference_environment(
    *,
    kind: str,
    model: str,
    wire_api: InferenceWireApi,
    base_url: str,
    token: str,
) -> dict[str, str]:
    """
    Pass only the scoped relay connection to the adapter, not upstream credentials.

    Returns:
        dict[str, str]: Ephemeral provider settings for the tested process.
    """
    validate_inference_adapter(kind=kind, wire_api=wire_api)
    return (
        HarnessAdapterRegistry.get_registry_singleton()
        .get(kind)
        .inference_environment(
            model=model,
            wire_api=wire_api,
            base_url=base_url,
            token=token,
        )
    )
