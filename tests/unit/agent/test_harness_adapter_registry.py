# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from pyrit.agent.harness import (
    HarnessAdapter,
    HarnessAdapterRegistry,
    launch_environment,
    validate_harness_authentication,
)
from pyrit.models.agent_execution import AgentProfile, HarnessProfile
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.setup.initialization import reset_setup_registries


class TeamAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="team_v1",
            display_name="Team agent",
            default_command=("team-agent", "--stdio"),
            reserved_environment=("TEAM_HOME",),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"TEAM_HOME": home}


@pytest.fixture
def adapter_registry() -> Iterator[HarnessAdapterRegistry]:
    registry = HarnessAdapterRegistry()
    with patch.object(HarnessAdapterRegistry, "_singleton", registry):
        yield registry


def test_custom_adapter_dispatch(adapter_registry: HarnessAdapterRegistry) -> None:
    adapter = TeamAdapter()
    adapter_registry.register(adapter)
    adapter_registry.register(adapter)
    assert adapter_registry.get("team_v1") is adapter
    assert next(info for info in adapter_registry.list_adapters() if info.name == "team_v1") == adapter.info
    assert launch_environment(kind="team_v1", home="private") == {"TEAM_HOME": "private"}
    with pytest.raises(ValueError, match="already registered"):
        adapter_registry.register(TeamAdapter())
    with pytest.raises(ValueError, match="cannot override execution configuration"):
        validate_harness_authentication(
            harness=HarnessProfile(harness_kind="team_v1", command=("team-agent",), credential_env=("TEAM_HOME",))
        )


def test_unknown_adapter_data_roundtrips_without_import_or_fallback(adapter_registry: HarnessAdapterRegistry) -> None:
    profile = AgentProfile(
        environment="local", local_execution_acknowledged=True, harness_kind="team_v1", command=("team-agent",)
    )
    assert AgentProfile.model_validate_json(profile.model_dump_json()) == profile
    with pytest.raises(ValueError, match="unavailable"):
        launch_environment(kind=profile.harness_kind, home="private")
    with pytest.raises(ValueError, match="unavailable"):
        validate_harness_authentication(harness=HarnessProfile(harness_kind="team_v1", command=("agent",)))


@pytest.mark.usefixtures("patch_central_database")
def test_setup_reset_discards_custom_adapters(adapter_registry: HarnessAdapterRegistry) -> None:
    adapter_registry.register(TeamAdapter())
    reset_setup_registries()
    current = HarnessAdapterRegistry.get_registry_singleton()
    assert current.get("copilot")
    with pytest.raises(ValueError, match="unavailable"):
        current.get("team_v1")
