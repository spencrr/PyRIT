# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import Parameter
from pyrit.models.agent_setup import AgentTargetComposition
from pyrit.registry import AgentConfigurationRegistry
from pyrit.setup.agent_initialization import (
    AgentConfigurationInitializer,
    initializer_parameters,
    initializer_values,
    register_agent_target,
)


class AgentTargetInitializer(AgentConfigurationInitializer):
    """Compose registered model, harness, and environment settings into one named target."""

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare the named references, consent, and bounded execution policy.

        Returns:
            list[Parameter]: The flat composition parameters.
        """
        return initializer_parameters(model=AgentTargetComposition)

    def definition(self) -> AgentTargetComposition:
        """
        Read and validate this composition's flat input.

        Returns:
            AgentTargetComposition: References resolved when initialization reaches this entry.
        """
        return AgentTargetComposition.model_validate(initializer_values(self))

    def validate_params(self) -> None:
        """Validate parameters before model/preset initializers have executed."""
        super().validate_params()
        self.definition()

    async def initialize_async(self) -> None:
        """Register the composition after its dependency initializers."""
        register_agent_target(self.definition())

    def preflight(self, presets: AgentConfigurationRegistry) -> AgentTargetComposition:
        """
        Describe the composition without resolving a model or constructing a target.

        Returns:
            AgentTargetComposition: The proposed references and execution policy.
        """
        return self.definition()
