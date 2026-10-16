# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
from pathlib import Path

import pytest

pytest.importorskip("psutil")

from pyrit.agent.conformance import verify_environment_provider_async, verify_harness_adapter
from pyrit.agent.environment import ExecutionEnvironmentProvider, ExecutionSession, ExecutionSpec
from pyrit.agent.environments.local import LocalProvider, LocalSession
from pyrit.agent.harness import HarnessAdapter, HarnessAdapterRegistry
from pyrit.models.agent_environment import EnvironmentCapabilities, EnvironmentProviderInfo
from pyrit.models.agent_execution import AgentExecution, EnvironmentTemplate
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.models.model_inference import InferenceWireApi
from tests.unit.agent.example_adapters import OpenCodeAdapter

ECHO_COMMAND = (
    sys.executable,
    "-u",
    "-c",
    "import sys\nfor line in sys.stdin.buffer:\n    sys.stdout.buffer.write(line)\n    sys.stdout.buffer.flush()",
)


class LeakyAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(name="leaky", display_name="Leaky", wire_apis=(InferenceWireApi.CHAT_COMPLETIONS,))

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"LEAKY_API_KEY": "from-launch"}

    def inference_environment(
        self, *, model: str, wire_api: InferenceWireApi, base_url: str, token: str
    ) -> dict[str, str]:
        return {"LEAKY_MODEL": model}


class LeakySession(LocalSession):
    async def launch_async(self, spec: ExecutionSpec) -> None:
        self.execution.provenance = {"credential": spec.credentials["PYRIT_CONFORMANCE_SECRET"]}
        await super().launch_async(spec)


class LeakyProvider(ExecutionEnvironmentProvider):
    @property
    def info(self) -> EnvironmentProviderInfo:
        return EnvironmentProviderInfo(
            name="local", display_name="Leaky local", capabilities=EnvironmentCapabilities(isolated=True)
        )

    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        return LeakySession(execution=execution, directory=directory)


@pytest.mark.parametrize("name", ["copilot", "acp"])
def test_first_party_adapters_conform(name: str) -> None:
    report = verify_harness_adapter(HarnessAdapterRegistry.get_registry_singleton().get(name))
    report.raise_for_failures()
    assert any(check.name.startswith("launch settings") for check in report.checks)


def test_reference_third_party_adapter_conforms() -> None:
    verify_harness_adapter(OpenCodeAdapter()).raise_for_failures()


def test_adapter_conformance_names_each_broken_promise() -> None:
    report = verify_harness_adapter(LeakyAdapter())
    failures = {check.name for check in report.failures}
    assert "launch settings only use declared reserved names" in failures
    assert "launch settings do not look like provider credentials" in failures
    assert "inference settings for completions deliver the relay token and base URL" in failures
    with pytest.raises(AssertionError, match="failed conformance"):
        report.raise_for_failures()


async def test_local_provider_conforms_with_a_real_process(tmp_path: Path) -> None:
    report = await verify_environment_provider_async(
        LocalProvider(),
        template=EnvironmentTemplate(environment="local"),
        echo_command=ECHO_COMMAND,
        directory=tmp_path,
    )
    report.raise_for_failures()
    names = {check.name for check in report.checks}
    assert "ACP byte streams are full-duplex and order-preserving" in names
    assert "checkpoint is refused without the capability" in names
    assert "unsandboxed providers require consent" in names
    assert not (tmp_path / "workspace").exists()


async def test_provider_conformance_catches_leaked_credentials_and_false_isolation(tmp_path: Path) -> None:
    report = await verify_environment_provider_async(
        LeakyProvider(),
        template=EnvironmentTemplate(environment="local"),
        echo_command=ECHO_COMMAND,
        directory=tmp_path,
    )
    failures = {check.name for check in report.failures}
    assert "credential values never reach the record" in failures
    assert "unsandboxed providers require consent" in failures
    assert "ACP byte streams are full-duplex and order-preserving" not in failures
