# Testing an ACP agent

`AgentTarget` runs a real tool-capable harness as a PyRIT target instead of replacing its
model or its tool loop. PyRIT talks to the harness over the Agent Client Protocol (ACP) on
stdio, owns the place the harness runs, records everything the protocol exposes, and releases
the resources when the conversation ends. Install the optional dependencies with
`uv sync --extra agents`.

```text
Model binding + Harness profile + Environment template
                         |
                  Configured agent target          (a recipe; no resources)
                         | first send: provision and connect
                         v
Attack / chat -> Owned execution -> ACP -> Tested harness
                         |
                  Append-only journal (every ACP frame, stderr, lifecycle)
```

## Terminology

- **Model binding** says which model answers. In this release it is a harness-managed
  selection by name; a later release binds registered PyRIT targets.
- **Harness profile** describes the executable, its credential references, and its tool
  permission policy. It is independent of the model and of where the harness runs.
- **Environment template** describes where the harness runs: the starting filesystem and the
  execution policy of an installed *environment provider*. It is never a live resource.
- **Configured agent target** is the resolved system under test. Attacks send to it through
  the ordinary target interface.
- **Execution** is the active, stateful instance: one native agent session in one owned
  environment, created on the first message of a conversation and reused for later turns.

Target identity is the configuration without defaulted fields, timeouts, and evidence budgets,
so tightening a deadline does not create a new target, while changing what runs or where does.
An active conversation keeps the execution it started with. Completing an attack releases its
conversations; otherwise an execution ends at explicit closure, idle expiry, its maximum
lifetime, a turn deadline, or runtime shutdown.

## Use from Python

Initialize PyRIT memory normally, then scope the runtime that owns executions:

```python
from pathlib import Path

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget

configuration = AgentTargetConfiguration(
    name="copilot-local",
    harness_profile=HarnessProfile(
        credential_env=("COPILOT_GITHUB_TOKEN",),
        authentication_method="copilot-login",
        permission_policy="allow_once",
    ),
    environment_template=EnvironmentTemplate(
        environment="local",
        local_execution_acknowledged=True,  # local execution is NOT sandboxed
        fixture_directory="assets/agent_receipt",
    ),
    artifact_paths=("receipt.json",),
)

async with AgentExecutionManager(root=Path("dbdata") / "agent-executions") as manager:
    target = AgentTarget(agent_configuration=configuration)
    target.bind_execution_manager(manager)
    response = await PromptNormalizer().send_prompt_async(
        target=target,
        conversation_id="receipt-example",
        message=MessagePiece(
            role="user",
            original_value="Read orders.json and write receipt.json with a numeric total.",
        ).to_message(),
    )
```

The target can be any attack's `objective_target`, including `PromptSendingAttack` and
`AtomicAttack` runs with concurrency: each conversation gets its own execution. The target
accepts user text and supports multi-turn, but not system prompts or editable/replayed
history; a new conversation never inherits another execution's state, and PyRIT never
resends a side-effecting turn after a disconnection.

`command` is an executable/argument tuple, not a shell command. Client-side filesystem,
terminal, and elicitation capabilities are not advertised to the harness; requests to run
client-side tools are rejected, never executed on the PyRIT host. A harness's own MCP
configuration is part of the tested system.

A turn ends with a `TargetResponse` whose `TargetOutcome` is `completed`, `cancelled`,
`failed`, or `unknown`; see [explicit outcomes](./0_prompt_targets.md#explicit-outcomes).
A completed turn without assistant text, such as an agent that finished by writing files,
is recorded as such rather than fabricated into a reply.

Without a running manager of your own, `AgentTarget` uses the runtime-owned manager
(`pyrit.agent.runtime`), rooted in the memory results directory or, when results live in
remote storage, the local database data directory. The Co-PyRIT backend reconciles it at
startup, waits for its executions before shutting down, and closes it with the other services.
One manager exclusively owns its directory through a file lock; records left by an interrupted
runtime are reconciled and released at the next start, or marked `cleanup_failed` with the
reason when their provider is no longer installed.

A manager admits at most `max_executions` concurrent executions (`PYRIT_AGENT_MAX_EXECUTIONS`
for the runtime-owned manager, default 4). A new conversation waits up to
`startup_timeout_seconds` for a slot before failing explicitly, so attacks with higher
concurrency queue rather than error.

## What is recorded

Every ACP frame in both directions, the harness's stderr, and lifecycle transitions are
appended to the execution's journal, in order, with referenced credential values redacted.
The journal and the per-turn cursors into it are the evidence; `max_evidence_bytes`
bounds it, and capture failure is recorded on the execution rather than hidden.
`artifact_paths` names workspace files to copy out when the execution closes (bounded by
`max_artifact_bytes`). The runtime does not interpret tool calls in this release.

Timeouts are core policy and apply to every provider and harness: `startup_timeout_seconds`,
`turn_timeout_seconds`, `idle_timeout_seconds`, `lifetime_seconds`, and
`cancellation_grace_seconds`. A turn that exceeds its deadline is cancelled with the harness and
returned as a `cancelled` outcome carrying `agent_turn_error`, with any partial text, and its
execution is closed; only a cancellation the harness does not confirm within the grace period
raises.

## Permission policy

ACP lets a harness ask before running a tool. `permission_policy` answers deterministically:
`deny` refuses (the default), `allow_once` accepts the single-use option the harness offers.
A harness that offers neither option fails the request explicitly. Policy answers are
journaled like every other frame. This is a protocol-level answer, not a tool sandbox: what
a permitted tool can reach is decided by the environment.

## Harness adapters

`HarnessAdapter` and `HarnessAdapterRegistry` in `pyrit.agent.harness` separate trusted
implementation code from inert profiles. `copilot` and generic `acp` are the built-in
registrations. A trusted Python initializer can register another adapter before composing
targets that select its name in `harness_kind`.

An adapter supplies immutable `HarnessAdapterInfo` (name, label, default command, and the
environment variable names it reserves for its own configuration) and the private-home
launch settings for the harness. It does not provision resources, implement ACP, persist
messages, or decide which tools run. Credential references may not claim a name the
adapter or the execution reserves (`HOME`, `PATH`, and similar process identity).

```python
import json

from pyrit.agent.harness import HarnessAdapter, HarnessAdapterRegistry
from pyrit.models.harness_adapter import HarnessAdapterInfo


class ExampleAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="example",
            display_name="Example agent",
            default_command=("example-agent", "acp"),
            reserved_environment=("EXAMPLE_CONFIG",),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"EXAMPLE_CONFIG": json.dumps({"permission": "ask", "home": home})}


HarnessAdapterRegistry.get_registry_singleton().register(ExampleAdapter())
```

The test suite keeps a complete reference adapter of this shape for OpenCode
(`tests/unit/agent/example_adapters.py`), validated against the same ACP core during
development; PyRIT does not ship or support it.

Registration is trusted setup code, not an import path supplied over REST. Conflicting names
cannot replace an existing implementation; use a new, versioned name when behavior changes.
Setup reset removes custom registrations, so the initializer must run again on every start.
Profile commands are always persisted explicitly, so decoding saved records never imports a
plugin. A missing adapter blocks new target use but not record decoding or interrupted
resource cleanup. Like other trusted Python components, an adapter is not a security boundary.

## Execution environment providers

`ExecutionEnvironmentProvider` and `EnvironmentProviderRegistry` in `pyrit.agent.environment`
are the seam between the runtime and the place a harness runs. **The runtime owns policy**:
timeouts, evidence budgets, cancellation, secret redaction, and the ACP protocol. **A provider
owns resources**: it allocates and releases exactly what it created, seeds the workspace,
passes the `ExecutionSpec` (command, environment, credentials) to the harness, and exposes the
harness's ACP byte streams and stderr through an `ExecutionSession`.

`local` is the first-party provider in this release. It runs the harness as a child process
of the PyRIT host with a private home and a fresh workspace. It is explicitly **not a
sandbox**: a process running as the same OS user can reach what that user can reach. Its
template therefore requires `local_execution_acknowledged=True`. An isolated Docker provider
follows in the next release of this series.

`EnvironmentTemplate.environment` always names the provider explicitly; there is no default
place to run an agent. A registered provider reads its own validated `environment_settings`;
first-party providers refuse settings they would ignore. `EnvironmentProviderInfo` declares
`capabilities` and an optional flat JSON `settings_schema` for authoring tools. `isolated`
defaults to `False`: a provider must claim isolation explicitly, and one that does not requires
the same `local_execution_acknowledged` consent as local execution.

```python
from pathlib import Path

from pyrit.agent.environment import (
    EnvironmentProviderRegistry,
    ExecutionEnvironmentProvider,
    ExecutionSession,
    ExecutionSpec,
)
from pyrit.models.agent_environment import EnvironmentCapabilities, EnvironmentProviderInfo
from pyrit.models.agent_execution import AgentExecution


class PoolSession(ExecutionSession):
    harness_home = "/home/agent"
    agent_cwd = "/workspace"

    def reserve(self) -> None:
        # Deterministic identifiers keep a crash during allocation reconcilable.
        self.execution.provider_id = f"pool-{self.execution.id}"

    async def launch_async(self, spec: ExecutionSpec) -> None:
        ...  # allocate, seed the workspace, start spec.command with spec.environment and spec.credentials

    async def close_async(self) -> None:
        ...  # release exactly the owned allocation; idempotent, also after a runtime restart

    # acp_reader / acp_writer / stderr expose the harness streams after launch_async


class PoolProvider(ExecutionEnvironmentProvider):
    @property
    def info(self) -> EnvironmentProviderInfo:
        return EnvironmentProviderInfo(
            name="team_pool",
            display_name="Team sandbox pool",
            capabilities=EnvironmentCapabilities(isolated=True),
            settings_schema={"type": "object", "required": ["pool"], "properties": {"pool": {"type": "string"}}},
        )

    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        return PoolSession(execution=execution, directory=directory)


EnvironmentProviderRegistry.get_registry_singleton().register(PoolProvider())
```

Providers must never log, journal, or persist `spec.credentials`, and must release only
resources they can prove they own (labels, deterministic names). `AgentExecution.resource`
holds the provider's durable handles for release after a restart; `provenance` holds
display-safe origin facts.

### Conformance

Both seams have executable contracts in `pyrit.agent.conformance`.
`verify_harness_adapter(adapter)` checks an adapter's declarations against what it emits
without running anything. `verify_environment_provider_async(provider, template=...,
echo_command=..., directory=...)` drives one real allocation with a byte-echo command and
checks stream ordering and duplexing, credential handling, consent, registration, and
idempotent release. Each returns a report naming every broken promise; call
`raise_for_failures()` from a test. PyRIT runs both against its first-party implementations.

## Authentication boundaries

A profile forwards its explicit `credential_env` references into the tested process. PyRIT
never discovers, brokers, or refreshes harness credentials: a profile names environment
variables, and the PyRIT process must hold their values when an execution is allocated. Those
values are exposed to the agent and its tools, and indirectly to anyone who can prompt the
target. They are resolved at launch, never appear in arguments, identifiers, or records, and
are redacted from the journal before frames are serialized. A value shorter than eight
characters cannot be redacted without corrupting unrelated content, so launch refuses it.

A model name alone does not authenticate a native Copilot harness. For local development,
export your own GitHub CLI token into the PyRIT process and reference it from the profile:

```bash
export COPILOT_GITHUB_TOKEN="$(gh auth token --hostname github.com)"
```

```powershell
$env:COPILOT_GITHUB_TOKEN = gh auth token --hostname github.com
```

Then set `credential_env=("COPILOT_GITHUB_TOKEN",)` on the `HarnessProfile`. Each execution
receives a fresh private home; no login files or prior sessions are copied. A process under
the local provider may still discover keychain or other login state belonging to the same OS
user (observed with Copilot CLI 1.0.95): a private directory is not credential isolation.
Do not treat an exported personal token on a shared backend as a deployment design.

## Administration

Creating an `AgentTarget` through `POST /api/targets` requires administrator access: the
class declares `requires_admin_creation = True` because every execution launches a process
with credentials the server holds, regardless of parameters. The create-target service
enforces this next to the existing `code_execution_parameter` and `server_credential_parameter`
checks. `agent_configuration` is accepted over REST as a JSON object because
`AgentTargetConfiguration` declares `JsonParameterValue`; the model validates it before
construction.

`fixture_directory` copies server files into the agent's workspace, so API-created targets
follow the same rule as other file-uploading targets: the class declares
`upload_directory_parameter = "allowed_fixture_directory"`, the server supplies its configured
`target_upload_directory`, and a fixture directory must resolve inside it. Direct Python and
initializer use pass no root and may name any path.
