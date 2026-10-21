# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Reference adapter for a harness PyRIT does not ship.

OpenCode 1.18.35 was validated during development against the same ACP core as Copilot.
It is kept here, outside the package, as the model for registering a third-party adapter:
declare capabilities and reserved names, translate the scoped relay connection into the
harness's own configuration mechanism, and never read provider credentials.
"""

import json

from pyrit.agent.harness import HarnessAdapter
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.models.model_inference import InferenceWireApi

OPENCODE_COMMAND = ("opencode", "acp")


class OpenCodeAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="opencode",
            display_name="OpenCode",
            default_command=OPENCODE_COMMAND,
            wire_apis=(InferenceWireApi.CHAT_COMPLETIONS,),
            native_model_credentials=(
                "OPENCODE_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_GENERATIVE_AI_API_KEY",
            ),
            reserved_environment=("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"),
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
