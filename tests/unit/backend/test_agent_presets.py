# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrit.agent.runtime import peek_agent_execution_manager
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.routes.configuration import router
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
    presets = AgentConfigurationRegistry.get_registry_singleton()
    harness = HarnessProfile(harness_kind="opencode", permission_policy="ask", credential_env=("TEST_TOOL_TOKEN",))
    template = EnvironmentTemplate(
        environment="local" if local else "docker",
        image=None if local else "team:1",
        docker_network="none",
        docker_cpus=2,
    )
    presets.register_harness(name="team", profile=harness, initializer="PrivateHarnessInitializer")
    presets.register_environment(name="workspace", template=template, initializer="PrivateEnvironmentInitializer")
    return AgentTargetConfiguration(
        harness_profile=harness,
        environment_template=template.model_copy(update={"local_execution_acknowledged": local}),
    )


def request(configuration: AgentTargetConfiguration) -> CreateTargetRequest:
    return CreateTargetRequest(
        name="agent",
        type="AgentTarget",
        params={"agent_configuration": configuration.model_dump(mode="json")},
        agent_presets=AgentPresetSelection(harness_profile="team", environment_template="workspace"),
    )


def test_catalog_is_admin_only_and_returns_detached_canonical_models() -> None:
    configuration = config()
    before = peek_agent_execution_manager()
    app = FastAPI()
    app.include_router(router)
    with (
        TestClient(app) as client,
        patch.dict(
            "os.environ",
            {
                "PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "false",
                "TEST_TOOL_TOKEN": "not-for-export",
            },
        ),
    ):
        assert client.get("/config/agent-presets").status_code == 403
        app.dependency_overrides[require_admin] = lambda: None
        response = client.get("/config/agent-presets")
    assert response.status_code == 200
    body = response.json()
    assert body["harness_profiles"] == [
        {
            "name": "team",
            "profile": configuration.harness_profile.model_dump(mode="json"),
            "initializer": "PrivateHarnessInitializer",
        }
    ]
    assert body["environment_templates"][0]["initializer"] == "PrivateEnvironmentInitializer"
    assert "not-for-export" not in response.text
    body["environment_templates"][0]["template"]["image"] = "changed"
    assert AgentConfigurationRegistry.get_registry_singleton().get_environment("workspace").image == "team:1"
    assert peek_agent_execution_manager() is before


@pytest.mark.parametrize("local", [False, True])
async def test_create_validates_snapshot_and_returns_provenance_without_affecting_identity_async(local: bool) -> None:
    configuration = config(local=local)
    service = TargetService()
    before = peek_agent_execution_manager()
    created = await service.create_target_async(request=request(configuration))
    assert created.agent_configuration_provenance.origin is AgentTargetOrigin.RUNTIME
    assert created.agent_configuration_provenance.presets.harness_profile == "team"
    assert (await service.get_target_async(target_registry_name="agent")) == created
    assert (await service.list_targets_async()).items[0] == created
    custom = await service.create_target_async(
        request=CreateTargetRequest(
            name="custom",
            type="AgentTarget",
            params={"agent_configuration": configuration.model_dump(mode="json")},
        )
    )
    assert custom.identifier.hash == created.identifier.hash
    assert custom.agent_configuration_provenance.presets == AgentPresetSelection()
    assert peek_agent_execution_manager() is before


@pytest.mark.parametrize("change", ["removed", "profile", "template"])
async def test_changed_or_removed_preset_is_rejected_without_registration_async(change: str) -> None:
    configuration = config()
    create = request(configuration)
    if change == "removed":
        AgentConfigurationRegistry.reset_registry_singleton()
    else:
        updated = configuration.model_copy(
            update={"harness_profile": configuration.harness_profile.model_copy(update={"command": ("different",)})}
            if change == "profile"
            else {"environment_template": configuration.environment_template.model_copy(update={"image": "other:2"})}
        )
        create = request(updated)
    with pytest.raises(ValueError, match="Unknown|changed"):
        await TargetService().create_target_async(request=create)
    assert TargetRegistry.get_registry_singleton().instances.get("agent") is None


async def test_ordinary_target_cannot_claim_agent_preset_provenance_async() -> None:
    with pytest.raises(ValueError, match="require an AgentTarget"):
        await TargetService().create_target_async(
            request=CreateTargetRequest(
                type="TextTarget",
                agent_presets=AgentPresetSelection(harness_profile="team"),
            )
        )


async def test_initializer_targets_are_distinguished_from_session_copies_async() -> None:
    registry = InitializerRegistry.get_registry_singleton()
    for name, params in [
        ("harness_profile", {"name": "team", "harness_kind": "opencode"}),
        ("environment_template", {"name": "workspace", "image": "team:1"}),
    ]:
        await registry.create_and_configure(name, initializer_params=params).initialize_async()
    target = register_agent_target(
        AgentTargetComposition(name="configured", harness_profile="team", environment_template="workspace")
    )
    view = await TargetService().get_target_async(target_registry_name="configured")
    assert view.agent_configuration_provenance.origin is AgentTargetOrigin.INITIALIZER
    assert view.identifier.hash == target.get_identifier().hash
    catalog = AgentConfigurationRegistry.get_registry_singleton().catalog()
    assert catalog.harness_profiles[0].initializer == "HarnessProfileInitializer"
    assert catalog.environment_templates[0].initializer == "EnvironmentTemplateInitializer"


def test_optional_unavailable_targets_visible_and_origins_clear_on_reset() -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    config()
    presets.set_target_unavailable(name="pending", reason="Model target not registered")
    assert presets.catalog().unavailable_targets == {"pending": "Model target not registered"}
    AgentConfigurationRegistry.reset_registry_singleton()
    empty = AgentConfigurationRegistry.get_registry_singleton().catalog()
    assert empty.harness_profiles == [] and empty.environment_templates == [] and empty.unavailable_targets == {}
