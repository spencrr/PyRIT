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
even when its inputs came from initializers.

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
PyRIT -- ACP --> Copilot / OpenCode
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
filename. Copilot supports the existing wire APIs; OpenCode currently supports
Chat Completions. Custom ACP commands use harness-managed model access until a
provider-configuration adapter exists. This is separate from source-target
eligibility, which remains capability-driven.

### Harness and workspace selection

`HarnessProfile(harness_kind="opencode")` defaults to `("opencode", "acp")`.
`harness_kind="copilot"` defaults to the Copilot ACP command. Either allows an
explicit executable path. `harness_kind="acp"` requires a command and passes no
Copilot/OpenCode configuration. Older custom commands stay generic ACP; they
are not silently opted into a new adapter.

The harness owns provider configuration and private-home settings. The
environment owns process/container placement and filesystem preparation. For
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

OpenCode's inline configuration sets permission handling to `ask`, disables
automatic sharing/updates, and routes both main and small-model inference through
the bound target. PyRIT answers the permission requests it receives with its
configured policy. Workspace/agent configuration and plugins can change which
requests OpenCode raises; this is not an enforcement boundary or tool allowlist.
The current OpenCode adapter advertises a 128,000-token context and 8,192-token
output limit rather than discovering model limits from the source target. Verify
compatibility with smaller-limit models before evaluation.

Harness-managed access forwards only explicitly selected credential references;
no backend login is implicitly copied. Configure these under **Launch and
authentication** if needed. A private local workspace/home also does not prevent
the harness from discovering configuration in ancestor directories. Use a
results root outside source checkouts when inherited project configuration would
affect the test; local execution is not a sandbox.

### Authentication boundaries

The native tools-off `GitHubCopilotTarget` retains its existing precedence:
explicit `github_token`, then `GITHUB_TOKEN`, then SDK login discovery. For a
hosted CoPyRIT instance, that is the **backend runtime's** login context, not the
web operator's identity.

Managed ACP profiles instead forward only their explicit `credential_env`
references into the tested process/container. These are exposed to the agent and
its tools, and indirectly to users who can prompt that target. Values are resolved
at launch; they are not refreshed inside the environment. Docker environment
values are inspectable by Docker administrators while the container exists.

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

OpenCode **1.18.35** is the validation version. Its documented
[`opencode acp`](https://opencode.ai/docs/acp/) entry point and
[configuration precedence](https://opencode.ai/docs/config/) are used directly.
No claim is made that every ACP extension, model, or OpenCode version is supported.

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
remain target-owned; conflicting harness values fail explicitly. Inference uses
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

# OpenCode installed locally, or supply --executable with its full path.
uv run --extra agents python -m build_scripts.agent_byok_demo --harness opencode --results dbdata\opencode-local

# Image-owned starting workspace; no runtime fixture import.
docker build --tag pyrit-opencode-receipt:1.18.35 --file docker\agent_receipt\Dockerfile.opencode .
uv run --extra agents python -m build_scripts.agent_byok_demo --harness opencode --docker --results dbdata\opencode-docker

# Verify an explicit permission refusal, without requiring a fabricated text answer.
uv run --extra agents python -m build_scripts.agent_byok_demo --harness opencode --permission-policy deny --results dbdata\opencode-denied
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
  Held executions are also closed during restart reconciliation; restorable
  suspend/resume requires provider and harness reattachment support not implemented
  in this slice. Unresolved permissions from interrupted owners are cancelled.
- Live executions block configuration replacement, including idle chats.
- Cleanup uses exact process identities/container ownership labels. Failures
  remain visible; resource closure is not inferred from a disconnected pipe.
- After successful cleanup, private runtime workspace/home directories are
  removed. Explicitly selected, size-bounded artifacts and evidence remain.

Closed/expired conversations cannot be silently recreated. **Copy configuration to new
target**, then start a new conversation, creates fresh state on first send.
The copied profile uses the recorded image ID and fixture hash. A changed or
missing source fixture fails explicitly; keep versioned fixtures available.
This is not resume, rollback, or a snapshot of the previous agent. There are no used-session pools, restored checkpoint resume, operator takeover,
or orchestrator steering.

Evidence is stored beneath the configured memory results directory in
`agent_executions`, or the standalone manager's explicit directory. Records are
atomic files and events are append-only JSONL; this MVP evidence store is local,
not an Azure SQL execution index. Compute expiry does **not** delete evidence.
Operators manage evidence retention separately; protect that directory because
prompts, tool outputs, and artifacts may be sensitive.

## Evidence and outcome semantics

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
