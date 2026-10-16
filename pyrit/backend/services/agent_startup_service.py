# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Save resolved agent settings as ordinary startup initializer entries."""

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from yaml.error import YAMLError as ConfigurationYamlError

from pyrit.backend.models.common import MAX_FILE_CONTENT_LENGTH
from pyrit.backend.models.configuration import SaveAgentStartupRequest, SavedAgentStartup
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.models.agent_setup import AgentTargetComposition
from pyrit.prompt_target import AgentTarget
from pyrit.registry import AgentConfigurationRegistry, InitializerRegistry, TargetRegistry
from pyrit.setup.agent_initialization import resolve_agent_configuration
from pyrit.setup.configuration_loader import ConfigurationLoader


async def save_agent_startup_async(
    *,
    request: SaveAgentStartupRequest,
    source: ConfigurationFileService,
) -> SavedAgentStartup:
    """
    Save a standalone snapshot without editing presets, registering a target, or provisioning.

    Returns:
        SavedAgentStartup: The durable definition and unchanged runtime status.

    Raises:
        ValueError: The definition cannot be represented faithfully or conflicts with existing settings.
    """
    configuration = request.configuration
    binding = configuration.model_binding
    if binding.target_registry_name is not None and binding.target_identifier_hash is None:
        raise ValueError("Saving a model binding requires a pinned model target; reselect its current configuration")
    if request.agent_presets is not None:
        AgentConfigurationRegistry.get_registry_singleton().validate_selection(
            selection=request.agent_presets,
            configuration=configuration,
        )
    registry = TargetRegistry.get_registry_singleton()
    existing = registry.instances.get(request.name)
    if existing is None:
        registry.instances.validate_name_available(request.name)
    target = registry.create_instance("AgentTarget", agent_configuration=configuration)
    if not isinstance(target, AgentTarget):
        raise ValueError("The registered AgentTarget implementation is not compatible")
    identity = target.get_identifier().hash
    if existing is not None and existing.get_identifier().hash != identity:
        raise ValueError("A different runtime target uses this name; save under a new startup name")
    resolved = target.agent_configuration
    suffix = identity[:12]
    harness_name, environment_name = f"saved_harness_{suffix}", f"saved_environment_{suffix}"
    harness = resolved.harness_profile.model_dump(mode="json", exclude={"inference_requirements"}, exclude_none=True)
    requirements = resolved.harness_profile.inference_requirements.model_dump(mode="json")
    harness.update({f"inference_{name}": value for name, value in requirements.items()})
    if resolved.environment_template.environment_settings:
        raise ValueError(
            "Startup authoring supports the first-party environment providers; register templates for "
            f"'{resolved.environment_template.environment}' from a Python initializer"
        )
    template = resolved.environment_template.model_dump(
        mode="json", exclude={"local_execution_acknowledged", "environment_settings"}, exclude_none=True
    )
    composition = AgentTargetComposition(
        **resolved.model_dump(exclude={"name", "model_binding", "harness_profile", "environment_template"}),
        name=request.name,
        configuration_name=resolved.name,
        expected_target_hash=identity,
        harness_profile=harness_name,
        environment_template=environment_name,
        model=resolved.model_binding.model,
        model_target=resolved.model_binding.target_registry_name,
        model_target_hash=resolved.model_binding.target_identifier_hash,
        wire_api=resolved.model_binding.wire_api,
        local_execution_acknowledged=resolved.environment_template.local_execution_acknowledged,
    )
    # Prove the persisted scalar/list initializer representation keeps the exact runtime identity.
    entries: list[dict[str, Any]] = [
        {"name": "harness_profile", "args": {"name": harness_name, **harness}},
        {"name": "environment_template", "args": {"name": environment_name, **template}},
        {"name": "agent_target", "args": composition.model_dump(mode="json", exclude_none=True)},
    ]
    _validate_roundtrip(entries=entries, expected_hash=identity)
    version = await source.amend_async(
        transform=lambda content: _append_entries(
            content=content,
            entries=entries,
            model_target=composition.model_target,
            expected_hash=identity,
        ),
        expected_version=request.version,
    )
    return SavedAgentStartup(
        name=request.name,
        source=source.source,
        version=version,
        target_identifier_hash=identity,
        model_target=composition.model_target,
    )


def _validate_roundtrip(*, entries: list[dict[str, Any]], expected_hash: str) -> None:
    registry = InitializerRegistry.get_registry_singleton()
    presets = AgentConfigurationRegistry()
    harness = registry.create_and_configure("harness_profile", initializer_params=entries[0]["args"])
    template = registry.create_and_configure("environment_template", initializer_params=entries[1]["args"])
    from pyrit.setup.agent_initialization import AgentConfigurationInitializer

    if not isinstance(harness, AgentConfigurationInitializer) or not isinstance(
        template, AgentConfigurationInitializer
    ):
        raise ValueError("Startup preset initializers must support isolated configuration validation")
    harness.preflight(presets)
    template.preflight(presets)
    initializer = registry.create_and_configure("agent_target", initializer_params=entries[2]["args"])
    if not isinstance(initializer, AgentConfigurationInitializer):
        raise ValueError("Startup target initializer must support isolated validation")
    composition = initializer.preflight(presets)
    if composition is None:
        raise ValueError("Startup initializer did not describe a target composition")
    configuration = resolve_agent_configuration(composition=composition, presets=presets)
    target = TargetRegistry.get_registry_singleton().create_instance("AgentTarget", agent_configuration=configuration)
    if target.get_identifier().hash != expected_hash:
        raise ValueError("These settings cannot be saved without changing target identity")


def _append_entries(
    *,
    content: str,
    entries: list[dict[str, Any]],
    model_target: str | None,
    expected_hash: str,
) -> str:
    if len(content) > MAX_FILE_CONTENT_LENGTH:
        raise ValueError("Startup configuration exceeds the editable size limit")
    try:
        original = safe_load_yaml(content)
        loader = ConfigurationLoader.from_dict(original)
    except (ConfigurationYamlError, TypeError) as error:
        raise ValueError("Repair invalid startup YAML in Configuration before saving an agent") from error
    yaml = YAML(typ="rt")
    yaml.version = (1, 1)
    yaml.preserve_quotes = True
    try:
        document = yaml.load(content)
    except YAMLError as error:
        raise ValueError("Startup YAML cannot be safely edited; repair it in Configuration first") from error
    if not isinstance(document, CommentedMap):
        raise ValueError("Startup configuration must be a YAML mapping")
    sequence = document.get("initializers")
    direct_keys = {key for key, _ in document.non_merged_items()}
    if "initializers" not in direct_keys or not isinstance(sequence, CommentedSeq) or not sequence:
        raise ValueError(
            "Declare an explicit nonempty initializers list before saving; implicit defaults must not be replaced"
        )
    if sequence.yaml_anchor() is not None:
        raise ValueError("Anchored initializer lists must be edited in Configuration to avoid changing aliases")
    if model_target is not None and loader.initialization_scripts:
        raise ValueError(
            "For startup model bindings, register dependencies in the initializers list, "
            "not later initialization_scripts"
        )
    changed = False
    expected_initializers = list(original["initializers"])
    for entry in entries:
        matches = [
            current
            for current in loader.initializer_configs
            if current.name == entry["name"] and current.args and current.args.get("name") == entry["args"]["name"]
        ]
        if matches:
            if len(matches) != 1 or matches[0].args != entry["args"]:
                raise ValueError(
                    f"Startup definition '{entry['args']['name']}' already exists; save a new name instead"
                )
            continue
        sequence.append(entry)
        expected_initializers.append(entry)
        changed = True
    if not changed:
        _validate_saved_entries(content=content, entries=entries, expected_hash=expected_hash)
        return content
    output = io.StringIO()
    # A source directive may have changed the emitter version during loading.
    yaml.version = (1, 1)
    yaml.dump(document, output)
    updated = output.getvalue()
    if len(updated) > MAX_FILE_CONTENT_LENGTH:
        raise ValueError("Updated startup configuration exceeds the editable size limit")
    restored = safe_load_yaml(updated)
    if restored != {**original, "initializers": expected_initializers}:
        raise ValueError("Editing this YAML would change configuration values; use Configuration instead")
    _validate_saved_entries(content=updated, entries=entries, expected_hash=expected_hash)
    return updated


def _validate_saved_entries(*, content: str, entries: list[dict[str, Any]], expected_hash: str) -> None:
    loader = ConfigurationLoader.from_dict(safe_load_yaml(content))
    saved_entries = []
    for entry in entries:
        matches = [
            current
            for current in loader.initializer_configs
            if current.name == entry["name"] and current.args and current.args.get("name") == entry["args"]["name"]
        ]
        if len(matches) != 1:
            raise ValueError("Serialized startup definition cannot be resolved unambiguously")
        saved_entries.append({"name": matches[0].name, "args": matches[0].args})
    _validate_roundtrip(entries=saved_entries, expected_hash=expected_hash)
