# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.agent.runtime import peek_agent_execution_manager
from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.services.target_service import TargetService
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile
from pyrit.models.agent_setup import AgentPresetSelection, AgentTargetComposition, AgentTargetOrigin
from pyrit.registry import AgentConfigurationRegistry, InitializerRegistry, Registry, TargetRegistry
from pyrit.setup.agent_initialization import register_agent_target

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons, clear=True), patch.object(AgentConfigurationRegistry, "_singleton", None):
        yield


def config(*, local: bool = False) -> AgentTargetConfiguration:
    harness = HarnessProfile(harness_kind="acp", command=("team-agent", "--stdio"), credential_env=("TEST_TOOL_TOKEN",))
    template = (
        EnvironmentTemplate(environment="local", local_execution_acknowledged=True)
        if local
        else EnvironmentTemplate(environment="docker", image="team:1", docker_network="none", docker_cpus=2)
    )
    return AgentTargetConfiguration(harness_profile=harness, environment_template=template)


@pytest.mark.parametrize("local", [False, True])
async def test_api_created_agent_targets_expose_configuration_and_runtime_origin_async(
    local: bool, tmp_path: Path
) -> None:
    configuration = config(local=local)
    service = TargetService()
    before = peek_agent_execution_manager()
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name="agent", type="AgentTarget", params={"agent_configuration": configuration.model_dump(mode="json")}
        ),
        upload_directory=str(tmp_path),
        is_admin=True,
    )
    assert created.agent_configuration == configuration
    assert created.agent_configuration_provenance is not None
    assert created.agent_configuration_provenance.origin is AgentTargetOrigin.RUNTIME
    assert created.agent_configuration_provenance.presets == AgentPresetSelection()
    assert (await service.get_target_async(target_registry_name="agent")) == created
    assert (await service.list_targets_async()).items[0] == created
    # Provenance is registry metadata, not identity: the same configuration composes to the same target.
    assert (
        created.identifier.hash == TargetRegistry.get_registry_singleton().instances.get("agent").get_identifier().hash
    )
    assert peek_agent_execution_manager() is before


async def test_ordinary_targets_carry_no_agent_configuration_async() -> None:
    service = TargetService()
    created = await service.create_target_async(
        request=CreateTargetRequest(name="plain", type="TextTarget", params={}), is_admin=True
    )
    assert created.agent_configuration is None
    assert created.agent_configuration_provenance is None
    assert "agent_configuration" not in created.model_dump(mode="json")


async def test_initializer_targets_are_distinguished_from_session_copies_async() -> None:
    registry = InitializerRegistry.get_registry_singleton()
    for name, params in [
        ("harness_profile", {"name": "team", "harness_kind": "acp", "command": ["team-agent", "--stdio"]}),
        ("environment_template", {"name": "workspace", "environment": "docker", "image": "team:1"}),
    ]:
        await registry.create_and_configure(name, initializer_params=params).initialize_async()
    target = register_agent_target(
        AgentTargetComposition(name="configured", harness_profile="team", environment_template="workspace")
    )
    assert target is not None
    view = await TargetService().get_target_async(target_registry_name="configured")
    assert view.agent_configuration_provenance is not None
    assert view.agent_configuration_provenance.origin is AgentTargetOrigin.INITIALIZER
    assert view.agent_configuration == target.agent_configuration
    assert view.identifier.hash == target.get_identifier().hash
    catalog = AgentConfigurationRegistry.get_registry_singleton().catalog()
    assert catalog.harness_profiles[0].initializer == "HarnessProfileInitializer"
    assert catalog.environment_templates[0].initializer == "EnvironmentTemplateInitializer"


def test_catalog_returns_detached_canonical_models_and_clears_on_reset() -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    configuration = config()
    presets.register_harness(name="team", profile=configuration.harness_profile, initializer="Private")
    presets.register_environment(name="workspace", template=configuration.environment_template)
    presets.set_target_unavailable(name="pending", reason="Model target not registered")
    catalog = presets.catalog()
    assert catalog.harness_profiles[0].profile == configuration.harness_profile
    assert catalog.harness_profiles[0].initializer == "Private"
    assert catalog.unavailable_targets == {"pending": "Model target not registered"}
    # Catalog entries are detached snapshots of the registered values.
    assert catalog.environment_templates[0].template == presets.get_environment("workspace")
    assert catalog.environment_templates[0].template is not presets.get_environment("workspace")
    AgentConfigurationRegistry.reset_registry_singleton()
    empty = AgentConfigurationRegistry.get_registry_singleton().catalog()
    assert empty.harness_profiles == [] and empty.environment_templates == [] and empty.unavailable_targets == {}
