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

- **Model binding** says which model answers: a harness-managed selection by name, or a
  registered PyRIT target whose single-inference capability the harness reaches through a
  scoped relay (see [Bring your own model](#bring-your-own-model)).
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

For an isolated run, replace the template with
`EnvironmentTemplate(environment="docker", image="pyrit-copilot-receipt:1.0.93", fixture_directory="assets/agent_receipt")`;
see [Docker](#docker) for building that image.

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

Harness profiles and environment templates are reusable by name. The `harness_profile`,
`environment_template`, and `agent_target` initializers register them from `.pyrit_conf`
and compose targets into the ordinary `TargetRegistry`; see
[reusable agent profiles](../../getting_started/pyrit_conf.md#reusable-agent-profiles-and-environment-templates).
From Python, `AgentConfigurationRegistry` holds the same named values and
`pyrit.setup.agent_initialization.register_agent_target` composes an
`AgentTargetComposition` without starting anything. Registered agent targets expose their
resolved `agent_configuration` and a provenance (initializer-defined or runtime-created)
through the target catalog; provenance is registry metadata, never part of identity.

Without a running manager of your own, `AgentTarget` uses the runtime-owned manager
(`pyrit.agent.runtime`), rooted in the memory results directory or, when results live in
remote storage, the local database data directory. The Co-PyRIT backend reconciles it at
startup, waits for its executions before shutting down, and closes it with the other services.
One manager exclusively owns its directory through a file lock; records left by an interrupted
runtime are reconciled and released at the next start, or marked `cleanup_failed` with the
reason when their provider is no longer installed. A record this version cannot decode (another
PyRIT version's schema, truncated evidence) is logged and skipped rather than blocking the
runtime; its directory is left untouched and listed in `store.unreadable_records`.

A manager admits at most `max_executions` concurrent executions (`PYRIT_AGENT_MAX_EXECUTIONS`
for the runtime-owned manager, default 4). A new conversation waits up to
`startup_timeout_seconds` for a slot before failing explicitly, so attacks with higher
concurrency queue rather than error.

## Bring your own model

A harness normally brings its own model access (for Copilot, a GitHub login). To test the
harness against a model PyRIT already knows how to reach, bind a **registered target** instead:

```text
PyRIT -- ACP --> harness
                   |  standard model API + execution-scoped relay credential
                   v
          inference-only relay  --->  registered PyRIT target  --->  provider
                                      (auth, token refresh, transport)
```

```python
from pyrit.models.agent_execution import ModelBinding
from pyrit.registry import TargetRegistry

TargetRegistry.get_registry_singleton().create_named_instance(
    name="judge-model", type_name="OpenAIChatTarget", params={"endpoint": "...", "model_name": "...", "api_key": "..."}
)
configuration = configuration.model_copy(
    update={"model_binding": ModelBinding(target_registry_name="judge-model")}
)
```

This is not an endpoint-settings exporter. Every model request the harness makes invokes the
bound target's `open_inference_async` (see [single inference](./0_prompt_targets.md#single-inference)),
so the source target keeps its provider credential or token callback; the harness receives only a
revocable, execution-scoped relay token, delivered through the adapter's declared provider
settings (`COPILOT_PROVIDER_*` for Copilot). Eligibility is decided by `inference_capabilities`
against the harness profile's `inference_requirements` (wire API, streaming, tool-call
passthrough, input modalities), never by target class name. Construction resolves the source and
pins its identifier hash into the configuration: a changed source yields a different agent
target, and a source that no longer matches is refused rather than silently rebound.

The relay serves only the bound model and wire API, a bounded request body, and
harness-executed tools; caller-supplied routing or authentication overrides, provider-hosted
tools, background inference, and server-side continuation or file references are rejected. Each
execution allows `max_inference_requests` admission attempts over its lifetime (default 100,
maximum 1000, counting invalid requests), at most four concurrent requests, 16 MiB per response, and the turn
deadline per request. Closing the execution revokes its lease and cancels in-flight inference;
the runtime stops the listener at shutdown, and the backend closes every registered target's
transport (`cleanup_target_async`) with its other services.

Every relayed request is journaled beside the ACP frames (`direction="inference"`): source
target identity, request identity, provider status and outcome, and byte counts.
`capture_inference_content=True` additionally retains request bodies and decoded response text; it is
off by default because content is sensitive, and provider request headers are never recorded.
Response content is buffered within the response byte limit and journaled as an
`inference.content` event when the stream settles (a partial prefix on failure), so known
literal credential values spanning transport chunks pass through runtime redaction together.
It is decoded UTF-8 text, not a byte-exact replay archive. Delivery to the harness is still
streamed unchanged; status and outcome events remain live.
A model requesting a tool is not evidence that the tool ran; see [tool calls](#tool-calls).

The listener binds `127.0.0.1` on an ephemeral port. A container cannot reach that address, so
the Docker provider refuses a loopback relay; on a trusted Docker Desktop machine set
`PYRIT_INFERENCE_RELAY_HOST=0.0.0.0` and `PYRIT_INFERENCE_RELAY_ADVERTISED_HOST=host.docker.internal`
on the PyRIT process, and restrict the host interface with a firewall. Providers that need a
tunnel translate the address in `ExecutionSession.inference_endpoint_async`. The HTTP relay is
for local or private networking, not untrusted networks.

`build_scripts/agent_byok_demo.py` verifies the routing with a real Copilot CLI, a registered
`OpenAIChatTarget`, and a synthetic provider that requires a secret only the target holds and
returns a streamed tool call; it needs no GitHub login and no real model:

```powershell
uv run --extra agents python -m build_scripts.agent_byok_demo --results dbdata\byok-local
uv run --extra agents python -m build_scripts.agent_byok_demo --docker --results dbdata\byok-docker
uv run --extra agents python -m build_scripts.agent_byok_demo --permission-policy deny --results dbdata\byok-denied
```

## What is recorded

Every ACP frame in both directions, the harness's stderr, and lifecycle transitions are
appended to the execution's journal, in order, with referenced credential values redacted.
The journal and the per-turn cursors into it are the evidence; `max_evidence_bytes`
bounds it, and capture failure is recorded on the execution rather than hidden.
`artifact_paths` names workspace files to copy out when the execution closes (bounded by
`max_artifact_bytes`).

## Tool calls

Each completed turn is projected from its journal into PyRIT's existing canonical tool
pieces (`function_call` / `function_call_output`), so `MessageToolCallScorer` with
`ToolsCalled` evaluates an agent turn like any other target's, including a **tool-only**
turn that finished without assistant text. The projection is conservative: a call needs an
explicit programmatic `name` and object-shaped `rawInput`; a human-readable `title` is never
used as a name, and an output counts as an invocation only when the harness reported execution
and a `rawOutput`. Denials, duplicate IDs, conflicting reports, and request-only
calls are not evidence of invocation; `AgentTurn.tool_call_omissions` records why each such
report was left out, and the raw report stays in the journal. Each turn pins its journal
range, so late reports cannot change already projected pieces.

Projected pieces carry `AgentToolCallProvenance` (execution, turn, source call ID, journal
sequence) in their metadata and outputs carry `ToolExecutionMetadata(invoked=True)`. Response
converters do not rewrite them. These persisted pieces are evidence, not replay instructions:
on the next turn the target verifies them against the journal and removes only verified pieces
from the history it prepares; copied, simulated, or edited native tool history is refused, and
the conversation keeps its live execution.

```python
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import ScoringExpectation, ToolCallRequirement, ToolsCalled
from pyrit.score import MessageToolCallScorer

attack = PromptSendingAttack(
    objective_target=target,
    attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
)
result = await attack.execute_async(
    objective="Read orders.json and write receipt.json",
    expectation=ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="write_file"),)),)),
)
```

Real harnesses may omit programmatic names in their ACP reports, in which case tool scoring
stays undetermined rather than false. No synthetic trace IDs are minted; `OtelToolCallScorer`
still needs real correlated trace evidence and can be composed with message tool scoring.

Timeouts are core policy and apply to every provider and harness: `startup_timeout_seconds`,
`turn_timeout_seconds`, `idle_timeout_seconds`, `lifetime_seconds`, and
`cancellation_grace_seconds`. A turn that exceeds its deadline is cancelled with the harness and
returned as a `cancelled` outcome carrying `agent_turn_error`, with any partial text, and its
execution is closed; only a cancellation the harness does not confirm within the grace period
raises. Interactive sends (below) keep the environment instead of closing it.

## Permission policy

ACP lets a harness ask before running a tool. `permission_policy` answers deterministically:
`deny` refuses (the default), `allow_once` accepts the single-use option the harness offers,
and `ask` forwards the request to the operator who owns the conversation. A harness that
offers neither single-use option fails the request explicitly. Policy answers are journaled
like every other frame. This is a protocol-level answer, not a tool sandbox or an allowlist:
what a permitted tool can reach is decided by the environment.

### Interactive sends

`ask` needs someone to answer, so it is only valid for an *interactive* send: a dispatch made
inside `pyrit.agent.send_context.interactive_agent_send(conversation_id)`, which the Co-PyRIT
manual send wraps around each chat turn. The scope is a context variable keyed by conversation,
never target state; an attack running the same target concurrently stays automated. Automated
sends reject `ask` before anything is provisioned
(`Automated agent runs require an explicit allow_once or deny permission policy`), and a
conversation cannot switch mode once its execution exists.

A permission request becomes a journaled `AgentApproval` on the execution (`permission.pending`),
with the harness's options and an expiry of `approval_timeout_seconds` (default 120). Exactly
one decision wins: an operator `allow`/`deny`, the timeout, or cancellation; it is journaled as
`permission.resolved` with the actor, and the flushed journal event is the commit point. A
failed record snapshot afterwards is logged and recovered from the journal at the next start,
and unresolved approvals left by an interrupted runtime are marked cancelled. While requests
wait, the turn's active-work clock pauses; the absolute lifetime does not. Denials, timeouts,
and cancellations project as `permission_not_granted` omissions in tool scoring, never as
invocations.

When an interactive turn exceeds its deadline and the harness confirms cancellation, the
execution is **held** rather than closed: the environment keeps running (and keeps consuming
capacity) for `interactive_hold_seconds` (default 300), bounded by the original lifetime.
*Continue* makes it available for a new turn without resending anything, *Extend* renews the
hold within the lifetime, and *Close* releases it; an expired hold is closed by the reaper. A
hold is not a snapshot, freeze, or rollback. Approval and hold timeouts are execution policy,
excluded from target identity like the other timeouts.

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

`docker` and `local` are the first-party providers. `docker` is the isolated reference
provider and declares the typed template fields it reads (`image`, `workspace_base`,
`docker_network`, `docker_memory`, `docker_cpus`). `local` runs the harness as a child process
of the PyRIT host with a private home and a fresh workspace; it is explicitly **not a
sandbox**, because a process running as the same OS user can reach what that user can reach,
so its template requires `local_execution_acknowledged=True`.

`EnvironmentTemplate.environment` always names the provider explicitly; there is no default
place to run an agent. A registered provider reads its own validated `environment_settings`;
first-party providers refuse settings they would ignore. `EnvironmentProviderInfo` declares
`capabilities` and an optional flat JSON `settings_schema` for authoring tools. `isolated`
defaults to `False`: a provider must claim isolation explicitly, and one that does not requires
the same `local_execution_acknowledged` consent as local execution.

### Docker

The Docker provider runs the harness in a fresh container per execution. The container is
created from the template's `image` with no host home, repository, or Docker socket mounted,
all capabilities dropped, no new privileges, and the template's `docker_network` (default
`bridge`; `none` disconnects it), `docker_memory`, and `docker_cpus` limits. The image tag is
resolved to an image ID before the container is created and retained as
`AgentExecution.image_id`, so a record names exactly the bytes that ran. The container runs as
the image's configured user; named users and groups are resolved from the stopped image's
account files, so images without those files should specify a numeric `USER uid:gid`.

`workspace_base="image"` uses the image's `/workspace` and keeps that root's permissions, so the
image must make it writable where required; `workspace_base="empty"` uses a unique private path
instead. `fixture_directory` overlays a private copy of host files into the workspace, prepared
with the image user's ownership. Each container is labelled with its execution ID; release
removes only containers carrying that label, including after a runtime restart.

Credentials are passed as container environment values, which Docker administrators can
inspect while the container exists, and are never written to the record or journal. Docker must
be able to reach the harness's model endpoints through its own network and trust store: an ACP
handshake alone does not prove authentication or model access.

### Benign receipt demonstration

`build_scripts/agent_receipt_demo.py` exercises the full path with a benign task: the agent
reads `orders.json`, writes `receipt.json` with total 31, updates it to 36 in a second turn, and
a fresh execution is verified to start from 31 again. The verifier reads retained artifacts, not
the agent's claims. The scripted mode drives a real subprocess over the real ACP SDK but **does
not call a model**:

```powershell
uv sync --extra agents
uv run --extra agents python -m build_scripts.agent_receipt_demo --scripted --results dbdata\agent-demo
```

To run real Copilot CLI in Docker, build the example image and reference a credential held by
your shell. `docker/agent_receipt/Dockerfile` pins the CLI version and base image digest and
includes the CA trust store the CLI needs:

```powershell
docker build --tag pyrit-copilot-receipt:1.0.93 --file docker\agent_receipt\Dockerfile .
uv run --extra agents python -m build_scripts.agent_receipt_demo --credential-env COPILOT_GITHUB_TOKEN --results dbdata\agent-demo-docker
```

`--local` runs the same demonstration under the local provider; it is not sandboxed.

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
receives a fresh private home; no login files or prior sessions are copied. A fresh Docker home
without supplied credentials reports that authentication is required: selecting a model does
not authenticate it. A process under the local provider may still discover keychain or other
login state belonging to the same OS user (observed with Copilot CLI 1.0.95): a private
directory is not credential isolation. Do not treat an exported personal token on a shared
backend as a deployment design.

Under a model binding the harness must not be able to bypass the relay: the adapter's declared
native model credentials (for Copilot, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`) are
rejected as credential references, as are names that override the private home, command search
path, or relay settings, and an ACP `authentication_method` is refused rather than ignored.
Tool-specific credentials still travel as ordinary references. The source target's provider
key is never forwarded; this is a delivery contract, not a claim that a local process running
as the same OS user cannot reach host secrets.

The target catalog shows an agent target's full `agent_configuration` to every authenticated
user, like the endpoints of other targets: credential environment variable *names* (never
values) and, for API-created targets, the `fixture_directory` resolved to its absolute server
path. Names are the authoring contract; the path reveals server layout, not content. Treat
the catalog as visible to everyone who can use the target.

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

`CreateTargetRequest.agent_presets` may name the registered harness profile and environment
template the configuration was copied from; the service verifies the current preset values
still match before registering and records the names as provenance. A changed or removed
preset is refused rather than silently resolved to new values. `GET /api/config/agent-presets`
lists the registered presets, installed harness adapters and environment providers, and the
template fields each first-party provider accepts; it never resolves credentials.

### Co-PyRIT API

Start the backend from the repository root with the explicit demo configuration, which
overlays the shared `~/.pyrit/.pyrit_conf` for this process only and loads no environment
files or Key Vault references:

```powershell
$env:PYRIT_AUTH_MODE = 'local'                   # Loopback-only development
$env:PYRIT_ALLOW_UNAUTHENTICATED_ADMIN = 'true'  # Trusted local use only
# Set COPILOT_GITHUB_TOKEN in this terminal using your approved credential source.
uv run --extra agents pyrit_backend --config-file docker\agent_receipt\pyrit.yaml --host 127.0.0.1 --port 8000
```

The demo configuration sets `target_upload_directory: assets`, so API-created targets may name
`fixture_directory: agent_receipt`; other fixture directories must live under that folder.

Conversation-scoped routes under
`/api/attacks/{attack_id}/conversations/{conversation_id}/execution` reuse the attack access
boundary of the chat transcript and never resolve another conversation's execution (a foreign
`execution_id` is a 404). They omit the structured fields that identify infrastructure and
credentials: owner, provider and process identities, image digest, and the profile with its
credential references. Turn errors and the journal are diagnostics from the harness and
provider and may mention container names or paths; they never contain credential values,
which are redacted before frames are written.

- `GET /` returns the current execution's operator-visible state, or `null` before provisioning.
- `GET /stream?after=0&execution_id=...` is an authenticated, fetch-based SSE feed of state
  snapshots and retained event pages with an execution-scoped resume cursor. A connection lasts
  at most 60 seconds before the client reconnects through the normal authorization checks;
  reconnects catch up from the journal, and a conversation whose execution was replaced receives
  a `reset` frame naming the new execution before its events replay from the start. Observers
  are woken only by their own conversation's changes (one coalesced wakeup each), and observing
  neither renews an execution's lifetime nor counts as active runtime work.
- `GET /{execution_id}/events?after=0&limit=100` pages the retained session updates, lifecycle,
  and inference events.
- `POST /{execution_id}/cancel` cancels this conversation's active turn, never the environment.
- `POST /{execution_id}/permissions/{approval_id}` with `{"allow": true|false}` (strict
  booleans) decides a pending approval; a stale or duplicate decision is a 409.
- `POST /{execution_id}/control/{continue|extend|close}` acts on an interactive execution; it
  is a 409 for automated executions or an expired hold.

Administrator routes under `/api/executions` are the resource view:

- `GET /` lists bounded execution pages (`conversation_id` filters a chat); `GET /{id}` reads
  resource state separately from turn outcomes.
- `GET /{id}/events?after=0&limit=100` reads the next retained event page; polling never
  renews the lease.
- `POST /{id}/cancel` cancels the active turn; `POST /{id}/close` releases resources,
  including retrying failed cleanup.

Each manual send bumps the execution's `transcript_revision` after its memory write, so a
reconnected viewer knows when to reload the transcript. A single backend process owns its
execution store; standalone managers are independently owned and are not administered by the
backend. Do not share a store directory between machines or backend workers.

### Co-PyRIT UI

The frontend exposes the same three configuration primitives and the same two views, and
nothing more than the API grants:

- **New target → Agent (ACP)** (administrators only) is a form with three sections, *Model*,
  *Harness*, and *Environment*, in that order. *Model* picks a registered connection for BYOK
  (the inference protocol is inferred when only one is compatible) or leaves the harness to
  authenticate on its own. *Harness* and *Environment* each offer the registered presets from
  `GET /api/config/agent-presets`; selecting a preset locks its fields and sends the preset names
  as provenance, while *Customize profile* / *Customize template* copies the values into
  editable fields and drops the provenance. Choosing a provider that is not isolated requires
  checking *I understand local tools can access the host*. The form never resolves credentials:
  *Harness credential references* holds environment variable names only. New configurations
  default the tool permission policy to *Ask me*; choose *Deny* or *Allow* for a target that
  attacks will drive. Timeouts are editable but excluded from target identity. *Refresh options* reloads
  the catalog after the backend reinitializes; edits made in the meantime are kept but cannot be
  submitted until the catalog verifies them.
- **Chat** with an agent target adds a status strip (feed, ACP connection, environment/state,
  pending approvals, *Cancel turn* while a turn is working, *Close execution* for interactive
  executions) above the transcript and renders each turn's streamed activity, tool calls, and
  the scoring status of its tool messages beneath the stored message. A pending permission
  shows *Allow once* / *Deny* under its tool call; decisions are server-owned, so a refresh or
  a second tab cannot lose or double-answer one. A held execution shows the remaining hold with
  *Continue* and *Extend hold*, and the composer stays disabled until it is continued.
  A request whose transcript has not arrived yet is shown as retained execution evidence rather
  than invented history. Model, harness profile, and environment template are fixed for the
  conversation; a closed execution tells the operator to start a new conversation. The strip
  follows the upstream operator, target, and resolution locks and becomes read-only with them.
- **Registry → Agent executions** (administrators only) lists the executions the backend owns,
  shows one record's state and configuration, and offers *Cancel turn*, *Close execution* /
  *Retry cleanup*, and *Copy configuration to new target*, which opens the agent form prefilled
  with the record's configuration. The copy starts a fresh execution on its next conversation;
  it never resumes the original. Chat links here with `?conversation=` so an operator can reach
  the resource view of the execution behind a transcript.
