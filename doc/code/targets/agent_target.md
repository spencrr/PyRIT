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
