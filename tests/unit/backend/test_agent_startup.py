# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import copy
import json
import os
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from pyrit.agent.runtime import peek_agent_execution_manager
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models.configuration import SaveAgentStartupRequest
from pyrit.backend.routes.configuration import router
from pyrit.backend.services.agent_startup_service import save_agent_startup_async
from pyrit.backend.services.configuration_file_service import ConfigurationFileConflictError, ConfigurationFileService
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile, ModelBinding
from pyrit.models.model_inference import InferenceRequirements
from pyrit.prompt_target import AgentTarget
from pyrit.registry import AgentConfigurationRegistry, Registry, TargetRegistry
from pyrit.setup.configuration_loader import ConfigurationLoader
from pyrit.setup.initialization import reset_setup_registries

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons, clear=True), patch.object(AgentConfigurationRegistry, "_singleton", None):
        yield


@pytest.fixture
def source(tmp_path: Path) -> ConfigurationFileService:
    file = tmp_path / "startup.yaml"
    file.write_text(
        "# Team configuration\nmemory_db_type: in_memory\nenv_files: []\ninitialization_scripts: []\n"
        'operator: "original" # preserve this comment\ninitializers:\n'
        "  - name: harness_profile # original private preset\n"
        "    args: {name: original, harness_kind: opencode}\nextensions: {note: unchanged}\n",
        encoding="utf-8",
    )
    return ConfigurationFileService(config_file_value=str(file))


def configuration() -> AgentTargetConfiguration:
    return AgentTargetConfiguration(
        name="display-name",
        harness_profile=HarnessProfile(
            harness_kind="opencode",
            credential_env=("TEST_REPO_KEY",),
            permission_policy="deny",
            inference_requirements=InferenceRequirements(input_modalities=frozenset({"text", "image"})),
        ),
        model_binding=ModelBinding(model="test-model"),
        environment_template=EnvironmentTemplate(
            image="team-image:1",
            docker_network="none",
            docker_cpus=2,
            working_directory="project",
        ),
        artifact_paths=("a,b.json", "space in name.txt"),
        max_artifact_bytes=12345,
        lifetime_seconds=812,
        capture_inference_content=True,
    )


async def request(source: ConfigurationFileService, *, name: str = "saved_agent") -> SaveAgentStartupRequest:
    _, version = await source.read_with_version_async()
    return SaveAgentStartupRequest(name=name, configuration=configuration(), version=version)


async def test_save_preserves_comments_settings_and_recreates_exact_target_after_restart_async(
    source: ConfigurationFileService,
) -> None:
    create = await request(source)
    expected = AgentTarget(agent_configuration=create.configuration).get_identifier().hash
    before = peek_agent_execution_manager()
    with patch.dict(os.environ, {"TEST_REPO_KEY": "not-in-configuration"}):
        result = await save_agent_startup_async(request=create, source=source)
        assert result.applied is False
        assert TargetRegistry.get_registry_singleton().instances.get(create.name) is None
        saved = await source.read_async()
        assert "# Team configuration" in saved
        assert '"original" # preserve this comment' in saved
        assert "# original private preset" in saved
        assert "not-in-configuration" not in saved
        assert "TEST_REPO_KEY" in saved
        assert safe_load_yaml(saved)["extensions"] == {"note": "unchanged"}
        reset_setup_registries()
        loader = ConfigurationLoader.from_yaml_file(source.source)
        await loader.initialize_pyrit_async()
    restored = TargetRegistry.get_registry_singleton().instances.get(create.name)
    assert restored is not None
    assert restored.get_identifier().hash == expected == result.target_identifier_hash
    assert restored.agent_configuration == create.configuration
    assert peek_agent_execution_manager() is before
    assert AgentConfigurationRegistry.get_registry_singleton().get_harness("original").harness_kind == "opencode"


async def test_startup_saves_developer_login_selection_without_resolving_or_storing_credentials_async(
    source: ConfigurationFileService,
) -> None:
    snapshot = AgentTargetConfiguration(
        harness_profile=HarnessProfile(use_github_cli_login=True),
        environment_template=EnvironmentTemplate(image="copilot:test"),
    )
    _, version = await source.read_with_version_async()
    with (
        patch.dict(os.environ, {"PYRIT_ALLOW_GITHUB_CLI_LOGIN": "true"}),
        patch("pyrit.agent.environment.github_cli_token_async", new_callable=AsyncMock) as login,
    ):
        result = await save_agent_startup_async(
            request=SaveAgentStartupRequest(name="dev_agent", configuration=snapshot, version=version),
            source=source,
        )
        content = await source.read_async()
        assert safe_load_yaml(content)["initializers"][-3]["args"]["use_github_cli_login"] is True
        assert result.target_identifier_hash == AgentTarget(agent_configuration=snapshot).get_identifier().hash
        login.assert_not_awaited()


@pytest.mark.parametrize("name", ["on", "yes", "off", "no"])
async def test_saved_yaml_preserves_strings_under_the_startup_loader_async(
    source: ConfigurationFileService,
    name: str,
) -> None:
    snapshot = AgentTargetConfiguration(
        name=name,
        model_binding=ModelBinding(model="OFF"),
        harness_profile=HarnessProfile(
            harness_kind="acp",
            command=("yes", "on", "OFF", "012", "1:20", "null"),
        ),
        environment_template=EnvironmentTemplate(image="off", working_directory="on", docker_network="none"),
        artifact_paths=("yes", "OFF", "null", "012"),
    )
    result = await save_agent_startup_async(
        request=SaveAgentStartupRequest(
            name=name,
            configuration=snapshot,
            version=(await source.read_with_version_async())[1],
        ),
        source=source,
    )
    content = await source.read_async()
    entries = safe_load_yaml(content)["initializers"]
    assert entries[-1]["args"]["name"] == name
    assert entries[-1]["args"]["configuration_name"] == name
    assert entries[-1]["args"]["model"] == "OFF"
    assert entries[-3]["args"]["command"] == list(snapshot.harness_profile.command)
    reset_setup_registries()
    await ConfigurationLoader.from_yaml_file(source.source).initialize_pyrit_async()
    restored = TargetRegistry.get_registry_singleton().instances.get(name)
    assert restored is not None
    assert restored.agent_configuration == snapshot
    assert restored.get_identifier().hash == result.target_identifier_hash


@pytest.mark.parametrize("header", ["", "%YAML 1.2\n---\n"])
async def test_yaml_emission_uses_startup_schema_even_with_explicit_source_version_async(
    source: ConfigurationFileService,
    header: str,
) -> None:
    content = (
        f"{header}memory_db_type: in_memory\nenv_files: []\ninitialization_scripts: []\ninitializers: [technique]\n"
        'extensions: {boolean: true, count: 10, text: "on"} # keep types\n'
    )
    await asyncio.to_thread(Path(source.source).write_text, content, encoding="utf-8")
    create = await request(source, name="on")
    await save_agent_startup_async(request=create, source=source)
    emitted = await source.read_async()
    assert "# keep types" in emitted
    assert safe_load_yaml(emitted)["extensions"] == safe_load_yaml(content)["extensions"]
    assert safe_load_yaml(emitted)["initializers"][-1]["args"]["name"] == "on"
    with patch.dict(os.environ, {"TEST_REPO_KEY": "test"}):
        await ConfigurationLoader.from_yaml_file(source.source).initialize_pyrit_async()
    target = TargetRegistry.get_registry_singleton().instances.get("on")
    assert target is not None
    assert target.agent_configuration == create.configuration


async def test_actual_emitted_entries_are_validated_before_storage_write_async(
    source: ConfigurationFileService,
) -> None:
    create = await request(source)
    before = await source.read_async()
    dump = YAML.dump

    def corrupt_dump(yaml: YAML, data: Any, stream: StringIO) -> None:
        corrupted = copy.deepcopy(data)
        corrupted["initializers"][-1]["args"]["lifetime_seconds"] += 1
        dump(yaml, corrupted, stream)

    with (
        patch.object(YAML, "dump", autospec=True, side_effect=corrupt_dump),
        patch.object(source, "_write_source_async", new_callable=AsyncMock) as write,
        pytest.raises(ValueError, match="change configuration values|changing target identity"),
    ):
        await save_agent_startup_async(request=create, source=source)
    write.assert_not_awaited()
    assert await source.read_async() == before
    assert TargetRegistry.get_registry_singleton().instances.get(create.name) is None


async def test_saved_byok_identity_is_stable_across_process_hash_seeds_async(tmp_path: Path) -> None:
    script = r"""
import asyncio
import json
import sys

from pyrit.backend.models.configuration import SaveAgentStartupRequest
from pyrit.backend.services.agent_startup_service import save_agent_startup_async
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile, ModelBinding
from pyrit.models.model_inference import InferenceCapabilities, InferenceRequirements, InferenceWireApi
from pyrit.prompt_target import TextTarget
from pyrit.registry import TargetRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.configuration_loader import ConfigurationLoader

class InertModelTarget(TextTarget):
    @property
    def inference_capabilities(self):
        return InferenceCapabilities(
            wire_apis=frozenset({InferenceWireApi.CHAT_COMPLETIONS}),
            streaming=True, tool_calls=True, input_modalities=frozenset({"text", "image"}),
        )

async def main_async():
    await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[], load_defaults=False, silent=True)
    model = InertModelTarget()
    TargetRegistry.get_registry_singleton().instances.register(model, name="model")
    source = ConfigurationFileService(config_file_value=sys.argv[2])
    result = {"model_hash": model.get_identifier().hash}
    if sys.argv[1] == "save":
        configuration = AgentTargetConfiguration(
            model_binding=ModelBinding(
                model="base-model", target_registry_name="model", target_identifier_hash=model.get_identifier().hash,
            ),
            harness_profile=HarnessProfile(inference_requirements=InferenceRequirements(
                input_modalities=frozenset({"text", "image"}),
            )),
            environment_template=EnvironmentTemplate(image="test"),
        )
        saved = await save_agent_startup_async(
            request=SaveAgentStartupRequest(name="saved_agent", configuration=configuration,
                                           version=(await source.read_with_version_async())[1]),
            source=source,
        )
        result["target_hash"] = saved.target_identifier_hash
    else:
        loader = ConfigurationLoader.from_yaml_file(source.source)
        # The inert source is the startup model dependency; execute the saved agent entries normally.
        for initializer in loader.resolve_initializers():
            await initializer.initialize_async()
        target = TargetRegistry.get_registry_singleton().instances.get("saved_agent")
        result["target_hash"] = target.get_identifier().hash
    entries = safe_load_yaml(await source.read_async())["initializers"]
    harness = next(entry for entry in entries if isinstance(entry, dict) and entry["name"] == "harness_profile")
    result["modalities"] = harness["args"]["inference_input_modalities"]
    print("CHECKPOINT_RESULT=" + json.dumps(result))

asyncio.run(main_async())
"""
    path = tmp_path / "cross-process.yaml"
    await asyncio.to_thread(path.write_text, "initializers: [technique]\n", encoding="utf-8")
    results = []
    for mode, seed in (("save", "1"), ("load", "2"), ("load", "3")):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            mode,
            str(path),
            env={**os.environ, "PYTHONHASHSEED": seed},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        line = next(line for line in stdout.decode("utf-8").splitlines() if line.startswith("CHECKPOINT_RESULT="))
        results.append(json.loads(line.removeprefix("CHECKPOINT_RESULT=")))
    assert results[0] == results[1] == results[2]
    assert results[0]["modalities"] == ["image", "text"]


async def test_saved_model_source_hash_survives_and_rejects_replacement_async(source: ConfigurationFileService) -> None:
    from pyrit.setup.initializers.targets import TargetInitializer

    await source.update_async(
        "memory_db_type: in_memory\nenv_files: []\ninitialization_scripts: []\ninitializers: [target]\n",
        expected_version=(await source.read_with_version_async())[1],
    )
    with patch.dict(
        os.environ,
        {
            "OPENAI_CHAT_ENDPOINT": "https://provider.example/v1",
            "OPENAI_CHAT_MODEL": "test-model",
            "OPENAI_CHAT_KEY": "private-provider-key",
        },
        clear=True,
    ):
        await TargetInitializer().initialize_async()
        model = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
        assert model is not None
        snapshot = AgentTargetConfiguration(
            name="bound",
            model_binding=ModelBinding(
                target_registry_name="openai_chat",
                target_identifier_hash=model.get_identifier().hash,
            ),
            environment_template=EnvironmentTemplate(image="test"),
        )
        result = await save_agent_startup_async(
            request=SaveAgentStartupRequest(
                name="bound", configuration=snapshot, version=(await source.read_with_version_async())[1]
            ),
            source=source,
        )
        content = await source.read_async()
        assert "private-provider-key" not in content
        assert model.get_identifier().hash in content
        reset_setup_registries()
        await ConfigurationLoader.from_yaml_file(source.source).initialize_pyrit_async()
        assert (
            TargetRegistry.get_registry_singleton().instances.get("bound").get_identifier().hash
            == result.target_identifier_hash
        )
        reset_setup_registries()
        with (
            patch.dict(os.environ, {"OPENAI_CHAT_MODEL": "replacement"}),
            pytest.raises(ValueError, match="configuration changed"),
        ):
            await ConfigurationLoader.from_yaml_file(source.source).initialize_pyrit_async()


async def test_retry_is_idempotent_and_conflicting_definition_does_not_write_async(
    source: ConfigurationFileService,
) -> None:
    create = await request(source)
    first = await save_agent_startup_async(request=create, source=source)
    before = await source.read_async()
    repeated = await save_agent_startup_async(
        request=create.model_copy(update={"version": first.version}), source=source
    )
    assert repeated.version == first.version
    assert await source.read_async() == before
    changed = create.configuration.model_copy(update={"lifetime_seconds": 700})
    with pytest.raises(ValueError, match="already exists"):
        await save_agent_startup_async(
            request=create.model_copy(update={"configuration": changed, "version": first.version}),
            source=source,
        )
    assert await source.read_async() == before


async def test_stale_and_concurrent_saves_do_not_overwrite_other_edits_async(source: ConfigurationFileService) -> None:
    create = await request(source)
    results = await asyncio.gather(
        save_agent_startup_async(request=create, source=source),
        save_agent_startup_async(request=create.model_copy(update={"name": "second"}), source=source),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConfigurationFileConflictError) for result in results) == 1
    names = [
        entry["args"]["name"]
        for entry in safe_load_yaml(await source.read_async())["initializers"]
        if isinstance(entry, dict) and entry["name"] == "agent_target"
    ]
    assert len(names) == 1


@pytest.mark.parametrize(
    "content",
    [
        "initializers: [\n",
        "operator: unchanged\n",
        "initializers: []\n",
        "shared: &list [target]\ninitializers: *list\n",
        "base: &base {initializers: [target]}\n<<: *base\n",
        "initializers: [target]\ninitializers: [technique]\n",
    ],
)
async def test_implicit_anchored_or_ambiguous_initializer_lists_are_not_rewritten_async(
    source: ConfigurationFileService,
    content: str,
) -> None:
    Path(source.source).write_text(content, encoding="utf-8")
    create = await request(source)
    with pytest.raises(ValueError):
        await save_agent_startup_async(request=create, source=source)
    assert await source.read_async() == content


async def test_storage_failure_leaves_runtime_unchanged_async(source: ConfigurationFileService) -> None:
    create = await request(source)
    before = await source.read_async()
    with patch.object(source, "_write_source_async", AsyncMock(side_effect=OSError("disk unavailable"))):
        with pytest.raises(OSError):
            await save_agent_startup_async(request=create, source=source)
    assert await source.read_async() == before
    assert TargetRegistry.get_registry_singleton().instances.get(create.name) is None


def test_startup_api_is_admin_only_and_saves_without_returning_raw_config(source: ConfigurationFileService) -> None:
    app = FastAPI()
    app.state.configuration_file_service = source
    app.include_router(router)
    with TestClient(app) as client, patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "false"}):
        assert client.get("/config/agent-startup").status_code == 403
        assert client.post("/config/agent-startup", json={}).status_code == 403
        app.dependency_overrides[require_admin] = lambda: None
        destination = client.get("/config/agent-startup")
        assert set(destination.json()) == {"source", "version"}
        response = client.post(
            "/config/agent-startup",
            json={
                "name": "saved_agent",
                "configuration": configuration().model_dump(mode="json"),
                "version": destination.json()["version"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["applied"] is False
        assert "content" not in response.json()
        assert (
            client.post(
                "/config/agent-startup",
                json={
                    "name": "saved_agent",
                    "configuration": configuration().model_dump(mode="json"),
                    "version": destination.json()["version"],
                },
            ).status_code
            == 409
        )
    assert TargetRegistry.get_registry_singleton().instances.get("saved_agent") is None


async def test_save_requires_a_model_pin_and_keeps_existing_targets_async(source: ConfigurationFileService) -> None:
    create = await request(source)
    unpinned = create.configuration.model_copy(update={"model_binding": ModelBinding(target_registry_name="source")})
    with pytest.raises(ValueError, match="pinned"):
        await save_agent_startup_async(request=create.model_copy(update={"configuration": unpinned}), source=source)
    existing = AgentTarget(agent_configuration=create.configuration)
    TargetRegistry.get_registry_singleton().instances.register(existing, name=create.name)
    await save_agent_startup_async(request=create, source=source)
    assert TargetRegistry.get_registry_singleton().instances.get(create.name) is existing


async def test_startup_rejects_drifted_expected_target_hash_async(
    source: ConfigurationFileService,
) -> None:
    create = await request(source)
    await save_agent_startup_async(request=create, source=source)
    data = safe_load_yaml(await source.read_async())
    entry = next(item for item in data["initializers"] if item["name"] == "agent_target")
    entry["args"]["lifetime_seconds"] = 999
    loader = ConfigurationLoader.from_dict(data)
    with patch.dict(os.environ, {"TEST_REPO_KEY": "test"}), pytest.raises(ValueError, match="pinned"):
        await loader.initialize_pyrit_async()


async def test_pinned_saved_definition_requires_restart_before_live_mutation_async(
    source: ConfigurationFileService,
) -> None:
    create = await request(source)
    await save_agent_startup_async(request=create, source=source)
    loader = ConfigurationLoader.from_yaml_file(source.source)
    current = TargetRegistry.get_registry_singleton()
    with pytest.raises(ValueError, match="requires a restart"):
        await loader.preflight_reinitialization_async(environment_values={"TEST_REPO_KEY": "test"})
    assert TargetRegistry.get_registry_singleton() is current
    assert current.instances.get(create.name) is None


async def test_save_uses_existing_blob_configuration_transport_without_new_storage() -> None:
    from pyrit.backend.services import configuration_file_service

    source = ConfigurationFileService(config_file_value="https://account.blob.core.windows.net/config/startup.yaml")
    stored = {"content": b"initializers: [technique]\n# retained\n"}

    async def read_async(blob_uri: str) -> bytes:
        assert blob_uri == source.source
        return stored["content"]

    async def write_async(*, blob_uri: str, content: bytes) -> None:
        assert blob_uri == source.source
        stored["content"] = content

    with (
        patch.object(configuration_file_service, "_download_blob_config_async", side_effect=read_async),
        patch.object(configuration_file_service, "_upload_blob_config_async", side_effect=write_async) as upload,
    ):
        result = await save_agent_startup_async(request=await request(source), source=source)
    upload.assert_awaited_once()
    assert b"# retained" in stored["content"]
    assert result.source == source.source and result.applied is False
    assert TargetRegistry.get_registry_singleton().instances.get("saved_agent") is None
