# Testing an ACP agent

An agent target is the system under test: model binding + harness profile +
environment template. Saving configuration allocates nothing. Each new
conversation owns a fresh ACP session and local/Docker workspace, with ordered
durable evidence and explicit cancellation/cleanup. Local execution is not a sandbox.

Install `uv sync --extra agents`. Run the deterministic receipt smoke test with
`uv run --extra agents python -m build_scripts.agent_receipt_demo --scripted --results dbdata\agent-demo`.
The trusted verifier checks total31, second-turn36, and fresh31.

## Operator setup

Start `uv run --extra agents pyrit_backend --config-file docker\agent_receipt\pyrit.yaml --host 127.0.0.1 --port 8000`.
Configure credentials explicitly in the backend environment; the demo config
does not inherit shared environment or Key Vault sources. For trusted local
administration use the existing `PYRIT_ALLOW_UNAUTHENTICATED_ADMIN=true` opt-in.
From `frontend`, run `npm run dev -- --host localhost`.

Registry and New Chat use the same agent setup. Definitions are runtime-only;
initializers recreate persistent recipes. Administrators can inspect and close
executions. No API key values belong in saved profiles. Model targets and
compatible scenarios continue to use the existing normalizer/target contracts.

## Live evidence

Chat shows deterministic text/tool interleaving from the journal. Tool cards
remain anchored while their status changes. SSE uses normal HTTP authorization,
bounded subscriptions and durable cursors, reconnecting every60 seconds to
reauthorize. Observer disconnect does not cancel a run or extend resource lifetime.
Connection status distinguishes browser feed, ACP session and environment.
Provider event omissions do not prove a tool was not executed.

## Single-inference targets

Targets may implement `inference_capabilities` and `open_inference_async`.
This returns raw provider bytes/status/headers, not reconstructed chat text,
and does not execute tools or replay target history. OpenAI Chat/Responses
implementations reuse their authentication and HTTP transport. Inference has
bounded rate admission and no retries or redirects. Unsupported capabilities
and conflicting configured parameters fail explicitly.

## Target-backed BYOK

Select a capability-compatible registered target as the agent model source.
Copilot calls an execution-scoped authenticated relay, which invokes the bound
target; provider credentials never enter the tested environment. Binding identity
is pinned, budgets are bounded, and revocation cancels/joins in-flight requests.
Tool execution remains in the harness. Other harnesses require their own
provider-configuration adapter; they are not silently treated as Copilot.

Use `python -m build_scripts.agent_byok_demo --results dbdata\byok-demo` for
real Copilot against a synthetic authenticated provider (no live model billing).
Docker requires explicitly configured `PYRIT_INFERENCE_RELAY_HOST` and
`PYRIT_INFERENCE_RELAY_ADVERTISED_HOST`; restrict the inference-only listener to
trusted networks. Response protocol adapters are tested; actual model/harness
compatibility and live identity renewal require deployment verification.
