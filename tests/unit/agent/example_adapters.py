# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Reference adapter for a harness PyRIT does not ship.

OpenCode 1.18.35 was validated during development against the same ACP core as Copilot.
It is kept here, outside the package, as the model for registering a third-party adapter:
declare the names the adapter reserves, translate its launch configuration, and never
read provider credentials.
"""

import json

from pyrit.agent.harness import HarnessAdapter
from pyrit.models.harness_adapter import HarnessAdapterInfo

OPENCODE_COMMAND = ("opencode", "acp")


class OpenCodeAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="opencode",
            display_name="OpenCode",
            default_command=OPENCODE_COMMAND,
            reserved_environment=("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": "ask", "autoupdate": False, "share": "disabled"})}
