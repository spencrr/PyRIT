# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Named, inert agent settings populated by ordinary PyRIT initializers."""

from threading import RLock
from typing import ClassVar, TypeVar

from pyrit.models import validate_registry_name
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile
from pyrit.models.agent_setup import (
    AgentPresetCatalog,
    AgentPresetSelection,
    RegisteredEnvironmentTemplate,
    RegisteredHarnessProfile,
)

Preset = TypeVar("Preset", HarnessProfile, EnvironmentTemplate)


class AgentConfigurationRegistry:
    """Setup-owned values, not a registry of live agents or resource providers."""

    _singleton: ClassVar["AgentConfigurationRegistry | None"] = None
    _singleton_lock: ClassVar[RLock] = RLock()

    def __init__(self) -> None:
        """Create an independent registry, also usable for isolated validation."""
        self._lock = RLock()
        self._harnesses: dict[str, HarnessProfile] = {}
        self._environments: dict[str, EnvironmentTemplate] = {}
        self._harness_sources: dict[str, str | None] = {}
        self._environment_sources: dict[str, str | None] = {}
        self._unavailable_targets: dict[str, str] = {}

    @classmethod
    def get_registry_singleton(cls) -> "AgentConfigurationRegistry":
        """
        Get the registry owned by the current setup lifecycle.

        Returns:
            AgentConfigurationRegistry: The active preset registry.
        """
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_registry_singleton(cls) -> None:
        """Drop setup state without changing any already-composed target."""
        with cls._singleton_lock:
            cls._singleton = None

    def register_harness(self, *, name: str, profile: HarnessProfile, initializer: str | None = None) -> None:
        """Register a harness; equal definitions are idempotent, conflicts fail."""
        profile = HarnessProfile.model_validate(profile.model_dump(include=set(HarnessProfile.model_fields)))
        with self._lock:
            self._register(name=name, value=profile, values=self._harnesses)
            self._harness_sources.setdefault(name, initializer)

    def register_environment(self, *, name: str, template: EnvironmentTemplate, initializer: str | None = None) -> None:
        """
        Register an environment without carrying local-execution consent.

        Raises:
            ValueError: A reusable template attempts to grant local-execution consent.
        """
        template = EnvironmentTemplate.model_validate(
            template.model_dump(include=set(EnvironmentTemplate.model_fields))
        )
        if template.local_execution_acknowledged:
            raise ValueError("Local-execution consent belongs to target composition, not a reusable template")
        with self._lock:
            self._register(name=name, value=template, values=self._environments)
            self._environment_sources.setdefault(name, initializer)

    def catalog(self) -> AgentPresetCatalog:
        """
        Snapshot presets and their origins without constructing targets or resolving secrets.

        Returns:
            AgentPresetCatalog: Detached canonical values and registration sources.
        """
        with self._lock:
            return AgentPresetCatalog(
                harness_profiles=[
                    RegisteredHarnessProfile(
                        name=name, profile=value.model_copy(deep=True), initializer=self._harness_sources[name]
                    )
                    for name, value in sorted(self._harnesses.items())
                ],
                environment_templates=[
                    RegisteredEnvironmentTemplate(
                        name=name, template=value.model_copy(deep=True), initializer=self._environment_sources[name]
                    )
                    for name, value in sorted(self._environments.items())
                ],
                unavailable_targets=dict(self._unavailable_targets),
            )

    def validate_selection(self, *, selection: AgentPresetSelection, configuration: AgentTargetConfiguration) -> None:
        """
        Require selected names to match the supplied snapshot, not silently resolve to new values.

        Raises:
            ValueError: A selected preset was removed or its settings differ.
        """
        with self._lock:
            if (
                selection.harness_profile is not None
                and self.get_harness(selection.harness_profile) != configuration.harness_profile
            ):
                raise ValueError("Selected harness profile changed; reselect it or customize without a preset")
            if selection.environment_template is not None:
                template = configuration.environment_template.model_copy(update={"local_execution_acknowledged": False})
                if self.get_environment(selection.environment_template) != template:
                    raise ValueError("Selected environment template changed; reselect it or customize without a preset")

    def get_harness(self, name: str) -> HarnessProfile:
        """
        Resolve a harness name.

        Returns:
            HarnessProfile: A detached value.
        """
        return self._get(name=name, values=self._harnesses, kind="harness profile")

    def get_environment(self, name: str) -> EnvironmentTemplate:
        """
        Resolve an environment name.

        Returns:
            EnvironmentTemplate: A detached value without execution consent.
        """
        return self._get(name=name, values=self._environments, kind="environment template")

    def list_harnesses(self) -> dict[str, HarnessProfile]:
        """
        List registered harness values.

        Returns:
            dict[str, HarnessProfile]: Detached named values.
        """
        with self._lock:
            return {name: value.model_copy(deep=True) for name, value in sorted(self._harnesses.items())}

    def list_environments(self) -> dict[str, EnvironmentTemplate]:
        """
        List registered environment values.

        Returns:
            dict[str, EnvironmentTemplate]: Detached named values.
        """
        with self._lock:
            return {name: value.model_copy(deep=True) for name, value in sorted(self._environments.items())}

    def set_target_unavailable(self, *, name: str, reason: str | None) -> None:
        """Record an explicit optional-composition failure, or clear a resolved one."""
        with self._lock:
            if reason is None:
                self._unavailable_targets.pop(name, None)
            else:
                self._unavailable_targets[name] = reason

    def unavailable_targets(self) -> dict[str, str]:
        """
        List optional compositions that were not registered.

        Returns:
            dict[str, str]: Target names and non-secret reasons.
        """
        with self._lock:
            return dict(self._unavailable_targets)

    def _register(self, *, name: str, value: Preset, values: dict[str, Preset]) -> None:
        validate_registry_name(name)
        with self._lock:
            if name in values and values[name] != value:
                raise ValueError(f"Conflicting agent preset '{name}'; use a new name or reinitialize")
            values.setdefault(name, value.model_copy(deep=True))

    def _get(self, *, name: str, values: dict[str, Preset], kind: str) -> Preset:
        with self._lock:
            if name not in values:
                available = ", ".join(sorted(values)) or "(none)"
                raise ValueError(f"Unknown {kind} '{name}'. Register it first. Available: {available}")
            return values[name].model_copy(deep=True)
