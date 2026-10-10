# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Harness-specific process configuration; ACP and resource ownership stay independent."""

import json

from pyrit.models.agent_execution import AgentHarnessKind, HarnessProfile
from pyrit.models.model_inference import InferenceWireApi


def validate_harness_authentication(*, harness: HarnessProfile, target_backed: bool) -> None:
    """
    Validate credential delivery consistently for Python, initializers, and REST.

    Raises:
        ValueError: Credentials override execution settings or enable incompatible native model access.
    """
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
    native_credentials = (
        {"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}
        if harness.harness_kind == AgentHarnessKind.COPILOT
        else {"OPENCODE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"}
    )
    conflicts = {name for name in harness.credential_env if name.upper() in native_credentials}
    if conflicts:
        raise ValueError(
            "BYOK cannot forward native model credentials: "
            f"{', '.join(sorted(conflicts))}. Use separately scoped, tool-specific references."
        )


def validate_inference_adapter(*, kind: AgentHarnessKind, wire_api: InferenceWireApi) -> None:
    """
    Reject unsupported provider configuration before provisioning.

    Raises:
        ValueError: This harness does not implement the requested provider configuration.
    """
    if kind == AgentHarnessKind.ACP:
        raise ValueError("Generic ACP requires a provider-configuration adapter for target-backed BYOK")
    if kind == AgentHarnessKind.OPENCODE and wire_api != InferenceWireApi.CHAT_COMPLETIONS:
        raise ValueError("OpenCode target-backed BYOK currently supports Chat Completions only")


def launch_environment(*, kind: AgentHarnessKind, home: str) -> dict[str, str]:
    """
    Build harness-owned settings without inheriting operator configuration.

    Returns:
        dict[str, str]: Non-secret harness settings for its private execution home.
    """
    if kind == AgentHarnessKind.COPILOT:
        return {"COPILOT_HOME": f"{home}/.copilot"}
    if kind == AgentHarnessKind.OPENCODE:
        return {
            "OPENCODE_CONFIG_CONTENT": json.dumps({"permission": "ask", "autoupdate": False, "share": "disabled"}),
        }
    return {}


def inference_environment(
    *,
    kind: AgentHarnessKind,
    model: str,
    wire_api: InferenceWireApi,
    base_url: str,
    token: str,
) -> dict[str, str]:
    """
    Configure the harness to use the execution-scoped inference relay.

    Returns:
        dict[str, str]: Ephemeral settings including only the relay credential, never the provider key.

    Raises:
        ValueError: No compatible provider-configuration adapter exists.
    """
    validate_inference_adapter(kind=kind, wire_api=wire_api)
    if kind == AgentHarnessKind.COPILOT:
        return {
            "COPILOT_PROVIDER_BASE_URL": base_url,
            "COPILOT_PROVIDER_TYPE": "openai",
            "COPILOT_PROVIDER_WIRE_API": wire_api.value,
            "COPILOT_PROVIDER_API_KEY": token,
            "COPILOT_MODEL": model,
        }
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
            },
        },
    }
    return {"OPENCODE_CONFIG_CONTENT": json.dumps(config), "PYRIT_RELAY_TOKEN": token}
