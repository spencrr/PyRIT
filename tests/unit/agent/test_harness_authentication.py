# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.agent.harness import resolve_credential_values, validate_harness_authentication
from pyrit.models.agent_execution import HarnessProfile
from tests.unit.agent.example_adapters import OPENCODE_COMMAND


@pytest.mark.parametrize("name", ["COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "github_token"])
def test_copilot_credentials_are_plain_references(name: str) -> None:
    validate_harness_authentication(harness=HarnessProfile(credential_env=(name,)))


@pytest.mark.parametrize("name", ["HOME", "PATH", "COPILOT_HOME", "copilot_home"])
def test_credential_refs_cannot_override_execution_settings(name: str) -> None:
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(harness=HarnessProfile(credential_env=(name,)))


@pytest.mark.usefixtures("opencode_adapter")
def test_reserved_names_follow_the_selected_adapter() -> None:
    validate_harness_authentication(harness=HarnessProfile(credential_env=("OPENCODE_CONFIG_CONTENT",)))
    with pytest.raises(ValueError, match="override execution"):
        validate_harness_authentication(
            harness=HarnessProfile(
                harness_kind="opencode", command=OPENCODE_COMMAND, credential_env=("OPENCODE_CONFIG_CONTENT",)
            )
        )


def test_authentication_method_is_kept() -> None:
    harness = HarnessProfile(authentication_method="copilot-login")
    validate_harness_authentication(harness=harness)
    assert harness.authentication_method == "copilot-login"


def test_missing_credential_reference_fails_before_provisioning() -> None:
    with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="COPILOT_GITHUB_TOKEN"):
        resolve_credential_values(credential_env=("COPILOT_GITHUB_TOKEN",))


def test_credential_values_are_resolved_from_the_process_environment() -> None:
    with patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "gho_synthetic"}):
        credentials = resolve_credential_values(credential_env=("COPILOT_GITHUB_TOKEN",))
    assert credentials == {"COPILOT_GITHUB_TOKEN": "gho_synthetic"}
