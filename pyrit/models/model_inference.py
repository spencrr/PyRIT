# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provider-wire capabilities for a harness that owns its conversation and tools."""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class InferenceWireApi(str, Enum):
    """Standard model protocols understood by the relay."""

    CHAT_COMPLETIONS = "completions"
    RESPONSES = "responses"


class InferenceRequirements(BaseModel):
    """Required inference behavior, not a target class-name allowlist."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    wire_api: InferenceWireApi = InferenceWireApi.CHAT_COMPLETIONS
    streaming: bool = True
    tool_calls: bool = True
    input_modalities: frozenset[str] = frozenset({"text"})


class InferenceCapabilities(BaseModel):
    """An explicit implementation contract; model tool behavior still needs verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    wire_apis: frozenset[InferenceWireApi]
    streaming: bool
    tool_calls: bool
    input_modalities: frozenset[str]
    blocked_reason: str | None = None

    def incompatibilities(self, requirements: InferenceRequirements) -> list[str]:
        """
        Compare actual requirements with this target's inference implementation.

        Returns:
            list[str]: Reasons the binding cannot be used.
        """
        reasons: list[str] = []
        if self.blocked_reason:
            reasons.append(self.blocked_reason)
        if requirements.wire_api not in self.wire_apis:
            reasons.append(f"Target does not implement {requirements.wire_api.value} inference")
        if requirements.streaming and not self.streaming:
            reasons.append("Harness requires streaming inference")
        if requirements.tool_calls and not self.tool_calls:
            reasons.append("Harness requires tool-call passthrough without target-side execution")
        missing = requirements.input_modalities - self.input_modalities
        if missing:
            reasons.append(f"Unsupported input modalities: {', '.join(sorted(missing))}")
        return reasons
