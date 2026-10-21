# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("psutil")

from pyrit.agent.environment import (
    EnvironmentProviderRegistry,
    ExecutionSpec,
    create_execution_session,
    validate_environment_template,
)
from pyrit.agent.environments.local import LocalSession
from pyrit.models.agent_execution import AgentProfile, EnvironmentTemplate, HarnessProfile
from tests.unit.agent.session_helpers import execution, session_directory, spec_for


async def test_recreated_profile_rejects_changed_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "orders.json").write_text('{"total": 31}')
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        fixture_directory=str(fixture),
        command=("python", "-c", "import sys; sys.stdin.read()"),
    )
    first = execution(profile)
    session = LocalSession(execution=first, directory=session_directory(tmp_path, "first"))
    await session.launch_async(spec_for(profile))
    await session.close_async()
    (fixture / "orders.json").write_text('{"total": 36}')
    second = execution(
        profile.model_copy(update={"expected_fixture_sha256": first.fixture_sha256}), conversation_id="two"
    )
    recreated = LocalSession(execution=second, directory=session_directory(tmp_path, "second"))
    with pytest.raises(ValueError, match="fixture changed"):
        await recreated.launch_async(spec_for(profile))
    await recreated.close_async()
    assert not (recreated.workspace).exists()


async def test_local_launch_uses_private_home_and_exposes_streams(tmp_path: Path) -> None:
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        command=(
            "python",
            "-c",
            "import os,sys; print(os.environ['HOME'], os.environ['TOKEN']); sys.stderr.write('warned')",
        ),
    )
    record = execution(profile)
    session = LocalSession(execution=record, directory=session_directory(tmp_path))
    with pytest.raises(RuntimeError, match="not been launched"):
        _ = session.acp_reader
    await session.launch_async(ExecutionSpec(command=profile.command, environment={}, credentials={"TOKEN": "secret"}))
    line = await asyncio.wait_for(session.acp_reader.readline(), timeout=10)
    assert line.decode().split() == [str(session.home), "secret"]
    assert (await asyncio.wait_for(session.stderr.read(), timeout=10)) == b"warned"
    assert record.process_id is not None and record.process_created_at is not None
    await session.close_async()


def test_registry_lists_first_party_providers_and_rejects_unknown_names() -> None:
    registry = EnvironmentProviderRegistry()
    names = [info.name for info in registry.list_providers()]
    assert names == ["local"]
    local = registry.get("local").info
    assert not local.capabilities.isolated
    with pytest.raises(ValueError, match="unavailable"):
        registry.get("aca_sandbox")
    with pytest.raises(TypeError, match="ExecutionEnvironmentProvider"):
        registry.register(object())  # type: ignore[arg-type]


def test_template_validation_follows_provider_capabilities() -> None:
    validate_environment_template(EnvironmentTemplate(environment="local"), require_consent=False)
    with pytest.raises(ValueError, match="NOT sandboxed"):
        validate_environment_template(EnvironmentTemplate(environment="local"))
    with pytest.raises(ValueError, match="unavailable"):
        validate_environment_template(EnvironmentTemplate(environment="aca_sandbox", image="x"))


def test_first_party_providers_reject_settings_they_would_ignore() -> None:
    template = EnvironmentTemplate(
        environment="local", local_execution_acknowledged=True, environment_settings={"x": 1}
    )
    with pytest.raises(ValueError, match="environment_settings"):
        validate_environment_template(template)


async def test_local_close_is_effect_idempotent(tmp_path: Path) -> None:
    profile = AgentProfile(
        environment="local",
        local_execution_acknowledged=True,
        artifact_paths=("missing.txt",),
        command=("python", "-c", "import sys; sys.stdin.read()"),
    )
    record = execution(profile)
    session = LocalSession(execution=record, directory=session_directory(tmp_path))
    await session.launch_async(spec_for(profile))
    await session.close_async()
    assert len(record.artifact_errors) == 1
    snapshot = record.model_dump(mode="json")
    await session.close_async()
    assert record.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("model", [AgentProfile, HarnessProfile])
def test_retired_profile_fields_still_decode(model: type[AgentProfile] | type[HarnessProfile]) -> None:
    extra = {"image": "test"} if model is AgentProfile else {}
    decoded = model.model_validate({**extra, "use_github_cli_login": True})
    assert not hasattr(decoded, "use_github_cli_login")
    assert decoded.harness_kind == "copilot"


def test_sessions_are_created_through_the_record_provider(tmp_path: Path) -> None:
    local = create_execution_session(
        execution=execution(AgentProfile(environment="local", local_execution_acknowledged=True)), directory=tmp_path
    )
    assert isinstance(local, LocalSession) and local.harness_home == str(tmp_path / "home")
    with pytest.raises(ValueError, match="unavailable"):
        create_execution_session(execution=execution(AgentProfile(image="test")), directory=tmp_path)
