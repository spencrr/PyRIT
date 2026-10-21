# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import Parameter
from pyrit.models.agent_setup import HarnessProfileDefinition
from pyrit.registry import AgentConfigurationRegistry
from pyrit.setup.agent_initialization import AgentConfigurationInitializer, initializer_parameters, initializer_values


class HarnessProfileInitializer(AgentConfigurationInitializer):
    """Register one inert named harness profile, independently of model access and placement."""

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare the flat harness settings accepted by configuration files.

        Returns:
            list[Parameter]: The flat profile parameters, without nested inference requirements.
        """
        return initializer_parameters(model=HarnessProfileDefinition, exclude=frozenset({"inference_requirements"}))

    def definition(self) -> HarnessProfileDefinition:
        """
        Read and validate the configured profile.

        Returns:
            HarnessProfileDefinition: A named immutable profile.
        """
        return HarnessProfileDefinition.model_validate(initializer_values(self))

    def validate_params(self) -> None:
        """Validate parameter shape without registering or resolving credentials."""
        super().validate_params()
        self.definition()

    async def initialize_async(self) -> None:
        """Register the definition without starting the harness."""
        self.preflight(AgentConfigurationRegistry.get_registry_singleton())

    def preflight(self, presets: AgentConfigurationRegistry) -> None:
        """Register the inert definition in the supplied namespace."""
        definition = self.definition()
        presets.register_harness(
            name=definition.name,
            profile=definition,
            initializer=type(self).__name__,
        )
