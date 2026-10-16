# Testing an ACP agent

`AgentTarget` runs a real tool-capable harness rather than replacing its model or
tool loop. The initial integration uses the Agent Client Protocol (ACP) over
stdio. Install the optional dependencies with `uv sync --extra agents`.

```text
Model binding + Harness profile + Environment template
                         |
                  Configured agent target
                         | first send: provision and connect
                         v
Chat / scenario -> Active execution -> ACP -> Tested harness
                         |
                   Durable ACP journal
                    /             \
         Ordered chat activity   Validated tool projection
                                         |
                               Normalizer -> stored Messages
                                         |
                                MessageToolCallScorer
```

## Terminology and setup

- **Model binding** selects intelligence through a compatible access mechanism.
  It can select a harness-managed model or bind a registered PyRIT target that
  implements the required single-inference capabilities.
- **Harness profile** describes the executable, authentication references, and
  tool permission policy. It is independent of the selected model and placement.
- **Environment template** describes the starting filesystem and execution/access
  policy, not live resources.
- **Configured agent target** is the resolved system under test. PyRIT sends to it
  through the existing target interface.
- **Execution** is the active stateful instance: native agent session and owned
  environment.
- **Provisioning** creates and releases owned resources. ACP communicates with the
  harness; it does not provide sandboxing.

**New Target** is shared by the registry and New Chat. Choose an ordinary target
type or **Agent (ACP)** in the same target-type dropdown. The latter requires permission to configure
executable targets; existing registered targets remain selectable without that
configuration privilege. The native tools-off SDK target stays in the ordinary
target type picker.

The dialog expands in place to show **Model**, **Harness**, and **Environment**.
Switching to Agent and back retains the ordinary target's authentication and
parameters; selecting a different ordinary target type keeps its normal reset behavior.
The model picker lists registered connections using their model and registry
names, or **Use harness default**. A sole compatible inference protocol is selected
automatically; multiple compatible protocols are available in **Connection details**.
The optional harness model hint does not override the selected provider deployment.
**Environment settings** shows only parameters relevant to local/Docker execution,
using backend parameter metadata for defaults and types.
Edited environment policies are retained across runtime refresh. If their metadata
is unavailable, creation is blocked until those edits can be validated again.
Directory imports and artifact-path lists are not authored in this modal. Existing
initializer-defined or copied values are preserved unchanged; a summary identifies
retained non-default settings. Execution deadlines remain under **Execution settings**.
Registration does not provision resources. A new chat can derive a different
model binding from its selected agent's configuration without mutating the
original. Each registration is reusable within the current backend runtime;
use initializers/configuration for definitions that must survive backend restart.
The separate [`harness_profile`, `environment_template`, and `agent_target`
initializers](../../getting_started/pyrit_conf.md#reusable-agent-profiles-and-environment-templates)
define named reusable values and compose them through the same `TargetRegistry`.
They also work from Python or private initializer scripts; no new configuration
store is required.
The form selects these registered presets by name, displays their initializer
origin, and keeps their settings read-only until explicitly customized. It also
offers custom configuration when no preset is available. Selected snapshots are
checked against the registry on creation; reloading changed presets never silently
updates the target being authored. Registration in the GUI remains session-only,
even when its inputs came from initializers. The separate **Save for startup**
option writes a pinned snapshot as ordinary initializer entries to the existing
configuration source. It does not register or launch anything; restart the backend
to load it. See [startup authoring](../../getting_started/pyrit_conf.md#saving-an-agent-for-startup)
for dependency, naming, and conflict behavior.

Changing configuration creates a new target, not an in-place change to an active
conversation. The original flat `AgentProfile` input and retained execution
records are supported for compatibility. New authoring code should use
`AgentTargetConfiguration`; equivalent old/new configurations preserve target identity.

Each new PyRIT conversation gets a fresh process, native agent session, and
writable workspace. Subsequent turns use that same execution. Completing an
attack releases its conversations; interactive chat retains them until explicit
closure, idle expiry, maximum lifetime, or backend shutdown.

## Benign receipt demonstration

### Choosing the target surface

| Use | Model binding | Harness | Execution environment |
| --- | --- | --- | --- |
| Existing model target | Provider connection/authentication owned by the target | Target adapter; some targets have a high-level tool loop | No managed agent workspace |
| `GitHubCopilotTarget` | Copilot-managed model selection/login | Native Copilot SDK, deliberately **tools off** | Local working directory; no provisioned sandbox |
| `AgentTarget` | Harness-managed or compatible PyRIT raw-inference target | Tool-enabled agent driven through ACP | Fresh owned local/Docker environment per conversation |

Use **Create Target -> GitHubCopilotTarget** for native text or compatible
self-contained judge calls (`uv sync --extra github-copilot`). Use **New Target ->
Agent (ACP)** to test a tool-capable harness. The native SDK
target is not wrapped inside ACP or advertised as an inference-only source.
Its existing judge retries and session retirement remain native SDK behavior.
An agent under test and a tools-off Copilot judge can therefore be separate targets
in the same evaluation; the agent target itself is not a judge.

This complements the native SDK integration in
[microsoft/PyRIT#2828](https://github.com/microsoft/PyRIT/pull/2828), the tool-history
contracts in [microsoft/PyRIT#2852](https://github.com/microsoft/PyRIT/pull/2852), and
the message tool scorer in [microsoft/PyRIT#2854](https://github.com/microsoft/PyRIT/pull/2854).

### Running the receipt

From the repository root:

```powershell
uv sync --extra agents
uv run --extra agents python -m build_scripts.agent_receipt_demo --scripted --results dbdata\agent-demo
```

The scripted peer uses a real subprocess and the real ACP SDK, but **does not
call a model**. It writes a receipt with total 31, updates it to 36 in a second
turn, then verifies a fresh execution returns 31. The trusted verifier reads
retained JSON artifacts, not the agent's claims.

To test actual Copilot CLI locally, install Copilot and configure a supported
credential in your shell. Do not paste its value into the profile or command:

```powershell
uv run --extra agents python -m build_scripts.agent_receipt_demo --local --credential-env COPILOT_GITHUB_TOKEN --results dbdata\agent-demo-local
```

**Local execution is not sandboxed.** A fresh working directory and agent home
reduce accidental state sharing; they do not prevent access to the host.

For Docker:

```powershell
docker build --tag pyrit-copilot-receipt:1.0.93 --file docker\agent_receipt\Dockerfile .
uv run --extra agents python -m build_scripts.agent_receipt_demo --credential-env COPILOT_GITHUB_TOKEN --results dbdata\agent-demo-docker
```

The example image pins Copilot CLI and the base image digest, and includes the
system CA trust store required by the CLI. The execution resolves the image tag
to an image ID before container creation and retains that ID.

Docker must be able to reach GitHub/Copilot endpoints using its configured
network and trust store. An ACP handshake alone does not verify authentication
or successful model/tool execution. No host home, repository, or Docker socket
is mounted inside the test container.

## Use from Python

Initialize PyRIT memory normally, then explicitly scope standalone resources:

```python
from pathlib import Path

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import (
    AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile, ModelBinding,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget

configuration = AgentTargetConfiguration(
    name="copilot-receipt",
    model_binding=ModelBinding(),  # Harness default; use model="..." to select explicitly.
    harness_profile=HarnessProfile(
        credential_env=("COPILOT_GITHUB_TOKEN",),
        authentication_method="copilot-login",
        permission_policy="allow_once",
    ),
    environment_template=EnvironmentTemplate(image="pyrit-copilot-receipt:1.0.93"),
    artifact_paths=("receipt.json", "orders.json"),
)

async with AgentExecutionManager(root=Path("dbdata") / "agent-evidence") as manager:
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

The target can also be an existing attack's `objective_target`, including
`PromptSendingAttack`. Only compatible techniques should be selected: this
target accepts user text and supports multi-turn, but not arbitrary system
prompts or editable/replayed history. It never automatically resends a
side-effecting turn after a disconnection.

`command` is an executable/argument tuple, not a shell command. A different ACP
harness can supply its own command, authentication method, and image. Optional
client filesystem, terminal, and elicitation capabilities are not advertised;
requests to execute client-side tools are rejected, never run on the PyRIT host.
MCP configuration supplied through the harness's own launch configuration is
part of the tested system; remote services are not made fresh by a new workspace.

For harness-managed access, `model_binding.model`, when set, requires the agent to advertise an ACP model configuration
option. Otherwise configure the model explicitly in its launch command. Do not
assume the model selected by one harness is available in another.

## BYOK through an existing PyRIT target

```text
PyRIT -- ACP --> Copilot / registered harness
                    |
                    | standard model API + execution-scoped credential
                    v
             Inference-only relay
                    |
                    v
           Registered PyRIT target
           auth / token refresh / transport
                    |
                    v
                Provider
```

This is **not** an endpoint-settings exporter: every model request invokes the
bound target's `open_inference_async` capability. The source target retains its
provider credential or token callback. The harness receives only a revocable,
execution-scoped relay credential. No GitHub model-routing credential is needed
in BYOK mode; GitHub-dependent tools may have their own authentication needs.

In **New Target -> Agent (ACP)**, select an
**existing PyRIT target** as the model source. Options expose capability-based
incompatibility reasons. Target class names are not used for eligibility.
An adapter claiming compatibility is not proof that the selected model reliably
uses tools: test the actual model/harness combination.

Provider configuration uses an explicit harness adapter, not the executable's
filename. The built-in Copilot adapter supports the existing wire APIs; a
registered adapter declares its own. Custom ACP commands use harness-managed
model access until a provider-configuration adapter exists. This is separate
from source-target eligibility, which remains capability-driven.

### Harness and workspace selection

`harness_kind="copilot"` defaults to the Copilot ACP command and allows an
explicit executable path. `harness_kind="acp"` requires a command and passes no
Copilot configuration. A registered adapter's name also requires an explicit
command in Python and YAML; Create Target pre-fills the adapter's declared
default. Older custom commands stay generic ACP; they are not silently opted
into a new adapter.

The harness owns provider configuration and private-home settings. The
environment provider owns process/container placement and filesystem preparation. For
Docker, `workspace_base="image"` uses the image's `/workspace`;
`workspace_base="empty"` uses a unique private path instead. Local execution
always starts with a private empty workspace. `working_directory` is relative to
that root, while retained artifact paths remain root-relative.
The optional advanced `fixture_directory` overlays a private copy; images do not
require a runtime directory copy. No host home or repository is mounted.
Empty workspace directories, relative working directories, imported files, and
the private home are prepared for the image's configured user without running
the harness as root or granting it additional capabilities. Named users/groups
are resolved from the stopped image's account files; images without those files
should specify a numeric `USER uid:gid`. Image mode retains the existing workspace
root's permissions, so the image must make that root writable where required.

PyRIT answers the permission requests it receives with its configured policy.
Workspace/agent configuration and plugins can change which requests a harness
raises; this is not an enforcement boundary or tool allowlist.

Harness-managed access forwards only explicitly selected credential references;
no backend login is implicitly copied. Configure these under **Launch and
authentication** if needed. A private local workspace/home also does not prevent
the harness from discovering configuration in ancestor directories. Use a
results root outside source checkouts when inherited project configuration would
affect the test; local execution is not a sandbox.

### Installing another harness adapter

`HarnessAdapter` and `HarnessAdapterRegistry` in `pyrit.agent.harness` separate
trusted implementation code from inert profiles. Copilot and generic ACP are the
built-in registrations, not special branches in model binding or provisioning.
A trusted Python initializer can register an additional adapter before composing
targets that select its name in `harness_kind`.

An adapter supplies immutable `HarnessAdapterInfo` (name, label, command defaults,
supported inference APIs, native-model credential names, and the environment
variable names it reserves for its own configuration), private-home launch
settings, and optionally the translation of an execution-scoped inference relay
connection. It never receives the upstream provider key through this interface.
Advertising no inference APIs means native-model use only. Credential references
may not claim a reserved name. An adapter does not provision resources, implement
ACP, persist messages, score responses, or decide which tools run.

```python
import json

from pyrit.agent.harness import HarnessAdapter, HarnessAdapterRegistry
from pyrit.models.harness_adapter import HarnessAdapterInfo
from pyrit.models.model_inference import InferenceWireApi


class ExampleAdapter(HarnessAdapter):
    @property
    def info(self) -> HarnessAdapterInfo:
        return HarnessAdapterInfo(
            name="example",
            display_name="Example agent",
            default_command=("example-agent", "acp"),
            wire_apis=(InferenceWireApi.CHAT_COMPLETIONS,),
            native_model_credentials=("EXAMPLE_API_KEY",),
            reserved_environment=("EXAMPLE_CONFIG",),
        )

    def launch_environment(self, *, home: str) -> dict[str, str]:
        return {"EXAMPLE_CONFIG": json.dumps({"permission": "ask", "home": home})}

    def inference_environment(
        self, *, model: str, wire_api: InferenceWireApi, base_url: str, token: str
    ) -> dict[str, str]:
        provider = {"baseURL": base_url, "apiKey": "{env:PYRIT_RELAY_TOKEN}", "model": model}
        return {"EXAMPLE_CONFIG": json.dumps({"provider": provider}), "PYRIT_RELAY_TOKEN": token}


HarnessAdapterRegistry.get_registry_singleton().register(ExampleAdapter())
```

The test suite keeps a complete reference adapter of this shape for OpenCode
(`tests/unit/agent/example_adapters.py`), which was validated against the same
ACP core during development. PyRIT does not ship or support it.

The admin preset catalog publishes installed adapter metadata. Create Target uses
it for the harness picker, default command, and compatible model protocols; custom
preset and copied adapter names are preserved rather than changed to Copilot.
Profile commands are always persisted explicitly. Custom adapters require an
explicit command in Python/configuration, so decoding saved data never needs to
import a plugin or consult a live registry.

Registration is trusted setup code, not an import path supplied over REST.
Conflicting names cannot replace an existing implementation; use a new,
versioned name when behavior changes. Setup reset removes custom registrations,
so the initializer must run again on every start. The startup authoring operation
saves the profile, not its Python implementation. Install that initializer earlier
in the startup sequence. Missing adapters block new target use explicitly but do
not prevent historical record decoding, evidence export, or interrupted resource
cleanup. Like other trusted Python components, an adapter is not a security boundary.

Registering a harness does not imply support for a hosted environment or
coordinated checkpoint restoration; those are environment provider capabilities.

### Installing another execution environment provider

`ExecutionEnvironmentProvider` and `EnvironmentProviderRegistry` in
`pyrit.agent.environment` are the seam between the runtime and the place a harness
runs. The runtime owns policy: timeouts, evidence budgets, cancellation, approvals,
secret redaction, and the ACP protocol. A provider owns resources: it allocates and
releases exactly what it created, seeds the workspace, passes the launch
specification to the harness, and exposes the harness's ACP byte streams and stderr.
`docker` (isolated, checkpoint-capable) and `local` (explicitly not a sandbox) are the
first-party providers and are authored through the typed `EnvironmentTemplate` fields.

A registered provider is selected by name in `EnvironmentTemplate.environment` and
reads its own validated `environment_settings`. Its `EnvironmentProviderInfo`
declares `capabilities` (`isolated`, `checkpoint`) and an optional flat JSON
`settings_schema` that Create Target renders as fields. A provider that is not
isolated requires the same explicit `local_execution_acknowledged` consent as local
execution. Checkpoint operations are refused, not emulated, for providers without the
capability. Startup authoring persists only the first-party providers; register
templates for other providers from a Python initializer.

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

    async def inference_endpoint_async(self, *, relay_url: str) -> str:
        return relay_url  # or the address of a forward the session establishes

    async def launch_async(self, spec: ExecutionSpec) -> None:
        ...  # allocate, seed the workspace, start spec.command with spec.environment and spec.credentials

    async def close_async(self) -> None:
        ...  # release exactly the owned allocation; idempotent, also after a backend restart

    # acp_reader / acp_writer / stderr expose the harness streams after launch_async


class PoolProvider(ExecutionEnvironmentProvider):
    @property
    def info(self) -> EnvironmentProviderInfo:
        return EnvironmentProviderInfo(
            name="team_pool",
            display_name="Team sandbox pool",
            capabilities=EnvironmentCapabilities(isolated=True, checkpoint=False),
            settings_schema={"type": "object", "required": ["pool"], "properties": {"pool": {"type": "string"}}},
        )

    def create_session(self, *, execution: AgentExecution, directory: Path) -> ExecutionSession:
        return PoolSession(execution=execution, directory=directory)


EnvironmentProviderRegistry.get_registry_singleton().register(PoolProvider())
```

Providers must never log, journal, or persist `spec.credentials`, and must release
only resources they can prove they own (labels, deterministic names). A record whose
provider is no longer installed at startup is marked `cleanup_failed` with the
reason, rather than silently treated as released.

### Authentication boundaries

The native tools-off `GitHubCopilotTarget` retains its existing precedence:
explicit `github_token`, then `GITHUB_TOKEN`, then SDK login discovery. For a
hosted CoPyRIT instance, that is the **backend runtime's** login context, not the
web operator's identity.

Managed ACP profiles forward their explicit `credential_env` references into the
tested process/container. PyRIT never discovers, brokers, or refreshes harness
credentials itself: a profile names environment variables, and the backend process
must hold their values when an execution is allocated. These credentials are exposed
to the agent and its tools, and indirectly to users who can prompt that target.
Values are resolved at launch; they are not refreshed inside the environment. Docker
environment values are inspectable by Docker administrators while the container
exists. Values never appear in arguments, target identifiers, initializer files,
or execution records, and they are included in execution-evidence redaction.

A model ID alone does not authenticate a native Copilot harness. For **local
development**, export the token from your own GitHub CLI login into the backend
process and reference it from the profile:

```bash
export COPILOT_GITHUB_TOKEN="$(gh auth token --hostname github.com)"
uv run --extra agents pyrit_backend
```

```powershell
$env:COPILOT_GITHUB_TOKEN = gh auth token --hostname github.com
uv run --extra agents pyrit_backend
```

Then add `COPILOT_GITHUB_TOKEN` under **Harness credential references** in Create
Target, or set `credential_env=("COPILOT_GITHUB_TOKEN",)` on the `HarnessProfile`.
This is the backend account, **not the web operator's identity**, and it is
exposed to the tested agent and its tools. Each allocation still receives a fresh
private home; no login files, CLI configuration, or prior sessions are copied.

Do not treat an exported personal token on a shared hosted backend as a deployment
authentication design. Hosted native Copilot should use deliberately provisioned
credential references; organization-owned GitHub App authentication is a possible
future integration, not implemented automatic token brokering. Under BYOK, native
model credential references are rejected so the harness cannot bypass the scoped
inference relay.

A fresh Docker home without supplied credentials normally reports authentication
required. Selecting `gpt-6-astra` or another native model does not authenticate it.
Local execution is different: a process running as the same OS user may discover
keychain or other login state despite its private home. This was observed with
Copilot CLI **1.0.95**; a private directory is not credential isolation.
The native Docker **1.0.93** probe verified that explicit developer delegation
can authenticate `gpt-6-astra`, without requiring that model to be a registered
BYOK source. Availability still depends on the selected account's entitlements.

Selecting a PyRIT model target does not clear independent tool credentials.
Backend validation rejects known native-model credential variables under BYOK
(including Copilot's `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, and `GITHUB_TOKEN`), and
rejects references that override the private home, command search path, or relay
configuration. Use narrowly scoped, tool-specific credential references. A name
or a "tool-only" label does not prove a credential's permissions.

Current BYOK adapters do not perform ACP authentication negotiation. An explicit
`authentication_method` is therefore rejected, not silently ignored, and must be
removed deliberately when changing from harness-managed access. Harness-managed
mode still validates the requested authentication method against the ACP
handshake.

The source model target's provider key/token is **not forwarded** to the agent;
the model connection receives only an execution-scoped relay credential. This
is a delivery contract, not a claim that local execution under the same OS user
cannot access backend secrets or project files.

Copilot CLI **1.0.93** is the validation version. No claim is made that every
ACP extension, model, or harness version is supported; a registered adapter
carries its own validation claims.

In Python, after registering a compatible source target:

```python
configuration = AgentTargetConfiguration(
    name="copilot-with-pyrit-model",
    model_binding=ModelBinding(
        target_registry_name="my-model-target",
        model="gpt-4.1",  # Harness base-model identity, not an upstream deployment override.
    ),
    harness_profile=HarnessProfile(permission_policy="allow_once"),
    environment_template=EnvironmentTemplate(
        environment="local",
        local_execution_acknowledged=True,
    ),
)
target = AgentTarget(agent_configuration=configuration)
```

The target identifier is pinned at binding time. If omitted, the harness model
identity defaults to the source target's underlying-model name, then its model
name. The source target still owns the actual provider deployment. Changing a
registry alias does not retarget a live binding.

Built-in inference adapters are available on `OpenAIChatTarget` (Chat Completions)
and `OpenAIResponseTarget` (Responses). Custom targets can implement the same
optional contract and expose `InferenceCapabilities`. The adapter must preserve
provider response bytes/events and must not execute the harness's tools or
replay PyRIT history. Requirements include protocol, streaming, tool passthrough,
and required input modalities. Responses bindings select `wire_api="responses"`
on the model binding.

This does not replace `OpenAIResponseTarget(execute_tools=False)`: that existing
high-level API returns the first `Message` response without executing local tools,
but still performs message/history normalization and typed provider handling.
The inference relay instead needs unbuffered wire payloads, provider SSE/error
bytes, and no history replay. `InferenceCapabilities.tool_calls` describes this
wire passthrough only, not tool-history input support or observed execution.

Source targets with host-side tool configuration or custom body overrides that
cannot be safely composed are rejected. Configured sampling/output parameters
remain target-owned: non-null configured values override harness suggestions,
including temperature, top-p, penalties, seed, reasoning and output limits.
Reasoning options are merged by key, so a configured summary does not remove
an unconfigured harness reasoning effort (and vice versa).
Unconfigured parameters and tool/history/streaming payloads remain harness-owned.
For Chat Completions, a configured `max_completion_tokens` also replaces the
harness's legacy `max_tokens` alias rather than sending conflicting limit fields.
Completion-count conflicts still fail. Overrides are logged by parameter name,
without request/credential values; neither the target's configuration nor the
caller's request object is mutated. This lets an existing model connection with
configured temperature serve an agent harness without changing its
direct-chat settings. Inference uses
the existing target client and refreshable authentication. It shares target
pacing, with a five-second admission budget, and performs no inference retries: the
harness owns inference retries. Mid-stream failure is never rewritten as a
successful final answer.

The target's inference path uses the HTTP transport shared with its OpenAI client,
not the SDK's typed response/error parser. This preserves unknown payload fields
and avoids eager buffering of large provider error bodies before byte limits can
be enforced. HTTP redirects are not followed by this inference path.

### Local and Docker relay reachability

By default the inference listener binds only to `127.0.0.1` on an ephemeral port.
It has no registry, administration, or general proxy routes.

Docker requires explicit reachability. For a trusted Docker Desktop development
machine, set these on the backend/demo process:

```powershell
$env:PYRIT_INFERENCE_RELAY_HOST = '0.0.0.0'
$env:PYRIT_INFERENCE_RELAY_ADVERTISED_HOST = 'host.docker.internal'
```

This exposes a token-protected inference listener on host interfaces. Apply host
firewall/network restrictions; use an appropriate private address in deployed
environments. Do not expose it as an unauthenticated public service. The HTTP
transport is intended for local/private networking, not untrusted networks.
Local process execution is not OS-level credential isolation from the host user.

The relay permits only the bound model/protocol, a bounded request body, and
harness-executed tools. Caller-supplied routing/authentication overrides,
provider-hosted tools, background inference, and server-side continuation IDs are
rejected, including provider-side item/file references. Each execution allows up
to 100 inference requests, at most 16 MiB per
response, and the configured turn deadline per request.

On execution closure the binding is revoked and in-flight inference is cancelled.
The backend stops the listener and closes target-owned transports at runtime
shutdown. Standalone callers own their registered source targets and should call
`cleanup_target_async()` when those targets are no longer needed; closing an
execution does not close a source target used by other executions.
No provider key is written to
the image, profile, execution record, or relay credential.

### Verify the actual routing without provider credentials

The BYOK smoke test uses a **real Copilot CLI 1.0.93**, a registered
`OpenAIChatTarget`, and a synthetic provider. The fake provider requires a secret
held only by the target, returns a streamed tool call, and verifies that Copilot
returns its tool result. It does not call a real model or need GitHub login.

```powershell
uv run --extra agents python -m build_scripts.agent_byok_demo --results dbdata\byok-local

# Build the 1.0.93 image and configure Docker relay reachability as above first.
uv run --extra agents python -m build_scripts.agent_byok_demo --docker --results dbdata\byok-docker

# Verify an explicit permission refusal, without requiring a fabricated text answer.
uv run --extra agents python -m build_scripts.agent_byok_demo --permission-policy deny --results dbdata\byok-denied
```

Chat Completions was verified end to end in both local and Docker modes.
Responses raw-wire behavior is covered by target tests; the smoke test does not
establish Copilot/Responses compatibility or arbitrary model compatibility.
Refreshable token callbacks are covered by isolated tests; a live Entra refresh
cycle must be validated in the deployment using that identity.

Inference metadata (source target identity, request identity, provider status,
outcome, response bytes) appears beside ACP evidence. Chat displays a separate
model-inference summary: a model requesting a tool is not evidence it executed.
`capture_inference_content=True` additionally retains request bodies and response
chunks (base64) in the journal. It is off by default because content is sensitive.
Provider request headers are never copied into these records.

Inference turn correlation uses the execution's active-turn window, not a
harness-provided causal identity. Utility/subagent calls can overlap that window;
do not treat the association as proof of causality. Calls outside an active turn
remain execution-level evidence.

## Backend and central administration

Start the backend from the repository root with the explicit demo configuration:

```powershell
$env:PYRIT_ALLOW_UNAUTHENTICATED_ADMIN = 'true' # Trusted local use only
$env:PYRIT_DEV_MODE = 'true'
# Set COPILOT_GITHUB_TOKEN in this terminal using your approved credential source.
uv run --extra agents pyrit_backend --config-file docker\agent_receipt\pyrit.yaml --host 127.0.0.1 --port 8000 --log-level DEBUG
```

In a second terminal, also starting from the repository root:

```powershell
Set-Location frontend
npm run dev -- --host localhost
```

Open `http://localhost:3000/registry/executions`. The
[demo configuration](../../../docker/agent_receipt/pyrit.yaml) overrides shared
initializers, environment-file loading, and Key Vault bootstrap references for
this process only. It does not modify `~/.pyrit/.pyrit_conf`. Credentials must be
supplied explicitly in the backend environment. Default model/scorer targets
are not registered by this minimal profile; configure them separately when a
scanner technique requires them.

If requests return **503: PyRIT runtime is unavailable**, inspect readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/config/runtime | Format-List
```

The backend can serve health/configuration routes while initialization has
failed; a healthy HTTP listener does not mean the runtime is ready. Inspect the
backend's `PyRIT startup failed` traceback for the underlying error. For example,
an older shared config containing `initializers: [simple]` fails because this
checkout uses separate `technique`, `target`, `converter`, and `scorer`
initializers. Use the explicit demo configuration above, then restart the
backend. Do not disable the readiness gate or hide the failed initializer.

Open **Registry -> Targets -> New Target**, or **New target** in a new
chat, to register a configured target. Registered targets become available in the
existing chat and scanner selectors.

Tool activity is displayed **inside the conversation**, after the user turn that
caused it. Reports update in place by tool-call ID, with expandable inputs, outputs,
and affected locations. A display title is not misrepresented as a programmatic
tool name. Reported file changes are not claims of independently verified artifacts.
Text and tools use an ordered, deterministic journal projection: text before a
tool stays before it, text between calls stays between them, and status changes
update the original tool card rather than moving it. Text chunks in the same
message stay together across telemetry, transport, and status-only events; a
new message, newly appearing tool, or plan starts a new content block. This keeps
Markdown code fences and lists intact without moving text across tool calls.
The live view and replay use the same reducer and ignore repeated
event sequences after reconnect.

The final recorded response remains available for scoring, copying, and export.
Its text is not repeated below the activity when it exactly matches the retained
ordered text. Converted responses or incomplete/missing activity keep their own
visible response rather than silently substituting evidence.

Reopening an active chat shows execution activity even before
the user message is committed to its transcript, and terminal updates refresh
the transcript. Failed turns retain available streamed output, clearly labeled
as partial rather than a completed reply.
Expanded tool fields show up to 12,000 characters with an explicit truncation notice.
The journal remains the complete retained source up to the configured capture budget.

Operators can observe and cancel through conversation-scoped routes using the same
attack/conversation access boundary as the chat transcript. These routes verify
attack membership and never expose unrelated executions or infrastructure configuration.
They do not introduce new user-level ownership rules beyond the existing backend's
authentication/attack access model.

**Registry -> Agent executions** remains the administrator's resource view:
inspect resolved configuration and raw evidence, close resources, retry cleanup,
or copy a configuration for a new target. Normal chat observation no longer
requires this view.

New UI configurations default to **Ask operator**. Permissions are server-owned
pending requests with an expiry and one recorded decision, so refreshing a page
does not lose the approval. Allow once / Deny controls appear in the turn activity.
Conflicting decisions from two tabs cannot both win. Timeout denials and cancelled
permissions are distinguished from operator decisions.

A flushed permission journal event is the decision's commit point. Timeout and
cancellation wait for an in-flight decision rather than contradicting it.
Execution snapshots are recoverable projections: a failed snapshot write is
logged, but cannot roll back a journaled approval. Startup recovers committed
permission state from the journal before reconciling interrupted executions.

The active-work turn clock pauses while one or more permission requests wait;
`approval_timeout_seconds` (default 120) bounds that wait independently. The
absolute execution lifetime continues to run. Automated sends reject `ask` before
provisioning and require an explicit `deny` or `allow_once` policy. Existing
configurations retain their previous policy. Approval is **not** an exhaustive
tool allowlist or an isolation boundary.

## Live feed and connection monitoring

Chat uses authenticated **fetch-based SSE**, not a periodic summary/events poll.
The browser supplies its normal authorization header; bearer tokens are not placed
in URLs. A connection lasts at most 60 seconds before reconnecting through the
existing HTTP authorization checks (including their normal cache policy).
The fetch-based stream also supplies the frontend's bundled
`PyRIT-Compatibility-ID`, just like normal API requests. Missing/mismatched build
identities block the frontend rather than appearing as an unexplained activity
failure. Stream errors retain the backend's actionable problem detail.
The feed carries execution-scoped cursors, state snapshots and retained event
pages; a reconnect catches up before continuing live delivery.

Each observer has one coalesced wakeup rather than an unbounded event queue.
Slow observers replay from the journal. A cached journal offset index avoids
rescanning history for every page. At most 64 observers are admitted per owner.
Disconnecting a viewer releases its subscription; it neither cancels an agent
turn nor renews the execution's lifetime. Observer requests do not count as active
runtime work for configuration replacement.

The chat status strip distinguishes **browser feed**, **ACP session**, and
**environment** state. An idle harness is not called disconnected. ACP readiness
is based on session initialization, not just process creation; EOF marks its
connection disconnected. Additional details show the last retained event and
absolute execution expiry.

**Harness completion is not model-inference success.** A harness can finish a
turn by returning an error after its provider retries fail. Failed model requests
are shown visibly in agent activity, even when the harness reports `completed`;
request history distinguishes failures followed by a successful retry.
Backend connection failures report sanitized DNS, TLS verification, timeout, or
general connectivity guidance in both the journal and relay response. Credentials,
raw exception diagnostics, and provider URLs are not included in these messages.
For DNS failures, inspect the selected target's endpoint and DNS/VPN access from
the **backend machine**, not the agent container or browser. A configured
temperature-override warning alone is not a model connection failure.

The operator routes are under
`/api/attacks/{attack_id}/conversations/{conversation_id}/execution`:

- `GET /stream?after=0&execution_id=...`: live state/events and bounded reauthorization.
- `POST /{execution_id}/permissions/{approval_id}` with `{"allow": true|false}`.
- `POST /{execution_id}/control/continue`, `/extend`, or `/close`.

These follow the existing authenticated attack/conversation boundary, not a new
per-user isolation model. Resource-wide administration remains separate.

The API is under `/api/executions`:

- `GET /` lists bounded execution pages; `conversation_id` filters a chat.
- `GET /{id}` reads resource state separately from turn outcomes.
- `GET /{id}/events?after=0&limit=100` reads the next retained event page.
- `POST /{id}/cancel` cancels the active turn, not the entire session.
- `POST /{id}/close` releases resources, including retrying failed cleanup.

These are administrator routes, reusing PyRIT's existing authorization. For an
explicitly trusted local deployment without authentication, the existing
`PYRIT_ALLOW_UNAUTHENTICATED_ADMIN=true` setting enables administration. Do not
expose such a deployment to an untrusted network.

A single backend process owns its execution store. Standalone notebook/CLI
managers are independently owned and are not remotely administered by the
backend. A filesystem lock prevents two managers from concurrently claiming the
same store. Do not share that directory between machines or backend workers.
The default manager allows four live executions, including idle chats. Close idle
executions or limit scanner concurrency to that capacity; standalone managers can
set `max_executions` explicitly. Capacity exhaustion fails with an actionable
error rather than sharing a used session.

## Lifetime, recreation, and retention

Profiles are recipes; executions are allocated instances. No container is
launched by target construction or registry discovery.

- For interactive chat, turn timeout requests cancellation. Confirmed cancellation
  retains the running environment in **held** state for `interactive_hold_seconds`
  (default 300), bounded by its original absolute lifetime.
- **Continue** makes a held execution available for a new turn; it does not resend
  the interrupted prompt. **Extend hold** renews the grace period but cannot exceed
  the original lifetime. **Close execution** releases its resources.
- Automated runs retain strict predeclared deadline behavior. Unconfirmed
  cancellation, lost connections, and execution-fatal errors still close resources.
- A held environment is still running and consumes capacity. It is not a
  snapshot, process freeze, rollback, or provider suspend operation.
- Idle expiry applies only between turns. Observing events does not renew it.
- Maximum lifetime bounds allocation while the owner is running.
- On startup, the owner reconciles incomplete records and releases its recorded
  resources. A running backend is required to enforce expiry; there is no
  independent crash-proof TTL supervisor in this MVP.
  Held executions are also closed during restart reconciliation. Only an explicitly
  saved checkpoint can restore native state; a crash is not a checkpoint.
  Unresolved permissions from interrupted owners are cancelled.
- Live executions block configuration replacement, including idle chats.
- Cleanup uses exact process identities/container ownership labels. Failures
  remain visible; resource closure is not inferred from a disconnected pipe.
- After successful cleanup, private runtime workspace/home directories are
  removed. Explicitly selected, size-bounded artifacts and evidence remain.

Closed/expired conversations cannot be silently recreated. **Copy configuration to new
target**, then start a new conversation, creates fresh state on first send.
The copied profile uses the recorded image ID and fixture hash. A changed or
missing source fixture fails explicitly; keep versioned fixtures available.
This is not resume, rollback, or a snapshot of the previous agent. The explicit
checkpoint operation below is separate. There are no used-session pools,
operator takeover, or orchestrator steering.

Evidence is stored beneath the configured memory results directory in
`agent_executions`, or the standalone manager's explicit directory. Records are
atomic files and events are append-only JSONL; this MVP evidence store is local,
not an Azure SQL execution index. Compute expiry does **not** delete evidence.
Operators manage evidence retention separately; protect that directory because
prompts, tool outputs, and artifacts may be sensitive.

## Evidence and outcome semantics

### Save and close / Restore same chat

Administrators can open **Checkpoint options** from Executions or the chat's
execution details. **Save and close** requires explicit consent to retain sensitive
native session and workspace data. A completed checkpoint survives runtime restart;
its original allocation stays closed. **Restore same chat** starts a new allocation
with fresh lifetime, model lease, and permission state. It sends no prompt.

The first runtime slice deliberately accepts only the tested combination:

- Docker image workspace at `/workspace`, without overlays, relocated working
  directories, mounted filesystems, or a volume-declaring restore image;
- Copilot **1.0.93**, as advertised by the running harness and supported by the
  installed adapter's versioned checkpoint layout;
- target-backed BYOK with no forwarded credential variables or ACP login;
- an idle, connected execution with fully captured completed turns and no
  unresolved permission request. Held, cancelled, failed, or in-flight work is
  not a checkpointable state.

Registered trusted adapters may declare their own `HarnessCheckpointLayout`.
This is an explicit persistence contract, not automatic discovery of arbitrary
home-directory contents. The two built-in state layouts are described in the
feasibility proof below.

Capture owns the execution transition until completion, even if its caller
disconnects. It revokes the old model lease, stops and verifies the container,
captures bounded archives, and releases the original resources before publishing
a checkpoint. Incomplete capture or cleanup fails explicitly. Concurrent sends
and checkpoint commands share the backend's existing conversation reservation.
Standalone Python callers must also avoid unrelated direct memory writes.

Restore is restricted to the **latest saved allocation of the same chat**:
the original registered target configuration, model identity, transcript hash,
source journal bytes/cursor, image ID, harness version, and installed state layout
must still match. Neither an edited transcript nor a prior checkpoint can rewind
a newer allocation. Preflight failures allocate nothing. Once a restore allocation
is recorded, failures remain visible and that checkpoint cannot be silently
retried or forked; start a fresh chat if restoration fails.

The new execution references its parent and checkpoint. The original turns and
journal are not copied or rewritten. Native `session/load` history notifications
are retained with direction **`restore`**, no new-turn ID, and no canonical
tool-execution projection. Model inference and permission approval are disabled
during loading. Only a subsequent user prompt enables the normal tool loop.
Before extraction, a trusted preparation process clears the fresh container's
workspace contents and native-state subtree. This preserves deletions and
file/directory replacements rather than merging files back over the original
image. The preparation process then replaces itself with the configured harness;
there is no persistent extra supervisor or host mount. This path requires the
Linux shell and core file utilities provided by the tested images.

Private state lives under the execution store's `checkpoints/<id>` directory,
not in the evidence ZIP. Each checkpoint includes immutable metadata and two
archives with exact SHA-256/byte lengths. Each archive is capped at **64 MiB**
and **2,000 entries**, and transcript binding at **16 MiB**. The store retains at
most **eight checkpoints**. Links, special files, escaped paths, and known live
credential bytes are rejected; this is not a general secret scrubber.
In-flight captures reserve retention capacity before stopping their source,
so concurrent saves cannot oversubscribe the last slot.
Protect this storage with the same host access controls as other sensitive test
data. There is no automatic retention expiry or archive import in this slice.

**Delete checkpoint** requires confirmation and removes only the private saved
state. Original conversation/journal evidence and any restored running allocation
are retained. Deleting a checkpoint removes the ability to restore it.
Interrupted deletion remains visible and retryable; its checkpoint is marked
non-restorable until cleanup finishes.

The admin API uses:

- `GET /executions/{id}/checkpoint`: metadata and readiness, not state bytes;
- `POST /executions/{id}/checkpoint` with
  `{"acknowledge_sensitive_state": true}`: save and close;
- `POST /executions/{id}/checkpoint/restore`: resolve the unchanged registered
  target and restore the latest same-chat checkpoint;
- `DELETE /executions/{id}/checkpoint`: delete private checkpoint state.

Python callers use `AgentExecutionManager.save_checkpoint_async`,
`restore_checkpoint_async`, and `delete_checkpoint_async` with caller-owned
memory and a resolved pinned model binding. These operations do not score,
select prompts, or restore external side effects. They are application-state
restoration, not process-memory checkpoints or exactly-once execution guarantees
for arbitrary tools.

### Docker checkpoint feasibility proof

The developer-only `build_scripts.agent_checkpoint_demo` now demonstrates
**application-state restoration after a completed turn**, independently of the
production execution lifecycle. It does not add a Resume button or allow restoring
closed `AgentTarget` conversations.

```text
Completed native session + workspace
                 |
      stop container; revoke model lease
                 |
      bounded state/workspace archives
                 |
        remove original container
                 |
    new container from the same image ID
      + restored files and ownership
      + newly issued model lease
                 |
    ACP initialize -> session/load -> new prompt
```

The proof uses an in-process synthetic inference target behind the existing
execution relay; **no real model provider is called**. The actual Copilot
binary runs its real tool loop in Docker. A seed turn writes a receipt and an
append-only effects log, then receives a random assistant-only history token.
After the original container is removed, a new process must supply that token
and the prior tool result from its own persisted history. The caller sends only
a new continuation prompt, never reconstructing the old conversation for it.
The continuation advances the receipt from 31 to 36 and the write count from one
to two. Trusted artifact reads require exactly `seed` then `continued` in the
effects log.

The same run checks four independent fresh containers:

| Restored data | Required result |
| --- | --- |
| Neither | Loading the session fails; no inference |
| Workspace only | Loading the session fails; no inference |
| Native session only | History returns, but the missing workspace prevents continuation |
| Both | Native history and actual filesystem continuation both verify |

These controls passed with **Copilot CLI 1.0.93**, including a derived image
running as a non-root user.
Restoration preserves file UID/GID and creates the native-state parent
directories with the image user's ownership; otherwise newly created parent
directories can prevent the harness from starting.

Run against disposable receipt images, using a **new** results directory:

```powershell
uv run --extra agents python -m build_scripts.agent_checkpoint_demo `
  --harness copilot --image pyrit-copilot-receipt:1.0.93 `
  --relay-host 0.0.0.0 --advertised-host host.docker.internal `
  --results dbdata\checkpoint-copilot
```

The listener interface is explicit because Docker must reach the relay. Only the
fresh execution token grants inference authority; the original token is revoked
and tested for rejection. Use a trusted development machine/network. No host
directory, Docker socket, or operator credential is mounted into the agent.
The proof refuses volume-declaring images before container creation, rejects
mounted runtime filesystems, and cleans up only its labeled containers.

The output includes `checkpoint.json`, `workspace.tar`, `harness-state.tar`,
`report.json`, and bounded stderr plus protocol/inference diagnostics. Saved
archives are reread and their exact byte lengths and SHA-256 values verified
before restoration. Archive reads are limited to 64 MiB and 2,000 entries each;
links, special files, path escapes, and persisted relay tokens are rejected.
Private state is **not generally safe to distribute**: this proof uses synthetic
data in isolated homes. Its token check is not a general credential scrubber.

Important boundaries:

- This uses ACP [`session/load`](https://agentclientprotocol.com/protocol/v1/session-setup#loading-sessions),
  whose advertised capability is checked. It does not exercise `session/resume`.
  Load replays historical notifications from the harness to the client; these
  are not newly executed tools and must stay separate from new-turn evidence.
- Copilot's snapshot contains its native `session-state` directory, captured
  from the stopped filesystem. A harness that keeps state in a database must
  capture its journal/WAL files from the same stopped filesystem; copying a live
  database is not the tested operation. Layouts are version-specific, not ACP fields.
- Only `/workspace` and the selected native-state subtree are preserved, with
  the same absolute paths in the restored container. Unique/relocated workspace
  roots, arbitrary images, MCP state, plugins, and multi-session homes are not
  covered by this proof.
- No process memory, in-flight turn, background job, socket, mounted volume, or
  external side effect is restored or rolled back. A forced kill or OOM is not
  accepted as a successful checkpoint capture.
- No `docker commit`, privileged CRIU checkpoint, cloud service, or image
  publication is involved. [`docker cp`](https://docs.docker.com/reference/cli/docker/container/cp/)
  supplies file archives from stopped containers; application restoration is
  demonstrated by the subsequent behavioral checks, not inferred from copying.

The runtime operation above adds checkpoint identity/lineage, adapter-declared
layouts, capture admission, sensitive-state retention, fresh leases, and
restored-history evidence boundaries. The proof remains independently runnable.
Startup saves are still **configuration only**, and evidence packages are still
**read-only recordings**, not transferable executable checkpoints.

### Exporting a recording

Administrators can inspect a **closed** execution's recording in Executions or
the conversation's execution details, and download its evidence package. Playback
uses the same ordered-activity reducer as the live view. Moving the event cursor
never sends prompts, approves requests, or runs tools. An execution with live
resources, a running turn, or an unresolved permission must settle first; export
does not close or extend it on the operator's behalf.

The admin API exposes `GET /executions/{id}/evidence` for the bounded snapshot and
`GET /executions/{id}/evidence.zip` for the package. Ordinary conversation observers
do not receive this raw administrative export. Python callers can use
`AgentEvidenceExporter` from `pyrit.agent.evidence` with their existing manager
and memory; it takes ownership of neither.

```python
from pyrit.agent.evidence import AgentEvidenceExporter

exporter = AgentEvidenceExporter(manager=manager, memory=memory)
snapshot = await exporter.snapshot_async(execution_id)
package_bytes = await exporter.archive_async(execution_id)
```

Each ZIP contains:

- `manifest.json`: identities, capture time, exact journal cutoff, coverage,
  omissions, and SHA-256/byte length of every other file;
- `execution.json`, `events.jsonl`, `messages.json`, `scores.json`;
- `recording.html`: escaped, offline, script-free ordered event details;
- `artifacts/`: only explicitly retained regular files, never an arbitrary
  workspace or home-directory crawl.

Limits are 10,000 recorded events, 1,000 retained artifact entries, and 64 MiB
for both total uncompressed package bytes (including the manifest) and ZIP
download bytes. A snapshot must also fit 64 MiB. Artifact reads obey the execution's
per-file retention limit. Global overflow fails rather than returning a silently
truncated package. Missing, unreadable, or linked retained artifacts are omitted
with explicit reasons; invalid paths, journal gaps, or crossed identities fail.
The backend admits at most two simultaneous snapshot/package acquisitions.

The journal prefix is pinned; **messages and scores are current memory at export**,
not an atomic historical database snapshot. External observation/trace payloads
and message attachment bytes are not embedded. Their stored references remain.
Provider credentials are not deliberately collected, but prompts, tools, outputs,
and retained artifacts may themselves contain secrets. Protect downloads as
sensitive test data. File hashes detect changed bytes; they do not establish the
truth or completeness of harness reports.

The assessment distinguishes four operations:

| Operation | Meaning |
| --- | --- |
| Playback | Available or partial retained-event inspection; no execution |
| Re-score | Partial when messages exist; compatible scorers may need original memory, observations, or a judge |
| Recreate | Unverified: configuration exists, but image/binary availability, credentials, and model dependencies are not proved |
| Resume | Unavailable without a saved checkpoint; unverified when a separate checkpoint is recorded, pending live compatibility checks |

There is no archive import or automatic score rerun in this slice. Normal PyRIT
message/observation scoring remains the evaluation path.

Prepared input is written before dispatch. Protocol frames are captured before
delivery/processing, in local sequence order, including unknown extensions.
Configured credential values are redacted from captured text. This is not a
general secret-discovery service: use synthetic test data and scoped credentials.

The event journal is mandatory and independent of live viewers. Event size and
total evidence budgets are bounded. Capture failure stops the protocol path;
an unfinished turn is incomplete or unknown, never an inferred negative result.
`capture_complete` describes retained ACP traffic for the completed turn, not
visibility into every internal agent operation.

ACP tool reports and some tool fields are optional. Missing events do not prove
that a tool was never executed. The live feed catches up through retained journal
pages up to the configured capture budget.

### Existing tool scorers and canonical messages

The agent adapter projects a turn's retained journal into existing
`FunctionCallContent` / `FunctionOutputContent` messages. It requires an explicit
programmatic `name` and object-shaped `rawInput`; it never guesses the name from
`title`. An invocation-bearing output additionally requires reported execution
(`in_progress` before the result, or `completed`) and `rawOutput`. A failed tool
counts as an attempt only when execution was reported. Denials, duplicate IDs,
conflicting reports, request-only calls, and missing results do not become
successful invocation evidence. A display-only update still stays in the journal.

Each canonical piece carries execution/turn/call/event provenance and outputs
carry the existing `ToolExecutionMetadata(invoked=True)` contract. Call IDs are
scoped to execution and turn. `MessageToolCallScorer` can now evaluate these with
`ToolsCalled`, including a completed **tool-only** turn; its existing conversation
prefix, simulated-history exclusions, and undetermined-not-false rules apply.
Response converters do not rewrite canonical tool call/result messages.
Each completed turn pins its journal prefix so late reports cannot change already
projected evidence. Manual chat records the final returned response (including
a tool-only result), rather than an earlier assistant tool request, as the attack's
scoring reference.

Persisted native tool messages are evidence, not replay instructions. On the next
turn the adapter verifies them against the journal and removes only those verified
pieces from the target-facing history. Arbitrary/simulated tool history remains
unsupported, and copying history cannot restore a native session.

The scripted peer reports explicit names for testing. Real Copilot ACP reports
may omit them, in which case tool scoring remains undetermined rather than using
the human-readable title as a name. Chat exposes per-call omission reasons and
canonical provenance while preserving piece-local scores. Exports retain
canonical messages and a separately labelled snapshot of loaded agent activity,
with capture/feed state; that snapshot is not a complete raw-journal export.
No synthetic OTel trace IDs/spans are minted. `OtelToolCallScorer` still requires
real correlated trace evidence from its configured source; it can be combined
with message tool scoring using the existing composite scorer.

`TargetResponse` preserves message-list compatibility while carrying an explicit
terminal status and evidence references. The prompt normalizer persists the
request and any partial response, then raises `TargetResponseUnavailableError`
for cancelled/failed/unknown outcomes or completion without any scorable message.
A completed tool-only response with canonical evidence reaches message scoring
without fabricating assistant text. Text-oriented scorers still require suitable
text; use `MessageToolCallScorer` for the tool evidence.

Manual chat handles that explicit outcome without inventing a processing-error
assistant message. The inline activity shows cancellation or tool-only completion,
retains the evidence, and permits continuation when the execution remains usable.
Neither a cancelled turn nor its partial text becomes a new scored final response.
Existing text scorers do not thereby evaluate tool behavior.
