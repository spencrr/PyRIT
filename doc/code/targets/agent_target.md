# Testing an ACP agent

`AgentTarget` drives a real tool-capable harness through ACP. Model binding,
harness profile and environment template are immutable configuration. The first
send provisions one fresh process/session/workspace per conversation; later turns
retain that state. Local execution is explicitly not sandboxed. Docker contains
the harness and its tools, without mounting the operator's repository or home.

Install the optional dependencies with `uv sync --extra agents`.
Run the deterministic real-process receipt demo from the repository root:

```powershell
uv run --extra agents python -m build_scripts.agent_receipt_demo --scripted --results dbdata\agent-demo
```

The trusted verifier checks total31, a second-turn total36, then fresh31.
For real Copilot use `--local --credential-env COPILOT_GITHUB_TOKEN`, or build
`docker\agent_receipt\Dockerfile` as `pyrit-copilot-receipt:1.0.93` for Docker.
Never put token values in command arguments or saved profiles.

The manager owns startup, cancellation, expiry and exact-resource cleanup.
It records prepared input before dispatch and retains exposed ACP events and
selected artifacts. Capture failure is explicit. A closed conversation cannot
be recreated by replaying its side-effecting transcript. Restart reconciles owned
interrupted resources; it does not restore a checkpoint. Provider/internal event
coverage is not guaranteed by ACP. Scenarios requiring editable history are not
compatible. A standalone caller uses `async with AgentExecutionManager(...)`.

Explicit `TargetResponse` outcomes retain partial evidence without inventing a
completed/scorable answer; ordinary list-returning targets retain their behavior.
