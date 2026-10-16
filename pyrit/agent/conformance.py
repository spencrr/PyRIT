# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Executable contracts for the two agent extension seams.

Extension authors run these against their harness adapter or environment provider; PyRIT runs
them against the first-party implementations. A report lists every check, so a failure names
the broken promise instead of surfacing later as a confusing runtime error.
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pyrit.agent.environment import (
    ExecutionEnvironmentProvider,
    ExecutionSession,
    ExecutionSpec,
    validate_environment_template,
)
from pyrit.agent.harness import HarnessAdapter
from pyrit.models.agent_checkpoint import HarnessCheckpointLayout
from pyrit.models.agent_execution import AgentExecution, AgentProfile, AgentTargetConfiguration, EnvironmentTemplate


class ConformanceCheck(BaseModel):
    """One contract promise and whether the implementation kept it."""

    model_config = ConfigDict(frozen=True)
    name: str
    passed: bool
    detail: str = ""


class ConformanceReport(BaseModel):
    """All checks for one implementation; read ``passed`` or call ``raise_for_failures``."""

    subject: str
    checks: list[ConformanceCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether every check passed."""
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[ConformanceCheck]:
        """The checks that failed, in order."""
        return [check for check in self.checks if not check.passed]

    def raise_for_failures(self) -> None:
        """
        Fail loudly with every broken promise.

        Raises:
            AssertionError: At least one check failed.
        """
        if not self.passed:
            lines = [f"{check.name}: {check.detail}" for check in self.failures]
            raise AssertionError(f"{self.subject} failed conformance:\n" + "\n".join(lines))


class _Recorder:
    def __init__(self, subject: str) -> None:
        self.report = ConformanceReport(subject=subject)

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.report.checks.append(ConformanceCheck(name=name, passed=condition, detail="" if condition else detail))
        return condition

    async def attempt_async(self, name: str, action: Callable[[], Awaitable[object]]) -> bool:
        try:
            await action()
        except Exception as error:  # noqa: BLE001 - every failure must become a named check
            return self.check(name, False, f"{type(error).__name__}: {error}")
        return self.check(name, True)


def verify_harness_adapter(adapter: HarnessAdapter) -> ConformanceReport:
    """
    Verify an adapter's declarations against what it emits, without running anything.

    Returns:
        ConformanceReport: One check per promise.
    """
    recorder = _Recorder(f"harness adapter '{type(adapter).__name__}'")
    info = adapter.info
    reserved = set(info.reserved_environment) | adapter.EXECUTION_RESERVED_ENVIRONMENT
    recorder.check("info.name is a registry name", bool(info.name), "info.name must be set")
    recorder.check(
        "declared reserved names are uppercase identifiers",
        all(name.isidentifier() and name == name.upper() for name in info.reserved_environment),
        "reserved_environment must contain UPPER_CASE environment variable names",
    )
    home = "/conformance/home"
    launch = adapter.launch_environment(home=home)
    recorder.check(
        "launch settings only use declared reserved names",
        set(launch) <= reserved,
        f"undeclared launch variables: {sorted(set(launch) - reserved)}",
    )
    recorder.check(
        "launch settings do not look like provider credentials",
        not any("KEY" in name or "TOKEN" in name or "SECRET" in name for name in launch),
        "launch_environment must not emit credential-like variables",
    )
    token, base_url = secrets.token_hex(16), "http://relay.conformance.invalid/v1"
    if info.wire_apis:
        for wire_api in info.wire_apis:
            emitted = adapter.inference_environment(model="m", wire_api=wire_api, base_url=base_url, token=token)
            recorder.check(
                f"inference settings for {wire_api.value} only use declared reserved names",
                set(emitted) <= reserved,
                f"undeclared inference variables: {sorted(set(emitted) - reserved)}",
            )
            recorder.check(
                f"inference settings for {wire_api.value} deliver the relay token and base URL",
                any(token in value for value in emitted.values())
                and any(base_url.rstrip("/") in value for value in emitted.values()),
                "the harness cannot reach the scoped relay without both values",
            )
    # Wire APIs outside info.wire_apis are refused by the runtime before an adapter is consulted.
    layout = adapter.checkpoint_layout
    if layout is not None:
        recorder.check(
            "checkpoint layout pins agent versions",
            bool(layout.agent_versions) and bool(layout.version),
            "a checkpoint layout must name its tested harness versions",
        )
    return recorder.report


async def verify_environment_provider_async(
    provider: ExecutionEnvironmentProvider,
    *,
    template: EnvironmentTemplate,
    echo_command: tuple[str, ...],
    directory: Path,
    timeout_seconds: float = 60,
) -> ConformanceReport:
    """
    Drive one real allocation through the provider contract with a byte-echo command.

    ``echo_command`` must copy stdin to stdout inside the environment (for example ``("cat",)``
    in a POSIX image). Nothing here speaks ACP; the checks cover resources, streams, secrets,
    idempotent release, and capability honesty.

    Returns:
        ConformanceReport: One check per promise.
    """
    recorder = _Recorder(f"environment provider '{provider.info.name}'")
    info = provider.info
    recorder.check("template names this provider", template.environment == info.name, "template/provider mismatch")
    consent_required = not info.capabilities.isolated
    try:
        validate_environment_template(template.model_copy(update={"local_execution_acknowledged": False}))
        recorder.check("unsandboxed providers require consent", not consent_required, "consent was not enforced")
    except ValueError:
        recorder.check("unsandboxed providers require consent", consent_required, "isolated provider demanded consent")
    profile = conformance_profile(template, command=echo_command)
    record = AgentExecution(
        owner_id="conformance", conversation_id="conformance", target_id="conformance", profile=profile
    )
    session = provider.create_session(execution=record, directory=directory)
    recorder.check("create_session returns an ExecutionSession", isinstance(session, ExecutionSession))
    for name in ("acp_reader", "acp_writer", "stderr"):
        try:
            getattr(session, name)
        except RuntimeError:
            recorder.check(f"{name} is unavailable before launch", True)
        else:
            recorder.check(
                f"{name} is unavailable before launch", False, "streams must raise RuntimeError before launch"
            )
    session.reserve()
    await _verify_relay_translation_async(recorder, session)
    secret = secrets.token_hex(24)
    spec = ExecutionSpec(
        command=echo_command,
        environment={"PYRIT_CONFORMANCE": "1"},
        credentials={"PYRIT_CONFORMANCE_SECRET": secret},
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            launched = await recorder.attempt_async(
                "launch_async starts the harness", lambda: session.launch_async(spec)
            )
            if launched:
                await _verify_streams_async(recorder, session)
        recorder.check(
            "credential values never reach the record",
            secret not in record.model_dump_json(),
            "spec.credentials leaked into the persisted execution record",
        )
        recorder.check("provenance is a string mapping", all(isinstance(v, str) for v in record.provenance.values()))
    finally:
        async with asyncio.timeout(timeout_seconds):
            await recorder.attempt_async("close_async releases resources", session.close_async)
            await recorder.attempt_async("close_async is idempotent", session.close_async)
    if not info.capabilities.checkpoint:
        try:
            await session.capture_checkpoint_async(layout=_conformance_layout(), secrets=())
        except NotImplementedError:
            recorder.check("checkpoint is refused without the capability", True)
        except Exception as error:  # noqa: BLE001 - any other error means the refusal is not explicit
            recorder.check("checkpoint is refused without the capability", False, f"{type(error).__name__}: {error}")
        else:
            recorder.check(
                "checkpoint is refused without the capability", False, "capture returned without the capability"
            )
    return recorder.report


async def _verify_relay_translation_async(recorder: _Recorder, session: ExecutionSession) -> None:
    # Providers legitimately differ: a container needs a routable host, a remote sandbox forwards loopback.
    accepted: list[str] = []
    for relay_url in ("http://127.0.0.1:8021/v1", "http://relay.conformance.invalid:8021/v1"):
        try:
            endpoint = await session.inference_endpoint_async(relay_url=relay_url)
        except ValueError:
            continue
        if isinstance(endpoint, str) and endpoint.startswith("http"):
            accepted.append(relay_url)
        else:
            recorder.check("inference_endpoint_async returns a URL", False, f"got {endpoint!r} for {relay_url}")
            return
    recorder.check(
        "inference_endpoint_async translates at least one relay address and rejects others with ValueError",
        bool(accepted),
        "neither a loopback nor a routable relay URL was accepted",
    )


async def _verify_streams_async(recorder: _Recorder, session: ExecutionSession) -> None:
    payload = b"".join(secrets.token_hex(64).encode() + b"\n" for _ in range(64))
    session.acp_writer.write(payload)
    await session.acp_writer.drain()
    received = b""
    while len(received) < len(payload):
        line = await session.acp_reader.readline()
        if not line:
            break
        received += line
    recorder.check(
        "ACP byte streams are full-duplex and order-preserving",
        received == payload,
        f"echoed {len(received)} of {len(payload)} bytes intact" if received != payload else "",
    )
    session.acp_writer.close()
    trailing = await session.acp_reader.read()
    recorder.check(
        "closing ACP input ends the output stream", trailing == b"", "harness kept writing after input closed"
    )
    drained = await session.stderr.read()
    recorder.check("stderr is readable to end of stream", isinstance(drained, bytes))


def _conformance_layout() -> HarnessCheckpointLayout:
    return HarnessCheckpointLayout(version="conformance", agent_versions=("0",), state_root="/home/agent/.conformance")


def conformance_profile(template: EnvironmentTemplate, *, command: tuple[str, ...]) -> AgentProfile:
    """
    Build the profile the verifier allocates, for callers that want to inspect or reuse it.

    Returns:
        AgentProfile: A consented profile running ``command`` in ``template``.
    """
    configuration = AgentTargetConfiguration(
        environment_template=template.model_copy(update={"local_execution_acknowledged": True})
    )
    return configuration.to_profile().model_copy(update={"command": command})
