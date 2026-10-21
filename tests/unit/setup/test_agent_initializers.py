# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.agent.runtime import peek_agent_execution_manager
from pyrit.memory import CentralMemory
from pyrit.models.agent_execution import (
    AgentPermissionPolicy,
    AgentTargetConfiguration,
    EnvironmentTemplate,
    HarnessProfile,
    ModelBinding,
)
from pyrit.models.agent_setup import AgentTargetComposition, EnvironmentTemplateDefinition, HarnessProfileDefinition
from pyrit.prompt_target import AgentTarget
from pyrit.registry import AgentConfigurationRegistry, InitializerRegistry, Registry, TargetRegistry
from pyrit.setup.agent_initialization import (
    preflight_agent_initializers,
    register_agent_target,
)
from pyrit.setup.configuration_loader import ConfigurationLoader
from pyrit.setup.initialization import reset_setup_registries
from pyrit.setup.pyrit_initializer import PyRITInitializer
from tests.unit.agent.test_inference_relay import CustomInferenceTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons, clear=True), patch.object(AgentConfigurationRegistry, "_singleton", None):
        yield


def configured(initializer_type: str, **params: object) -> PyRITInitializer:
    return InitializerRegistry.get_registry_singleton().create_and_configure(
        initializer_type, initializer_params=params
    )


def local_plan(*, consent: bool = True) -> list[PyRITInitializer]:
    return [
        configured("harness_profile", name="custom", harness_kind="acp", command=["python", "scripted-agent.py"]),
        configured("environment_template", name="local", environment="local"),
        configured(
            "agent_target",
            name="example",
            harness_profile="custom",
            environment_template="local",
            local_execution_acknowledged=consent,
        ),
    ]


async def test_composition_uses_existing_registry_and_golden_identity_without_provisioning_async() -> None:
    before = peek_agent_execution_manager()
    initializers = local_plan()
    for initializer in initializers:
        await initializer.initialize_async()
    registry = TargetRegistry.get_registry_singleton()
    target = registry.instances.get("example")
    direct = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            harness_profile=HarnessProfile(harness_kind="acp", command=("python", "scripted-agent.py")),
            environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True),
        )
    )
    # Composition through the registry yields the identity a direct Python construction has.
    assert target.get_identifier().hash == direct.get_identifier().hash
    assert target.agent_configuration.environment_template.local_execution_acknowledged
    assert not AgentConfigurationRegistry.get_registry_singleton().get_environment("local").local_execution_acknowledged
    metadata = registry.instances.get_entry("example").metadata
    assert metadata["agent_composition"]["harness_profile"] == "custom"
    assert "example" not in target.get_identifier().model_dump_json()
    assert "custom" not in target.get_identifier().model_dump_json()
    assert peek_agent_execution_manager() is before
    for initializer in initializers:
        await initializer.initialize_async()
    assert registry.instances.get("example") is target


async def test_full_configuration_initialization_is_repeatable_async() -> None:
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        initialization_scripts=[],
        env_files=[],
        env_akv_ref=[],
        initializers=[
            {"name": "harness_profile", "args": {"name": "copilot"}},
            {
                "name": "environment_template",
                "args": {"name": "image", "environment": "docker", "image": "receipt:latest"},
            },
            {
                "name": "agent_target",
                "args": {"name": "agent", "harness_profile": "copilot", "environment_template": "image"},
            },
        ],
    )
    await config.initialize_pyrit_async()
    target = TargetRegistry.get_registry_singleton().instances.get("agent")
    direct = AgentTarget(
        agent_configuration=AgentTargetConfiguration(
            environment_template=EnvironmentTemplate(environment="docker", image="receipt:latest")
        )
    )
    assert target.get_identifier().hash == direct.get_identifier().hash
    await config.initialize_pyrit_async()
    assert TargetRegistry.get_registry_singleton().instances.get("agent") is target


def test_execution_policy_defaults_and_bounds_match_runtime_configuration() -> None:
    reference_fields = {
        "name",
        "harness_profile",
        "environment_template",
        "model",
        "model_target",
        "model_target_hash",
        "configuration_name",
        "expected_target_hash",
        "wire_api",
        "required",
        "local_execution_acknowledged",
    }
    for name, field in AgentTargetComposition.model_fields.items():
        if name in reference_fields:
            continue
        runtime = AgentTargetConfiguration.model_fields[name]
        assert field.default == runtime.default
        assert field.metadata == runtime.metadata


def test_initializer_catalog_exposes_existing_flat_parameter_contract() -> None:
    registry = InitializerRegistry.get_registry_singleton()
    for name in ("harness_profile", "environment_template", "agent_target"):
        metadata = registry.get_registered_class_metadata(name)
        parameters = {parameter.name: parameter.model_dump(mode="json") for parameter in metadata.supported_parameters}
        assert parameters["name"]["required"] is True
        assert all(not isinstance(value, type) for parameter in parameters.values() for value in parameter.values())
        if name == "harness_profile":
            assert parameters["harness_kind"]["choices"] is None
            assert parameters["harness_kind"]["type_name"] == "str"
            assert parameters["command"]["type_name"] == "list[str]"
            assert parameters["command"]["default"] == ["copilot", "--acp", "--stdio"]
            assert parameters["credential_env"]["default"] == []
        if name == "environment_template":
            assert "local_execution_acknowledged" not in parameters
        if name == "agent_target":
            assert parameters["artifact_paths"]["type_name"] == "list[str]"
            assert parameters["artifact_paths"]["default"] == []


@pytest.mark.parametrize(("named_harness", "named_environment"), [(True, False), (False, True), (True, True)])
def test_definition_subclasses_do_not_change_composed_target_identity(
    *,
    named_harness: bool,
    named_environment: bool,
) -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    harness = HarnessProfile()
    environment = EnvironmentTemplate(environment="docker", image="receipt:latest")
    presets.register_harness(name="base_harness", profile=harness)
    presets.register_environment(name="base_environment", template=environment)
    presets.register_harness(
        name="private_harness",
        profile=HarnessProfileDefinition(name="team_alias", **harness.model_dump()) if named_harness else harness,
    )
    presets.register_environment(
        name="private_environment",
        template=EnvironmentTemplateDefinition(name="environment_alias", **environment.model_dump())
        if named_environment
        else environment,
    )
    baseline = register_agent_target(
        AgentTargetComposition(
            name="baseline",
            harness_profile="base_harness",
            environment_template="base_environment",
        )
    )
    composed = register_agent_target(
        AgentTargetComposition(
            name="private_target",
            harness_profile="private_harness",
            environment_template="private_environment",
        )
    )
    assert baseline is not None and composed is not None
    assert composed.get_identifier().hash == baseline.get_identifier().hash
    assert composed.profile == baseline.profile
    assert type(presets.get_harness("private_harness")) is HarnessProfile
    assert type(presets.get_environment("private_environment")) is EnvironmentTemplate
    assert type(presets.list_harnesses()["private_harness"]) is HarnessProfile
    assert type(presets.list_environments()["private_environment"]) is EnvironmentTemplate
    identity = composed.get_identifier().model_dump_json()
    assert all(
        name not in identity for name in ("team_alias", "environment_alias", "private_harness", "private_environment")
    )
    entry = TargetRegistry.get_registry_singleton().instances.get_entry("private_target")
    assert entry is not None
    assert entry.metadata["agent_composition"]["harness_profile"] == "private_harness"
    assert entry.metadata["agent_composition"]["environment_template"] == "private_environment"


@pytest.mark.parametrize("named_first", [True, False])
def test_base_and_named_preset_registration_is_idempotent_in_either_order(named_first: bool) -> None:
    presets = AgentConfigurationRegistry()
    harness = HarnessProfile(harness_kind="acp", command=("team-agent", "--stdio"), permission_policy="allow_once")
    environment = EnvironmentTemplate(environment="docker", image="team-image", working_directory="repo", docker_cpus=2)
    named_harness = HarnessProfileDefinition(name="first_alias", **harness.model_dump())
    named_environment = EnvironmentTemplateDefinition(name="first_alias", **environment.model_dump())
    harnesses = (named_harness, harness) if named_first else (harness, named_harness)
    environments = (named_environment, environment) if named_first else (environment, named_environment)
    for profile in harnesses:
        presets.register_harness(name="shared", profile=profile)
    for template in environments:
        presets.register_environment(name="shared", template=template)
    presets.register_harness(
        name="shared",
        profile=HarnessProfileDefinition(name="second_alias", **harness.model_dump()),
    )
    presets.register_environment(
        name="shared",
        template=EnvironmentTemplateDefinition(name="second_alias", **environment.model_dump()),
    )
    assert presets.get_harness("shared") == harness
    assert presets.get_environment("shared") == environment
    with pytest.raises(ValueError, match="Conflicting"):
        presets.register_harness(
            name="shared", profile=named_harness.model_copy(update={"permission_policy": AgentPermissionPolicy.DENY})
        )
    with pytest.raises(ValueError, match="Conflicting"):
        presets.register_environment(name="shared", template=named_environment.model_copy(update={"docker_cpus": 3}))


async def test_model_target_composition_is_pinned_and_names_are_not_identity_async() -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    presets.register_harness(name="one", profile=HarnessProfile())
    presets.register_harness(name="two", profile=HarnessProfile())
    presets.register_environment(
        name="docker", template=EnvironmentTemplate(environment="docker", image="receipt:latest")
    )
    registry = TargetRegistry.get_registry_singleton()
    source = CustomInferenceTarget()
    registry.instances.register(source, name="source")
    composition = AgentTargetComposition(
        name="agent", harness_profile="one", environment_template="docker", model_target="source", model="model"
    )
    first = register_agent_target(composition)
    second = register_agent_target(composition.model_copy(update={"name": "another", "harness_profile": "two"}))
    assert first.get_identifier().hash == second.get_identifier().hash
    assert first.agent_configuration.model_binding.target_identifier_hash == source.get_identifier().hash
    assert register_agent_target(composition) is first
    with pytest.raises(ValueError, match="own model source"):
        register_agent_target(composition.model_copy(update={"name": "source"}))


def test_presets_copy_values_and_conflicting_names_fail() -> None:
    presets = AgentConfigurationRegistry()
    profile = HarnessProfile(harness_kind="acp", command=("team-agent", "--stdio"))
    presets.register_harness(name="open", profile=profile)
    presets.register_harness(name="open", profile=profile.model_copy())
    snapshot = presets.list_harnesses()
    snapshot.clear()
    assert presets.get_harness("open") == profile
    assert presets.get_harness("open") is not profile
    with pytest.raises(ValueError, match="Conflicting"):
        presets.register_harness(name="open", profile=HarnessProfile())
    with pytest.raises(ValueError, match="Available: open"):
        presets.get_harness("absent")
    with pytest.raises(ValueError, match="consent"):
        presets.register_environment(
            name="local",
            template=EnvironmentTemplate(
                environment="local",
                local_execution_acknowledged=True,
            ),
        )


async def test_reset_discards_presets_but_not_existing_target_settings_async() -> None:
    for initializer in local_plan():
        await initializer.initialize_async()
    old_registry = AgentConfigurationRegistry.get_registry_singleton()
    target = TargetRegistry.get_registry_singleton().instances.get("example")
    identity = target.get_identifier().hash
    memory = CentralMemory.get_memory_instance()
    reset_setup_registries()
    assert AgentConfigurationRegistry.get_registry_singleton() is not old_registry
    assert AgentConfigurationRegistry.get_registry_singleton().list_harnesses() == {}
    assert CentralMemory.get_memory_instance() is memory
    assert target.get_identifier().hash == identity
    assert target.agent_configuration.harness_profile.command == ("python", "scripted-agent.py")


@pytest.mark.parametrize(
    ("initializer", "params", "match"),
    [
        ("harness_profile", {"name": "one", "command": ["python"], "harness_kind": "invalid/name"}, "name"),
        ("environment_template", {"name": "one", "environment": "docker"}, "image"),
        (
            "environment_template",
            {"name": "one", "environment": "local", "local_execution_acknowledged": True},
            "unknown",
        ),
        (
            "agent_target",
            {"name": "one", "harness_profile": "h", "environment_template": "e", "lifetime_seconds": -1},
            "lifetime",
        ),
        ("harness_profile", {"name": ["one", "two"]}, "exactly one"),
    ],
)
def test_invalid_parameters_fail_before_execution(initializer: str, params: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        configured(initializer, **params)
    assert AgentConfigurationRegistry.get_registry_singleton().list_harnesses() == {}


async def test_consent_and_unknown_references_are_not_optional_async() -> None:
    for initializer in local_plan()[:2]:
        await initializer.initialize_async()
    composition = AgentTargetComposition(
        name="agent", harness_profile="custom", environment_template="local", required=False
    )
    with pytest.raises(ValueError, match="NOT sandboxed"):
        register_agent_target(composition)
    with pytest.raises(ValueError, match="Unknown"):
        register_agent_target(composition.model_copy(update={"harness_profile": "missing"}))


async def test_missing_dependencies_are_required_unless_explicitly_optional_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    presets.register_harness(name="copilot", profile=HarnessProfile(credential_env=("TEST_TOOL_TOKEN",)))
    presets.register_environment(name="docker", template=EnvironmentTemplate(environment="docker", image="test"))
    composition = AgentTargetComposition(
        name="agent", harness_profile="copilot", environment_template="docker", model_target="source", model="model"
    )
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="register model targets before"):
            register_agent_target(composition)
        assert register_agent_target(composition.model_copy(update={"required": False})) is None
    assert "TEST_TOOL_TOKEN" in presets.unavailable_targets()["agent"]
    assert "unavailable" in caplog.text
    assert TargetRegistry.get_registry_singleton().instances.get("agent") is None
    TargetRegistry.get_registry_singleton().instances.register(CustomInferenceTarget(), name="source")
    with patch.dict(os.environ, {"TEST_TOOL_TOKEN": "sentinel-secret"}):
        target = register_agent_target(composition)
    assert presets.unavailable_targets() == {}
    assert "sentinel-secret" not in target.get_identifier().model_dump_json()
    assert "sentinel-secret" not in str(presets.list_harnesses())
    assert "sentinel-secret" not in caplog.text


def test_missing_source_cannot_hide_invalid_byok_authentication() -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    presets.register_harness(name="copilot", profile=HarnessProfile(credential_env=("GITHUB_TOKEN",)))
    presets.register_environment(name="docker", template=EnvironmentTemplate(environment="docker", image="test"))
    with pytest.raises(ValueError, match="native model credentials"):
        register_agent_target(
            AgentTargetComposition(
                name="agent",
                harness_profile="copilot",
                environment_template="docker",
                model_target="absent",
                required=False,
            )
        )


def test_optional_missing_source_does_not_hide_unsupported_harness_adapter() -> None:
    presets = AgentConfigurationRegistry.get_registry_singleton()
    presets.register_harness(name="custom", profile=HarnessProfile(harness_kind="acp", command=("custom-agent",)))
    presets.register_environment(name="image", template=EnvironmentTemplate(environment="docker", image="test"))
    with pytest.raises(ValueError, match="provider-configuration adapter"):
        register_agent_target(
            AgentTargetComposition(
                name="agent",
                harness_profile="custom",
                environment_template="image",
                model_target="absent",
                required=False,
            )
        )


def test_preflight_is_isolated_and_rejects_invalid_plans() -> None:
    before = AgentConfigurationRegistry.get_registry_singleton()
    preflight_agent_initializers(initializers=local_plan(), environment={})
    assert before.list_harnesses() == {}
    with pytest.raises(ValueError, match="NOT sandboxed"):
        preflight_agent_initializers(initializers=local_plan(consent=False), environment={})
    with pytest.raises(ValueError, match="Unknown harness"):
        preflight_agent_initializers(initializers=list(reversed(local_plan())), environment={})
    with pytest.raises(ValueError, match="Conflicting"):
        preflight_agent_initializers(
            initializers=[
                *local_plan(),
                configured("harness_profile", name="custom", harness_kind="acp", command=["team-agent", "--stdio"]),
            ],
            environment={},
        )


def test_missing_optional_credential_does_not_hide_incompatible_registered_model() -> None:
    from pyrit.prompt_target import TextTarget

    presets = AgentConfigurationRegistry.get_registry_singleton()
    presets.register_harness(name="copilot", profile=HarnessProfile(credential_env=("TEST_TOOL_TOKEN",)))
    presets.register_environment(name="image", template=EnvironmentTemplate(environment="docker", image="test"))
    TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name="not_inference")
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError, match="single-inference"):
        register_agent_target(
            AgentTargetComposition(
                name="agent",
                harness_profile="copilot",
                environment_template="image",
                model_target="not_inference",
                required=False,
            )
        )
    assert presets.unavailable_targets() == {}


@pytest.mark.parametrize("required", [True, False])
def test_reserved_name_fails_preflight_before_registry_replacement(required: bool) -> None:
    plan = local_plan()[:2]
    plan.append(
        configured(
            "agent_target",
            name="types",
            harness_profile="custom",
            environment_template="local",
            local_execution_acknowledged=True,
            required=required,
        )
    )
    before = TargetRegistry.get_registry_singleton()
    with pytest.raises(ValueError, match="reserved"):
        preflight_agent_initializers(initializers=plan, environment={})
    assert TargetRegistry.get_registry_singleton() is before
    assert AgentConfigurationRegistry.get_registry_singleton().list_harnesses() == {}


async def test_reserved_name_cannot_become_optional_unavailable_entry_async() -> None:
    for initializer in local_plan()[:2]:
        await initializer.initialize_async()
    with pytest.raises(ValueError, match="reserved"):
        register_agent_target(
            AgentTargetComposition(
                name="types",
                harness_profile="custom",
                environment_template="local",
                local_execution_acknowledged=True,
                required=False,
            )
        )


async def test_live_apply_refuses_opaque_model_construction_before_reset_async() -> None:
    config = ConfigurationLoader(
        initializers=[
            {"name": "harness_profile", "args": {"name": "copilot"}},
            {"name": "environment_template", "args": {"name": "docker", "environment": "docker", "image": "test"}},
            {
                "name": "agent_target",
                "args": {
                    "name": "agent",
                    "harness_profile": "copilot",
                    "environment_template": "docker",
                    "model_target": "private_model",
                },
            },
        ],
        env_files=[],
        initialization_scripts=[],
    )
    current = TargetRegistry.get_registry_singleton()
    source = CustomInferenceTarget()
    current.instances.register(source, name="private_model")
    with pytest.raises(ValueError, match="requires a restart"):
        await config.preflight_reinitialization_async(environment_values={})
    assert TargetRegistry.get_registry_singleton() is current
    assert current.instances.get("private_model") is source


async def test_live_apply_rebuilds_declarative_presets_safely_async() -> None:
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        initializers=[
            {
                "name": "harness_profile",
                "args": {"name": "custom", "harness_kind": "acp", "command": ["python", "agent.py"]},
            },
            {"name": "environment_template", "args": {"name": "local", "environment": "local"}},
            {
                "name": "agent_target",
                "args": {
                    "name": "agent",
                    "harness_profile": "custom",
                    "environment_template": "local",
                    "local_execution_acknowledged": True,
                },
            },
        ],
        env_files=[],
        initialization_scripts=[],
    )
    old = AgentConfigurationRegistry.get_registry_singleton()
    prepared = await config.preflight_reinitialization_async(environment_values={})
    assert old.list_harnesses() == {}
    await config.apply_prepared_reinitialization_async(prepared=prepared)
    assert AgentConfigurationRegistry.get_registry_singleton() is not old
    assert TargetRegistry.get_registry_singleton().instances.get("agent") is not None


async def test_private_stored_initializer_uses_same_registration_api_async(tmp_path: Path) -> None:
    script = tmp_path / "private_agent.py"
    script.write_text(
        """
from pyrit.setup.pyrit_initializer import PyRITInitializer
from pyrit.models.agent_execution import HarnessProfile, EnvironmentTemplate
from pyrit.models.agent_setup import AgentTargetComposition
from pyrit.registry import AgentConfigurationRegistry
from pyrit.setup.agent_initialization import register_agent_target

class PrivateAgentInitializer(PyRITInitializer):
    async def initialize_async(self):
        registry = AgentConfigurationRegistry.get_registry_singleton()
        registry.register_harness(name="private", profile=HarnessProfile(harness_kind="acp", command=("team-agent",)))
        registry.register_environment(
            name="team_image", template=EnvironmentTemplate(environment="docker", image="private.example/agent:1")
        )
        register_agent_target(AgentTargetComposition(name="team_agent", harness_profile="private",
                                                     environment_template="team_image"))
""",
        encoding="utf-8",
    )
    registry = InitializerRegistry.get_registry_singleton()
    registry.configure_custom_scripts_source(str(tmp_path))
    registry.register_stored_initializers(strict=True)
    initializer = registry.create_and_configure("private_agent")
    await initializer.initialize_async()
    await initializer.initialize_async()
    assert (
        TargetRegistry.get_registry_singleton().instances.get("team_agent").profile.image == "private.example/agent:1"
    )


def test_direct_configuration_still_requires_explicit_local_consent() -> None:
    from pyrit.prompt_target import AgentTarget

    with pytest.raises(ValueError, match="NOT sandboxed"):
        AgentTarget(
            agent_configuration=AgentTargetConfiguration(
                model_binding=ModelBinding(),
                environment_template=EnvironmentTemplate(environment="local"),
            )
        )
