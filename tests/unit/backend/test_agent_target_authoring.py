# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.services.target_service import TargetService
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile
from pyrit.prompt_target import AgentTarget
from pyrit.registry import Registry, TargetRegistry


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    with patch.dict(Registry._singletons, clear=True):
        yield


def _configuration(**template: object) -> dict[str, object]:
    return AgentTargetConfiguration(
        harness_profile=HarnessProfile(harness_kind="acp", command=("team-agent", "--stdio")),
        environment_template=EnvironmentTemplate(environment="local", local_execution_acknowledged=True, **template),
    ).model_dump(mode="json")


@pytest.mark.usefixtures("patch_central_database")
async def test_executable_target_alias_requires_admin_before_construction() -> None:
    registry = TargetRegistry.get_registry_singleton()
    registry.register_class(AgentTarget, name="CustomHarnessAlias")
    with pytest.raises(PermissionError, match="administrator access"):
        await TargetService().create_target_async(
            request=CreateTargetRequest(type="CustomHarnessAlias", params={"agent_configuration": _configuration()}),
            is_admin=False,
        )
    assert not registry.instances.get_all_instances()


@pytest.mark.usefixtures("patch_central_database")
async def test_admin_creates_agent_target_from_json_configuration(tmp_path: Path) -> None:
    created = await TargetService().create_target_async(
        request=CreateTargetRequest(name="agent", type="AgentTarget", params={"agent_configuration": _configuration()}),
        upload_directory=str(tmp_path),
        is_admin=True,
    )
    target = TargetRegistry.get_registry_singleton().instances.get("agent")
    assert isinstance(target, AgentTarget)
    assert created.agent_configuration == target.agent_configuration
    assert target.agent_configuration.environment_template.environment == "local"


@pytest.mark.usefixtures("patch_central_database")
async def test_api_created_fixture_directories_stay_inside_the_upload_directory(tmp_path: Path) -> None:
    (tmp_path / "receipt").mkdir()
    service = TargetService()
    await service.create_target_async(
        request=CreateTargetRequest(
            name="inside",
            type="AgentTarget",
            params={"agent_configuration": _configuration(fixture_directory="receipt")},
        ),
        upload_directory=str(tmp_path),
        is_admin=True,
    )
    inside = TargetRegistry.get_registry_singleton().instances.get("inside")
    assert isinstance(inside, AgentTarget)
    assert inside.profile.fixture_directory == str((tmp_path / "receipt").resolve())
    with pytest.raises(ValueError, match="outside the allowed directory"):
        await service.create_target_async(
            request=CreateTargetRequest(
                name="escape",
                type="AgentTarget",
                params={"agent_configuration": _configuration(fixture_directory="..")},
            ),
            upload_directory=str(tmp_path),
            is_admin=True,
        )
    with pytest.raises(ValueError, match="target_upload_directory"):
        await service.create_target_async(
            request=CreateTargetRequest(
                name="unrooted", type="AgentTarget", params={"agent_configuration": _configuration()}
            ),
            is_admin=True,
        )
    assert TargetRegistry.get_registry_singleton().instances.get("escape") is None


@pytest.mark.usefixtures("patch_central_database")
async def test_invalid_json_configuration_is_rejected_before_construction(tmp_path: Path) -> None:
    configuration = {**_configuration(), "unexpected": True}
    with pytest.raises(ValueError, match="agent_configuration"):
        await TargetService().create_target_async(
            request=CreateTargetRequest(
                name="agent", type="AgentTarget", params={"agent_configuration": configuration}
            ),
            upload_directory=str(tmp_path),
            is_admin=True,
        )
    assert TargetRegistry.get_registry_singleton().instances.get("agent") is None
