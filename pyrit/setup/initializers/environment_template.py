# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.agent.environment import validate_environment_template
from pyrit.models import Parameter
from pyrit.models.agent_execution import EnvironmentTemplate
from pyrit.models.agent_setup import EnvironmentTemplateDefinition
from pyrit.registry import AgentConfigurationRegistry
from pyrit.setup.agent_initialization import AgentConfigurationInitializer, initializer_parameters, initializer_values


class EnvironmentTemplateInitializer(AgentConfigurationInitializer):
    """Register one inert environment template; local-execution consent belongs to composition."""

    @property
    def supported_parameters(self) -> list[Parameter]:
        """
        The starting filesystem and resource policy, without execution consent.

        Provider-owned ``environment_settings`` are nested values; templates for registered providers
        are created from a Python initializer rather than the scalar/list parameter bag.
        """
        return initializer_parameters(
            model=EnvironmentTemplateDefinition,
            exclude=frozenset({"local_execution_acknowledged", "environment_settings"}),
        )

    def definition(self) -> EnvironmentTemplateDefinition:
        """
        Read and validate the configured template.

        Returns:
            EnvironmentTemplateDefinition: A named immutable template.
        """
        return EnvironmentTemplateDefinition.model_validate(initializer_values(self))

    def validate_params(self) -> None:
        """Validate parameters without provisioning or contacting Docker."""
        super().validate_params()
        self.definition()

    async def initialize_async(self) -> None:
        """Register the template without acquiring resources."""
        self.preflight(AgentConfigurationRegistry.get_registry_singleton())

    def preflight(self, presets: AgentConfigurationRegistry) -> None:
        """Register the inert definition in the supplied namespace after its provider accepts it."""
        definition = self.definition()
        template = EnvironmentTemplate.model_validate(definition.model_dump(exclude={"name"}))
        validate_environment_template(template, require_consent=False)
        presets.register_environment(name=definition.name, template=template, initializer=type(self).__name__)
