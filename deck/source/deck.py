"""Deck content for the PyRIT agent-testing deck. build.py imports DECK and emits deck.json."""

from __future__ import annotations

REF = "agents/stack-08-agent-framework"
ASSETS = "https://raw.githubusercontent.com/spencrr/PyRIT/agents/stack-assets/diagrams/png"
PR = "https://github.com/spencrr/PyRIT/pull"


def ev(title, file=None, *, start=None, end=None, lines=None, focus=(), before=0, after=0, tag=None, tone=None, fixture=None):
    source = {"fixture": fixture} if fixture else {"ref": REF, "file": file}
    if start:
        source["start"] = start
    if end:
        source["end"] = end
    if lines:
        source["lines"] = lines
    if before:
        source["before"] = before
    if after:
        source["after"] = after
    source["focus"] = list(focus)
    out = {"title": title, "source": source}
    if tag:
        out["tag"] = tag
    if tone:
        out["tone"] = tone
    return out


def actor(id, label, x, y, w, *, tag=None, sub=None, slots=(), kind="card", tone=None, h=None, cols=None):
    a = {"id": id, "label": label, "x": x, "y": y, "w": w, "kind": kind}
    for k, v in (("tag", tag), ("sub", sub), ("tone", tone), ("h", h), ("cols", cols)):
        if v is not None:
            a[k] = v
    a["slots"] = [{"key": k, "label": lbl} for k, lbl in slots]
    return a


def edge(id, src, dst, **kw):
    return {"id": id, "from": src, "to": dst, **kw}


def card(id, title, takeaway, *, eyebrow=None, bullets=None, image=None, note=None, alt=None, autoplay=9000):
    c = {"id": id, "kind": "card", "title": title, "takeaway": takeaway, "autoplayMs": autoplay}
    for k, v in (("eyebrow", eyebrow), ("bullets", bullets), ("image", image), ("note", note), ("alt", alt)):
        if v is not None:
            c[k] = v
    return c


def packet(edge_id, label, tone="mint", count=1, **kw):
    return {"type": "packet", "edge": edge_id, "label": label, "tone": tone, "count": count, **kw}


def step(id, tab, title, body, *, status=None, slots=None, edges=None, camera=None, captions=None, animations=None, evidence=None, limit=None, review=None, autoplay=None):
    s = {"id": id, "kind": "step", "tab": tab, "title": title, "body": body, "patch": {}}
    if status:
        s["patch"]["status"] = status
    if slots:
        s["patch"]["slots"] = slots
    if edges:
        s["patch"]["edges"] = edges
    for k, v in (("camera", camera), ("captions", captions), ("animations", animations), ("evidence", evidence), ("limit", limit), ("review", review), ("autoplayMs", autoplay)):
        if v is not None:
            s[k] = v
    return s


LEGEND_DEFAULT = [
    {"swatch": "mint", "label": "PyRIT-owned · runtime, policy, journal, relay"},
    {"swatch": "amber", "label": "Agent-owned · harness, its frames, its effects"},
    {"swatch": "grey", "label": "Infrastructure · provider resources"},
    {"swatch": "red", "label": "Refused path"},
]

# ---------------------------------------------------------------------------------------------- chapter 1
CH1 = {
    "id": "turn", "index": 1, "short": "pipeline", "accent": "mint",
    "eyebrow": "Chapter 01 · one turn, end to end",
    "title": "One turn, end to end",
    "summary": "An attack sends one message; a real agent plans, calls a tool, answers; PyRIT returns what it did.",
    "takeaway": "The thing under test is a **harness** that plans, calls tools and changes state inside an environment PyRIT owns. Existing attacks, scorers and converters run against it **unchanged**.",
    "breadcrumb": ["attack", "target", "runtime", "environment", "relay"],
    "legend": LEGEND_DEFAULT,
    "stage": {
        "canvas": {"w": 2100, "h": 470},
        "actors": [
            actor("attack", "Attack", 0, 40, 250, tag="unchanged", sub="`PromptSendingAttack`, converters, scorers", slots=[("prompt", "message")]),
            actor("target", "AgentTarget", 320, 40, 280, tag="PromptTarget", slots=[("outcome", "outcome"), ("pieces", "returns")]),
            actor("memory", "Memory", 320, 290, 280, tag="pieces", slots=[("pieces", "stored"), ("scorer", "read by")]),
            actor("manager", "AgentExecutionManager", 670, 40, 300, tag="core · policy", slots=[("state", "state"), ("turn", "turn")]),
            actor("journal", "Journal", 670, 290, 300, tag="events.jsonl", slots=[("frames", "frames"), ("redact", "redaction")]),
            actor("env", "Execution environment", 1040, 20, 350, kind="panel", h=430, tag="provider · local / docker / yours"),
            actor("harness", "Harness", 1070, 90, 290, tag="copilot --acp --stdio", tone="amber", slots=[("session", "ACP"), ("tool", "tool call")]),
            actor("workspace", "Workspace", 1070, 290, 290, tag="template → filesystem", slots=[("effect", "effect")]),
            actor("relay", "Inference relay", 1460, 40, 290, tag="execution-scoped lease", slots=[("lease", "lease"), ("calls", "budget")]),
            actor("model", "Bound PyRIT target", 1820, 40, 270, tag="e.g. OpenAIChatTarget", slots=[("model", "model")]),
        ],
        "edges": [
            edge("send", "attack", "target"),
            edge("mgr", "target", "manager"),
            edge("mem", "target", "memory"),
            edge("launch", "manager", "harness"),
            edge("jrn", "manager", "journal"),
            edge("tool", "harness", "workspace"),
            edge("infer", "harness", "relay"),
            edge("bound", "relay", "model"),
        ],
        "initial": {"status": {}, "slots": {}, "edges": {}},
    },
    "steps": [
        card("intro", "One turn, <em>end to end</em>", "Follow a single message from an unchanged attack through a real agent and back. Everything on this stage is either **PyRIT-owned** (mint) or **agent-owned** (amber); the border between them is the point of the design."),
        step("send", "Send", "The attack does not know it is talking to an agent",
             "`AgentTarget` is one more `PromptTarget`. It accepts the current user message only and **never replays tool-producing history** to the harness: the agent owns its own session state.",
             status={"attack": "active", "target": "active"}, slots={"attack.prompt": "“Create the receipt”"}, edges={"send": "request"},
             captions=[{"label": "attack", "text": "Any existing attack, converter chain or scorer."}, {"label": "target", "text": "Validates user text; hands the message to the execution manager."}],
             animations=[packet("send", "user text")],
             evidence=ev("AgentTarget · send", "pyrit/prompt_target/agent_target.py", start=r"async def _send_prompt_to_target_async", lines=22, focus=[r"never replay", r"has_history", r"interactive="], tag="PR #17")),
        step("allocate", "Allocate", "First message: an execution is allocated and launched",
             "The manager persists a record, asks the **environment provider** for a session, reserves deterministic resource identifiers, then launches the harness command with a private home. State moves `starting → idle`. Every later message in this conversation reuses the same execution.",
             status={"attack": "idle", "target": "ok", "manager": "active", "env": "ok", "harness": "active", "workspace": "ok", "journal": "active"},
             slots={"manager.state": "starting → idle", "manager.turn": "1", "journal.frames": "6", "harness.session": "initialize · session/new", "workspace.effect": "seeded from template"},
             edges={"send": "dim", "mgr": "request", "launch": "request", "jrn": "request"},
             captions=[{"label": "runtime", "text": "`record.json` is written before any resource exists, so a crash is reconcilable."}, {"label": "environment", "text": "`reserve` → `inference_endpoint_async` → `launch_async`; the provider owns the resources."}],
             animations=[packet("mgr", "send_async"), {"type": "draw", "edge": "launch", "delayMs": 700}, {"type": "counter", "slot": "journal.frames", "from": 0, "to": 6, "delayMs": 900}],
             evidence=ev("ExecutionSession · lifecycle", "pyrit/agent/environment.py", start=r"def reserve\(self\)", end=r"async def close_async", after=1, focus=[r"def reserve", r"async def inference_endpoint_async", r"async def launch_async", r"async def close_async"], tag="PR #17")),
        step("prompt", "Prompt", "Every ACP frame is journaled before it is sent or acted on",
             "`session/prompt` goes out; the harness streams `session/update` notifications back: a `tool_call`, its `tool_call_update` with the result, an `agent_message_chunk`. The transport records **intent before writing** and retains every valid incoming frame, including extensions it does not understand.",
             status={"manager": "active", "harness": "active", "workspace": "active", "journal": "active"},
             slots={"manager.state": "working", "harness.session": "session/prompt → session/update ×3", "harness.tool": "read_orders({\"path\": \"orders.json\"})", "workspace.effect": "orders.json read", "journal.frames": "11", "journal.redact": "credential values → ***"},
             edges={"launch": "request", "tool": "response", "jrn": "request"},
             captions=[{"label": "harness", "text": "Plans, calls its tool, streams what it is doing. PyRIT does not interpret, it records."}, {"label": "journal", "text": "Append-only `events.jsonl`; the flushed journal is the commit point."}],
             animations=[packet("launch", "session/prompt"), packet("tool", ["tool_call", "tool_call_update"], tone="amber", count=2, delayMs=1200, gapMs=700), {"type": "counter", "slot": "journal.frames", "from": 6, "to": 11, "delayMs": 1200, "durationMs": 1800}],
             evidence=ev("RecordingTransport · send", "pyrit/agent/acp_connection.py", start=r"async def send\(self, message", lines=8, focus=[r"Record intent before writing", r'await self.record\("outgoing"'], tag="PR #17")),
        step("inference", "Inference", "The model behind the agent is a PyRIT target",
             "When the harness wants a completion it calls what it believes is an OpenAI endpoint. That endpoint is PyRIT's **inference relay**: an execution-scoped bearer lease, a per-execution call budget, and a forward to whichever registered PyRIT target the operator bound. The provider credential never enters the environment.",
             status={"manager": "idle", "harness": "active", "workspace": "idle", "relay": "active", "model": "active", "journal": "active"},
             slots={"relay.lease": "sha256(Bearer token) → lease", "relay.calls": "1 / 100", "model.model": "whatever the bound target is", "journal.frames": "+ inference.started/finished"},
             edges={"infer": "response", "bound": "request", "tool": "dim", "launch": "dim"}, camera={"x": 600, "crumbs": [2, 3, 4]},
             captions=[{"label": "harness → relay", "text": "`POST /v1/chat/completions` with the execution token. Path, model and body shape are policed."}, {"label": "relay → target", "text": "Single-inference capability of the bound target: transport + auth, no history, no tool loop."}],
             animations=[packet("infer", "POST /v1/chat/completions", tone="amber"), packet("bound", "send_prompt_async", delayMs=1600)],
             evidence=ev("Binding a lease to an execution", "pyrit/agent/execution_manager.py", start=r"lease = InferenceLease\(", before=0, lines=12, focus=[r"max_calls=live.record.profile.max_inference_requests", r"live.secrets = \(\*live.secrets, lease.token\)", r"inference_target_hash"], tag="PR #20"),
             limit="Budgets are per execution lifetime (default 100, max 1,000); per-turn accounting is deferred."),
        step("project", "Project", "What the agent did becomes pieces PyRIT already understands",
             "The turn finishes. From the journaled frames of this turn, `project_agent_tools` emits `function_call` / `function_call_output` pieces, each carrying provenance back to the exact event sequence. Nothing is inferred from the agent's prose.",
             status={"target": "active", "memory": "active", "manager": "ok", "harness": "ok", "relay": "idle", "model": "idle", "journal": "ok"},
             slots={"manager.state": "idle", "manager.turn": "1 · completed", "target.outcome": "completed", "target.pieces": "text + 2 tool pieces", "memory.pieces": "function_call + output", "journal.frames": "12"},
             edges={"mem": "request", "infer": "dim", "bound": "dim", "mgr": "dim"}, camera={"x": 0, "crumbs": [1, 2]},
             captions=[{"label": "projection", "text": "Explicit name, object arguments, granted permission, established result; otherwise omitted **with a reason**."}, {"label": "memory", "text": "Same piece types `MessageToolCallScorer` reads today."}],
             animations=[packet("mem", "pieces", count=2, gapMs=500)],
             evidence=ev("Captured projection · turn 1", fixture="projection.json", start=r'"pieces"', lines=16, focus=[r'"type": "function_call"', r'"name": "read_orders"', r'"total": 31'], tag="fixture", tone="mint")),
        step("return", "Return", "An explicit outcome, not an empty string",
             "`TargetResponse` carries `outcome=completed` plus execution and turn identifiers in metadata. A turn that produced tool effects but no assistant text is still a **completed** turn, which is why the stack starts with explicit target outcomes (PR 1).",
             status={"attack": "active", "target": "ok", "memory": "ok", "manager": "idle", "harness": "idle"},
             slots={"attack.prompt": "scored: “Receipt total: 31”", "memory.scorer": "MessageToolCallScorer"},
             edges={"send": "request", "mem": "dim"},
             captions=[{"label": "outcome", "text": "`completed` · `refused` · `failed` … every target declares one; agents just made it unavoidable."}, {"label": "scoring", "text": "Only completed assistant/tool pieces are auto-scored in Co-PyRIT."}],
             animations=[packet("send", "TargetResponse(completed)", reverse=True)],
             evidence=ev("AgentTarget · return", "pyrit/prompt_target/agent_target.py", start=r'metadata: dict\[str, Any\] = \{', lines=22, focus=[r'"agent_execution_id"', r"OUTCOME_METADATA_KEY", r"return TargetResponse"], tag="PR #17")),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 2
CH2 = {
    "id": "seam", "index": 2, "short": "topology", "accent": "mint",
    "eyebrow": "Chapter 02 · the environment seam",
    "title": "Core owns policy, providers own resources",
    "summary": "Two executable contracts separate PyRIT's runtime from wherever the agent runs: local, Docker, or your own sandbox.",
    "takeaway": "PyRIT does **not** own environments. It owns **policy**: timeouts, budgets, cancellation, approvals, redaction and the ACP protocol. A provider owns **resources**: allocation, release, workspace seeding and the byte streams. The seam between them is a contract PyRIT can verify.",
    "breadcrumb": ["core", "seam", "providers"],
    "legend": [{"swatch": "mint", "label": "Core · policy"}, {"swatch": "grey", "label": "Provider · resources"}, {"swatch": "amber", "label": "Harness · runs inside"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("core", "Core · policy", 0, 20, 430, kind="panel", h=430, tag="pyrit.agent"),
            actor("manager", "AgentExecutionManager", 20, 80, 390, cols=2, slots=[("timeouts", "timeouts"), ("budgets", "budgets"), ("cancel", "cancellation"), ("approvals", "approvals"), ("redact", "redaction"), ("acp", "protocol")]),
            actor("envseam", "ExecutionEnvironmentProvider", 500, 40, 360, tag="ABC", sub="`info` · `validate_template` · `create_session`"),
            actor("session", "ExecutionSession", 500, 260, 360, tag="ABC", sub="`reserve` → `inference_endpoint_async` → `launch_async` → `close_async`"),
            actor("providers", "Providers · resources", 920, 20, 580, kind="panel", h=430, tag="registered by setup, never from the UI"),
            actor("local", "local", 950, 80, 250, tag="first-party", sub="subprocess, private home; **not** a sandbox", slots=[("gives", "gives")]),
            actor("docker", "docker", 1230, 80, 250, tag="reference isolated", sub="container per execution", slots=[("gives", "gives")]),
            actor("aca", "ACA sandbox", 950, 300, 250, tag="out-of-tree", sub="built against the same contract · zero core changes"),
            actor("conformance", "Conformance suite", 1230, 300, 250, tag="executable", sub="`verify_environment_provider_async`"),
        ],
        "edges": [
            edge("c_env", "manager", "envseam", offset=-30),
            edge("c_sess", "manager", "session", offset=30),
            edge("env_local", "envseam", "local", offset=-10),
            edge("env_docker", "envseam", "docker", offset=10),
            edge("env_aca", "envseam", "aca"),
            edge("conf", "conformance", "session"),
        ],
        "initial": {},
    },
    "steps": [
        card("intro", "The environment <em>seam</em>", "Where the agent runs is a **provider decision**. Which rules apply is a **PyRIT decision**. Chapter 02 walks the contract between them and the three providers that exercise it: `local`, `docker`, and an out-of-tree cloud sandbox."),
        step("core", "Core", "Everything that is policy lives in core, once",
             "Timeouts, inference budgets, cancellation grace, permission policy, credential redaction and the ACP client are implemented exactly once, in `pyrit.agent`. A provider cannot weaken them, because it never sees them.",
             status={"core": "active", "manager": "active"},
             slots={"manager.timeouts": "startup · turn · idle · lifetime", "manager.budgets": "max_inference_requests", "manager.cancel": "session/cancel + grace", "manager.approvals": "deny · allow_once · ask", "manager.redact": "every journal frame", "manager.acp": "initialize · prompt · cancel"},
             captions=[{"label": "core", "text": "Trusted code that ships with PyRIT."}, {"label": "why", "text": "A sandbox vendor should not be able to turn off redaction by accident."}],
             evidence=ev("Provider contract", "pyrit/agent/environment.py", start=r"class ExecutionEnvironmentProvider\(ABC\)", lines=26, focus=[r"Trusted implementation code", r"def validate_template", r"def create_session"], tag="PR #17")),
        step("session", "Session", "A provider owns exactly four moments",
             "`reserve` records deterministic resource identifiers before anything exists, so a crash mid-allocation is reconcilable. `inference_endpoint_async` translates the relay URL into the environment's network namespace **without acquiring resources**. `launch_async` seeds the workspace and starts the harness. `close_async` releases only what it owns and is idempotent.",
             status={"core": "ok", "manager": "ok", "envseam": "active", "session": "active"},
             edges={"c_env": "request", "c_sess": "request"},
             animations=[{"type": "draw", "edge": "c_env"}, {"type": "draw", "edge": "c_sess", "delayMs": 400}],
             captions=[{"label": "streams", "text": "`acp_reader`, `acp_writer`, `stderr` raise `RuntimeError` before a successful launch."}, {"label": "secrets", "text": "Only variable **names** travel on command lines; values travel through the provider's own channel."}],
             evidence=ev("ExecutionSession · four moments", "pyrit/agent/environment.py", start=r"def reserve\(self\)", end=r"async def close_async", after=1, focus=[r"Record deterministic resource identifiers", r"This must not acquire resources", r"Prepare the workspace, acquire resources", r"Release exactly the owned resources"], tag="PR #17")),
        step("local", "Local", "`local`: the smallest honest provider",
             "A subprocess with a fresh empty workspace and a private home, running as the backend's own OS user. It is **not** a sandbox and says so: creating a local target requires an explicit acknowledgement. It is what development and CI use, and it proves the seam needs no container to be meaningful.",
             status={"envseam": "ok", "session": "ok", "providers": "ok", "local": "active"},
             slots={"local.gives": "process tree · workspace dir · artifacts"},
             edges={"env_local": "request"},
             animations=[{"type": "draw", "edge": "env_local"}],
             captions=[{"label": "local", "text": "`LocalSession(SubprocessSession)`; stop the owned process tree, keep requested artifacts, remove runtime directories."}],
             evidence=ev("LocalSession", "pyrit/agent/environments/local.py", start=r"class LocalSession", end=r"async def close_async", after=1, focus=[r"not a sandbox|private home", r"async def close_async"], tag="PR #17")),
        step("docker", "Docker", "`docker`: the reference isolated provider",
             "One container per execution, labelled with the execution and owner identifiers. Capabilities dropped, `no-new-privileges`, a pid limit, memory and CPU limits, and the network the template chose. Only variable names appear on the `docker create` command line; values travel through the docker CLI's own environment.",
             status={"local": "ok", "docker": "active"},
             slots={"docker.gives": "container · image provenance · network"},
             edges={"env_docker": "request"},
             animations=[{"type": "draw", "edge": "env_docker"}],
             captions=[{"label": "provenance", "text": "The resolved image id is recorded on the execution, not just the tag."}, {"label": "template", "text": "`EnvironmentTemplate` is a reusable starting filesystem and access policy, never a running environment."}],
             evidence=ev("docker create · flags", "pyrit/agent/environments/docker.py", start=r"args = \[", end=r'"--network",', after=1, focus=[r"pyrit.execution=", r"pyrit.owner=", r"--cap-drop=ALL", r"--security-opt=no-new-privileges", r"--pids-limit"], tag="PR #18")),
        step("ownership", "Ownership", "Teardown refuses anything it did not create",
             "Before stopping a container, the provider inspects it and compares the `pyrit.execution` and `pyrit.owner` labels with the record. A name collision with someone else's container is an error, not a cleanup.",
             status={"docker": "active"},
             captions=[{"label": "interrupted runs", "text": "On restart, `_reconcile_interrupted_async` recreates the session from the record and releases it through the same path."}],
             evidence=ev("Ownership check before stop", "pyrit/agent/environments/docker.py", start=r"async def _inspect_owned_container_async", lines=14, focus=[r'labels.get\("pyrit.execution"\)', r'labels.get\("pyrit.owner"\)', r"Refusing to stop"], tag="PR #18")),
        step("conformance", "Conformance", "The contract is executable",
             "`verify_environment_provider_async` drives one real allocation through the provider with a byte-echo command and reports one check per promise: streams unavailable before launch, relay translation, a secret that must not leak, idempotent release, honest capabilities. The ACA sandbox provider was built against this suite **outside the repository with zero core changes**.",
             status={"docker": "ok", "conformance": "active", "aca": "active", "session": "active"},
             edges={"conf": "request", "env_aca": "request"},
             animations=[{"type": "draw", "edge": "conf"}, {"type": "draw", "edge": "env_aca", "delayMs": 500}],
             captions=[{"label": "for maintainers", "text": "A provider PR is reviewed against the report, not against a reading of the ABC."}, {"label": "for vendors", "text": "Nothing here speaks ACP; a provider only has to move bytes and honour the four moments."}],
             evidence=ev("verify_environment_provider_async", "pyrit/agent/conformance.py", start=r"async def verify_environment_provider_async", lines=20, focus=[r"Drive one real allocation", r"resources, streams, secrets", r"One check per promise"], tag="PR #17"),
             review="Review a provider by running its conformance report in CI; the Docker and local providers already do."),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 3
CH3 = {
    "id": "journal", "index": 3, "short": "panel", "accent": "mint",
    "eyebrow": "Chapter 03 · journal → pieces",
    "title": "Recording what the agent did",
    "summary": "An append-only journal of every frame, and a projection that never guesses.",
    "takeaway": "PyRIT records what the agent **did**, not only what it **said**. The journal is the ground truth; the projection into memory pieces is explicit, provenance-carrying, and silent about anything it cannot establish.",
    "breadcrumb": ["journal", "projection", "memory"],
    "legend": [{"swatch": "amber", "label": "Agent frames · as received"}, {"swatch": "mint", "label": "Projection · PyRIT-owned rules"}, {"swatch": "red", "label": "Omitted with a reason"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("journal", "Journal · events.jsonl", 0, 20, 470, tag="append-only · redacted", cols=1, slots=[("e7", "seq 7 · outgoing"), ("e8", "seq 8 · incoming"), ("e9", "seq 9 · incoming"), ("e10", "seq 10 · incoming"), ("e11", "seq 11 · incoming"), ("e12", "seq 12 · lifecycle")]),
            actor("proj", "project_agent_tools", 540, 20, 420, tag="core · PR #19", cols=1, slots=[("r1", "named"), ("r2", "arguments"), ("r3", "permission"), ("r4", "call ↔ result")]),
            actor("pieces", "Memory pieces", 1020, 20, 480, tag="function_call · function_call_output", cols=1, slots=[("call", "function_call"), ("out", "function_call_output"), ("prov", "provenance")]),
            actor("omit", "Omitted", 1020, 300, 480, tag="never guessed", cols=1, slots=[("reason", "reason")]),
        ],
        "edges": [edge("jp", "journal", "proj"), edge("pp", "proj", "pieces"), edge("po", "proj", "omit")],
        "initial": {},
    },
    "steps": [
        card("intro", "Journal → <em>pieces</em>", "The faithfulness of tool records is the review that deserves undivided attention. A false `function_call_output` is **false evidence**. This chapter is why PR 2c is its own PR."),
        step("frames", "Frames", "Every frame, both directions, plus lifecycle and inference",
             "Directions are `outgoing`, `incoming`, `lifecycle`, `stderr` and `inference`. Writes are appended under a lock after redaction of every credential value the execution knows about, including the relay token. The page API reads by sequence, so a browser can resume exactly where it left off.",
             status={"journal": "active"},
             slots={"journal.e7": "session/prompt  “Create the receipt”", "journal.e8": "session/update · tool_call read_orders", "journal.e9": "session/update · tool_call_update {\"total\": 31}", "journal.e10": "session/update · agent_message_chunk", "journal.e11": "result · stopReason end_turn", "journal.e12": "turn_finished · completed"},
             captions=[{"label": "fixture", "text": "These six are sequences 7–12 of a real capture made with the scripted ACP agent in this repository."}],
             evidence={**ev("Captured journal · sequence 8", fixture="events.jsonl", focus=[r'"sessionUpdate": "tool_call"', r'"name": "read_orders"', r'"path": "orders.json"'], tag="fixture", tone="amber"), "source": {"fixture": "events.jsonl", "sequence": 8, "lines": 17, "focus": [r'"sessionUpdate": "tool_call"', r'"name": "read_orders"', r'"path": "orders.json"']}}),
        step("explicit", "Explicit", "Project explicit calls; never infer from claims",
             "The projector walks one turn's events in order and keeps a call only when the agent **named** the tool, reported **object** arguments, was **granted** permission (or never needed it), and both invocation and result are **established** by frames. Prose like “I ran the tests” is not a tool call.",
             status={"journal": "ok", "proj": "active"},
             edges={"jp": "response"},
             animations=[packet("jp", ["tool_call", "tool_call_update", "permission.resolved"], tone="amber", count=3, gapMs=550)],
             captions=[{"label": "input", "text": "Events of exactly one execution turn, strictly ordered; anything else is a `ValueError`."}],
             evidence=ev("project_agent_tools", "pyrit/agent/tool_calls.py", start=r"def project_agent_tools\(", lines=12, focus=[r"without guessing tool names or invocation from claims", r"retaining call/event provenance"], tag="PR #19")),
        step("rules", "Rules", "Four conditions, each visible as a slot",
             "Each condition is checked from frames, not from the model's text. When all four hold, two pieces are emitted. When any fails, the call is **omitted** and the reason is kept on the projection, so a reviewer can tell “no tools” from “tools we could not trust”.",
             status={"proj": "active"},
             slots={"proj.r1": "✓ name reported", "proj.r2": "✓ arguments are an object", "proj.r3": "✓ no permission denied", "proj.r4": "✓ invocation and result established"},
             captions=[{"label": "ambiguity", "text": "Two call reports with the same id and different names → `ambiguous_call_reports`."}],
             evidence=ev("Omission reasons", "pyrit/agent/tool_calls.py", start=r'"ambiguous_call_reports"', before=5, end=r"invocation_or_result_not_established", focus=[r"ambiguous_call_reports", r"permission_not_granted", r"name_not_reported", r"arguments_not_reported_as_object", r"invocation_or_result_not_established"], tag="PR #19")),
        step("provenance", "Provenance", "Provenance travels with the piece",
             "Each emitted piece carries `agent_tool_call` metadata: the source protocol, execution id, turn id, the agent's own call id and the **event sequence** it was established from. From any score you can open the exact journal frame.",
             status={"proj": "ok", "pieces": "active"},
             slots={"pieces.call": "read_orders({\"path\": \"orders.json\"})", "pieces.out": "{\"total\": 31}", "pieces.prov": "acp · seq 8 · turn 132096dd… · call “receipt”"},
             edges={"pp": "request", "jp": "dim"},
             animations=[packet("pp", ["function_call", "function_call_output"], count=2, gapMs=600)],
             captions=[{"label": "call id", "text": "`<execution>:<turn>:<agent call id>` so ids never collide across executions."}],
             evidence=ev("Captured projection · provenance", fixture="projection.json", start=r'"metadata"', lines=12, focus=[r'"source": "acp"', r'"source_call_id": "receipt"', r'"event_sequence": 8'], tag="fixture", tone="mint")),
        step("omitted", "Omitted", "Silence is explicit too",
             "The projection has an `omitted` map keyed by the agent's call id with one of the five reasons. `AgentTarget` puts the projection summary on the response metadata, so a scorer or a human can see that something was attempted but not established.",
             status={"pieces": "ok", "omit": "active"},
             slots={"omit.reason": "permission_not_granted (denied by policy) · name_not_reported · …"},
             edges={"po": "blocked", "pp": "dim"},
             captions=[{"label": "deny by default", "text": "With the default `deny` policy, a Copilot `tool_call` that asked permission shows up here, not as a result."}],
             evidence=ev("Omitted, with reason", "pyrit/agent/tool_calls.py", start=r"projection.omitted\[source_id\] = ", before=3, lines=6, focus=[r"projection.omitted"], tag="PR #19")),
        step("scorers", "Scorers", "Scorers already read this",
             "`MessageToolCallScorer` and friends consume `function_call` pieces today. Co-PyRIT's auto-scoring picks the pieces that are **scorable**: assistant or tool role, not a request trace, and a `completed` outcome. No new scorer type was needed.",
             status={"omit": "idle", "pieces": "active", "journal": "ok"},
             captions=[{"label": "terminology", "text": "“Tool calls”, matching the piece types; not “evidence”, which upstream now uses for findings."}],
             evidence=ev("What gets auto-scored", "pyrit/backend/services/message_send_service.py", start=r"def _is_scorable_response", lines=7, focus=[r'piece.role in \("assistant", "tool"\)', r"TargetOutcome.COMPLETED"], tag="PR #19")),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 4
CH4 = {
    "id": "relay", "index": 4, "short": "pipeline", "accent": "mint",
    "eyebrow": "Chapter 04 · the inference relay",
    "title": "Bring your own model, keep your own key",
    "summary": "The harness thinks it is talking to OpenAI. It is talking to a lease-scoped relay in front of a PyRIT target.",
    "takeaway": "The model behind the agent is **a PyRIT target**. The harness receives an execution-scoped token and a local base URL; the relay validates every request against the binding and forwards it with the single-inference capability. The provider credential stays on the server. Model and harness become **independently testable**.",
    "breadcrumb": ["harness", "relay", "binding", "provider"],
    "legend": [{"swatch": "amber", "label": "Harness request · untrusted"}, {"swatch": "mint", "label": "Relay · policy + forward"}, {"swatch": "red", "label": "Direct path · refused"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("env", "Execution environment", 0, 20, 330, kind="panel", h=430, tag="no provider credential inside"),
            actor("harness", "Harness", 25, 80, 280, tone="amber", tag="believes: OpenAI", cols=1, slots=[("base", "base url"), ("key", "api key"), ("model", "model")]),
            actor("relay", "Inference relay", 420, 40, 340, tag="127.0.0.1 · PyRIT backend", cols=1, slots=[("lease", "lease"), ("budget", "budget"), ("check", "body")]),
            actor("binding", "ResolvedModelBinding", 820, 40, 320, tag="which PyRIT target answers", slots=[("target", "target"), ("wire", "wire api")]),
            actor("provider", "Model provider", 1200, 40, 300, tag="credential stays here", slots=[("cred", "credential")]),
            actor("journal", "Journal · inference", 420, 300, 340, tag="direction = inference", cols=1, slots=[("ev", "events"), ("content", "content")]),
        ],
        "edges": [
            edge("hr", "harness", "relay"),
            edge("rb", "relay", "binding"),
            edge("bp", "binding", "provider"),
            edge("rj", "relay", "journal"),
            edge("direct", "harness", "provider", points=[[165, 395], [165, 445], [1350, 445], [1350, 165]], label="no direct path"),
        ],
        "initial": {},
    },
    "steps": [
        card("intro", "Bring your own <em>model</em>", "An agent is a harness **and** a model. To test either one you must be able to hold the other fixed. PR 4 makes the model a PyRIT target behind a relay, which is also how no provider credential ever enters an environment."),
        step("believes", "Believes", "The harness is configured, not modified",
             "Each harness adapter knows how its harness selects a provider. For Copilot CLI that is `COPILOT_PROVIDER_BASE_URL`, `_TYPE=openai`, `_WIRE_API`, `_API_KEY` and `COPILOT_MODEL`; for the Agent Framework host it is the `OPENAI_*` family. The values are the relay URL translated by the environment and the lease token.",
             status={"env": "ok", "harness": "active"},
             slots={"harness.base": "http://127.0.0.1:<relay>/v1  (or host.docker.internal)", "harness.key": "Bearer <execution token>", "harness.model": "the bound target's model"},
             captions=[{"label": "reserved", "text": "Those variable names are **reserved**: a profile cannot set them, so a template cannot point the harness elsewhere."}],
             evidence=ev("Copilot adapter · inference environment", "pyrit/agent/harness.py", start=r'"COPILOT_PROVIDER_BASE_URL": base_url', before=9, lines=16, focus=[r'"COPILOT_PROVIDER_BASE_URL"', r'"COPILOT_PROVIDER_API_KEY": token', r'"COPILOT_MODEL": model'], tag="PR #20")),
        step("lease", "Lease", "A lease, not a key",
             "The relay hashes the bearer value and looks up an **active lease**. No lease → 401. Over budget or more than four concurrent requests → 429. The token is also added to the execution's redaction set, so it never appears in the journal in clear.",
             status={"harness": "active", "relay": "active"},
             slots={"relay.lease": "sha256(token) → InferenceLease(active)", "relay.budget": "calls 1 / 100 · concurrency ≤ 4"},
             edges={"hr": "response"},
             animations=[packet("hr", "POST /v1/chat/completions", tone="amber")],
             captions=[{"label": "revocation", "text": "`revoke_async` cancels in-flight tasks when the execution closes or is cancelled."}],
             evidence=ev("InferenceRelay · handle", "pyrit/agent/inference_relay.py", start=r"async def _handle_async", lines=11, focus=[r"hashlib.sha256", r"status_code=401", r"status_code=429"], tag="PR #20")),
        step("policed", "Policed", "The body is policed against the binding",
             "Path must match the bound wire API; `model` must equal the binding; routing and auth overrides are refused; server-side continuation is refused; tools must be harness-executed function declarations; provider file references are refused. The harness gets exactly the model it was bound to, nothing adjacent.",
             status={"relay": "active"},
             slots={"relay.check": "path ✓ · model ✓ · no overrides ✓ · no continuation ✓ · tools: function only"},
             captions=[{"label": "why so strict", "text": "A compromised or merely creative harness must not be able to use PyRIT's credential as a general proxy."}],
             evidence=ev("InferenceRelay · validate body", "pyrit/agent/inference_relay.py", start=r"def _validate_body", lines=20, focus=[r"Model does not match this execution binding", r"Provider routing and authentication overrides", r"Only harness-executed tool declarations"], tag="PR #20"),
             limit="Per-request modality enforcement (rejecting undeclared `image_url` / `input_image` parts) is deferred; the binding declares input modalities."),
        step("forward", "Forward", "Forwarded to a PyRIT target with the single-inference capability",
             "PR 3 gives targets a **single-inference** capability: transport and auth without history or a tool loop. The relay uses it, streaming the provider's response back to the harness. The binding records the target's identifier hash and protocol on the execution, so a result always knows which model answered.",
             status={"relay": "ok", "binding": "active", "provider": "active"},
             slots={"binding.target": "OpenAIChatTarget · AzureOpenAI · Ollama · …", "binding.wire": "chat_completions | responses", "provider.cred": "server-held, never forwarded"},
             edges={"rb": "request", "bp": "request", "direct": "blocked", "hr": "dim"},
             animations=[packet("rb", "single inference"), packet("bp", "provider call", delayMs=1500)],
             captions=[{"label": "blocked", "text": "A harness profile that names a provider credential is refused by BYOK (`native_model_credentials`)."}],
             evidence=ev("Binding recorded on the execution", "pyrit/agent/execution_manager.py", start=r"self._relay.add\(lease\)", lines=10, focus=[r"inference_target_hash = binding.identifier_hash", r"inference_protocol = binding.requirements.wire_api"], tag="PR #20")),
        step("captured", "Captured", "Captured both ways, bounded and redacted",
             "Every relayed request is journaled as `inference.started` → `inference.response` → `inference.finished` with byte counts and outcome. Content capture is **optional**; when on, decoded response text is journaled after the stream settles so a credential spanning transport chunks is redacted as a whole.",
             status={"binding": "ok", "provider": "ok", "journal": "active", "relay": "active"},
             slots={"journal.ev": "inference.started → response → finished", "journal.content": "capture_inference_content · redacted whole"},
             edges={"rj": "request", "rb": "dim", "bp": "dim"},
             animations=[packet("rj", "inference.*", count=3, gapMs=450)],
             captions=[{"label": "correlation", "text": "`turn_correlation: active_execution_turn_window`: inference events are attributed to the turn in flight."}],
             evidence=ev("inference.started record", "pyrit/agent/inference_relay.py", start=r'"type": "inference.started"', before=1, lines=11, focus=[r'"inference.started"', r'"target_hash"', r"if lease.capture_content"], tag="PR #20")),
        step("independent", "Independent", "Model and harness, tested independently",
             "Hold the harness and swap the bound target: is the regression in the model? Hold the model and swap the harness (Copilot CLI, an Agent Framework agent, any ACP agent): is it in the scaffolding? Both are one configuration change, and the binding appears in every result's identity.",
             status={"harness": "active", "binding": "active", "relay": "ok", "journal": "ok"},
             slots={"binding.target": "swap here → same harness, different model"},
             edges={"hr": "response", "rb": "request", "bp": "request"},
             captions=[{"label": "native credentials", "text": "A harness may still use its own login (e.g. `COPILOT_GITHUB_TOKEN`) when no model is bound; then PyRIT records that no binding exists."}],
             evidence=ev("inference_environment · per harness kind", "pyrit/agent/harness.py", start=r"^def inference_environment\(", lines=22, focus=[r"kind: str", r"wire_api: InferenceWireApi", r"token: str"], tag="PR #20")),
    ],
}

# ---------------------------------------------------------------------------------------------- zoom out
ZOOM = {
    "id": "zoomout", "index": None, "accent": "mint", "eyebrow": "Half-time · where we are", "title": "Zoom out",
    "steps": [
        card("arch", "Four chapters in, <em>one picture</em>",
             "**Core owns policy, providers own resources.** Two seams, `HarnessAdapter` and `ExecutionEnvironmentProvider`, each with an executable conformance suite. The harness reaches the model only through the execution-scoped relay. Everything you have seen so far is on this diagram; the next three chapters are about people and configuration.",
             eyebrow="Half-time · runtime architecture", image=f"{ASSETS}/runtime-architecture.png", alt="Runtime architecture diagram", note="Diagram source and PNG live on the `agents/stack-assets` branch; the gist embeds the same image.", autoplay=12000),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 5
CH5 = {
    "id": "recipes", "index": 5, "short": "panel", "accent": "mint",
    "eyebrow": "Chapter 05 · recipes and identity",
    "title": "A target is a recipe, not a resource",
    "summary": "Model binding + harness profile + environment template → a configured target whose identity is the hash of the recipe.",
    "takeaway": "A configured `AgentTarget` **owns no resources**. Its identity is its configuration without defaults, timeouts and evidence budgets. Preset names are **provenance**, not identity. `permission_policy` **is** identity, and it defaults to deny.",
    "breadcrumb": ["parts", "recipe", "identity", "gates"],
    "legend": [{"swatch": "mint", "label": "Configuration · identity"}, {"swatch": "grey", "label": "Provenance · not identity"}, {"swatch": "amber", "label": "Agent runs later, per conversation"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("binding", "Model binding", 0, 30, 340, tag="which model answers", slots=[("target", "target"), ("wire", "wire api")]),
            actor("hprofile", "Harness profile", 0, 260, 340, tag="which agent, how launched", slots=[("kind", "kind"), ("cmd", "command")]),
            actor("template", "Environment template", 400, 30, 340, tag="where it runs", slots=[("provider", "provider"), ("image", "image")]),
            actor("policy", "Policy", 400, 260, 340, tag="part of the recipe", slots=[("perm", "permission"), ("timeouts", "timeouts")]),
            actor("config", "AgentTargetConfiguration", 820, 30, 340, tag="owns no resources", cols=1, slots=[("identity", "identity"), ("prov", "provenance")]),
            actor("init", "AgentTargetInitializer", 820, 300, 340, tag="YAML · setup", slots=[("comp", "composes")]),
            actor("target", "AgentTarget", 1220, 30, 280, tag="PromptTarget", cols=1, slots=[("exec", "executions"), ("admin", "over REST")]),
        ],
        "edges": [
            edge("b_c", "binding", "config", offset=-30),
            edge("h_c", "hprofile", "config", offset=-10),
            edge("t_c", "template", "config", offset=10),
            edge("p_c", "policy", "config", offset=30),
            edge("i_c", "init", "config"),
            edge("c_t", "config", "target"),
        ],
        "initial": {},
    },
    "steps": [
        card("intro", "Recipes and <em>identity</em>", "Operators compose agents from three reusable parts. PyRIT must know when two results came from the **same** system under test, so identity has to be exact: no names, no timeouts, no defaults."),
        step("parts", "Parts", "Three parts, one recipe",
             "**Model binding**: which registered PyRIT target answers inference, over which wire API. **Harness profile**: which agent kind, its command and declared credentials. **Environment template**: which provider, which image or workspace base, which network. Each is reusable and registered by setup, never fetched from the UI as import strings.",
             status={"binding": "active", "hprofile": "active", "template": "active"},
             slots={"binding.target": "openai_chat  (registry name)", "binding.wire": "chat_completions", "hprofile.kind": "copilot", "hprofile.cmd": "copilot --acp --stdio", "template.provider": "docker", "template.image": "pyrit/agent-receipt (resolved id recorded)"},
             edges={"b_c": "request", "h_c": "request", "t_c": "request"},
             animations=[{"type": "draw", "edge": "b_c"}, {"type": "draw", "edge": "h_c", "delayMs": 300}, {"type": "draw", "edge": "t_c", "delayMs": 600}],
             captions=[{"label": "composition", "text": "`AgentTargetComposition` is frozen and `extra=forbid`; references are resolved before the target exists."}],
             evidence=ev("AgentTargetComposition", "pyrit/models/agent_setup.py", start=r"class AgentTargetComposition", lines=16, focus=[r"harness_profile: str", r"environment_template: str", r"model_target: str \| None"], tag="PR #21")),
        step("identity", "Identity", "Identity is the hash of the recipe",
             "`AgentTarget` builds its identifier from the configuration serialised without defaults and without the fields that are **execution policy rather than identity**: timeouts, holds, evidence and artifact budgets. Two operators who compose the same recipe from different preset names get the same identity.",
             status={"binding": "ok", "hprofile": "ok", "template": "ok", "config": "active"},
             slots={"config.identity": "sha256(configuration − defaults − timeouts − budgets)", "config.prov": "preset names · origin (kept, not hashed)"},
             edges={"b_c": "dim", "h_c": "dim", "t_c": "dim"},
             captions=[{"label": "environment", "text": "`environment` has no default: a target must say where it runs."}],
             evidence=ev("Fields excluded from identity", "pyrit/prompt_target/agent_target.py", start=r"_IDENTITY_EXCLUDED_FIELDS: ClassVar", before=1, lines=12, focus=[r"Timeouts and evidence budgets are execution policy", r"startup_timeout_seconds", r"max_evidence_bytes"], tag="PR #17")),
        step("policy", "Policy", "Permission policy is identity, and it defaults to deny",
             "A target that **asks an operator** and one that **denies** every tool permission are different systems under test, so `permission_policy` is hashed. New targets, in Python and in the UI, refuse permission requests. `allow_once` and `ask` are explicit choices; `ask` is only valid for interactive Co-PyRIT sends and fails fast for attacks.",
             status={"policy": "active", "config": "active"},
             slots={"policy.perm": "deny  (default) · allow_once · ask (interactive only)", "policy.timeouts": "excluded from identity"},
             edges={"p_c": "request"},
             animations=[{"type": "draw", "edge": "p_c"}],
             captions=[{"label": "not enforcement", "text": "The policy is a deterministic ACP permission response; the environment is the enforcement boundary."}],
             evidence=ev("AgentPermissionPolicy", "pyrit/models/agent_execution.py", start=r"class AgentPermissionPolicy", lines=7, focus=[r"not a tool enforcement boundary", r'DENY = "deny"'], tag="PR #17")),
        step("provenance", "Provenance", "Names are provenance, not identity",
             "`AgentTargetInitializer` composes registered model, harness and environment presets into one named target from YAML. The names and origin are kept on the catalog entry as `agent_configuration_provenance` so a reviewer can see **how** a target was built, while identity stays the resolved recipe.",
             status={"policy": "ok", "init": "active", "config": "active"},
             edges={"i_c": "request", "p_c": "dim"},
             animations=[packet("i_c", "register_agent_target")],
             captions=[{"label": "deferred", "text": "A Registry tab to browse presets, and “create by preset name” over REST, are deferred; the three-panel Create Target form stays the primary path."}],
             evidence=ev("AgentTargetInitializer", "pyrit/setup/initializers/agent_target.py", start=r"class AgentTargetInitializer", end=r"register_agent_target\(self.definition\(\)\)", focus=[r"Compose registered model, harness, and environment settings", r"initializer_parameters\(model=AgentTargetComposition\)", r"register_agent_target\(self.definition\(\)\)"], tag="PR #21")),
        step("gates", "Gates", "Admin-only over REST; fixtures confined",
             "Every execution launches a process or container with credentials the server holds, regardless of parameters, so creating an `AgentTarget` through the API is **administrator-only** via `requires_admin_creation`. `agent_configuration` arrives as one validated object through the `JsonParameterValue` opt-in (exactly one parameter in the repository). Fixture directories reuse upstream's `upload_directory_parameter` confinement.",
             status={"init": "ok", "config": "ok", "target": "active"},
             slots={"target.exec": "one owned execution per conversation", "target.admin": "requires_admin_creation · JsonParameterValue · confined fixtures"},
             edges={"c_t": "request", "i_c": "dim"},
             animations=[packet("c_t", "to_profile()")],
             captions=[{"label": "replacement", "text": "Live executions, including idle and held chats, block configuration replacement, exactly as active sends do."}],
             evidence=ev("AgentTarget · class markers", "pyrit/prompt_target/agent_target.py", start=r"requires_admin_creation: ClassVar", before=2, lines=6, focus=[r"requires_admin_creation", r"upload_directory_parameter"], tag="PR #17")),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 6
CH6 = {
    "id": "operators", "index": 6, "short": "topology", "accent": "green",
    "eyebrow": "Chapter 06 · Co-PyRIT operators",
    "title": "Watch it, approve it, hold it",
    "summary": "Live streaming of durable evidence; permission requests routed to an operator; hold, cancel and close.",
    "takeaway": "Observation and control are separate PRs because they are separate concerns. 6a/6b let an operator **watch** what the agent does, live, from the journal. PR 7 lets the operator **change** what happens: approve or deny a tool, hold an execution, cancel, close.",
    "breadcrumb": ["browser", "api", "runtime", "harness"],
    "legend": [{"swatch": "green", "label": "Operator decision"}, {"swatch": "mint", "label": "PyRIT runtime"}, {"swatch": "amber", "label": "Harness request"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("browser", "Co-PyRIT chat", 0, 40, 320, tone="green", tag="browser", cols=1, slots=[("stream", "stream"), ("decision", "decision")]),
            actor("api", "Backend routes", 400, 40, 340, tag="/execution/stream · /permissions", cols=1, slots=[("sse", "SSE"), ("rev", "revision")]),
            actor("manager", "AgentExecutionManager", 800, 40, 340, tag="core", slots=[("state", "state"), ("pending", "pending")]),
            actor("harness", "Harness", 1200, 40, 300, tone="amber", tag="session/request_permission", cols=1, slots=[("ask", "asks"), ("outcome", "told")]),
            actor("approvals", "AgentApprovals", 800, 300, 340, tag="decisions", cols=1, slots=[("decision", "decision"), ("timeout", "timeout")]),
            actor("journal", "Journal · lifecycle", 400, 300, 340, tag="who did what", cols=1, slots=[("ev", "events"), ("actor", "actor")]),
        ],
        "edges": [
            edge("hm", "harness", "manager"),
            edge("ma", "manager", "approvals"),
            edge("ab", "api", "browser", offset=-25),
            edge("ba", "browser", "api", offset=25),
            edge("am", "api", "manager"),
            edge("aj", "approvals", "journal"),
        ],
        "initial": {},
    },
    "steps": [
        card("intro", "Operators <em>in the loop</em>", "An agent that can act needs a person who can say no. Chapter 06 shows how Co-PyRIT streams the journal to the browser, and how a permission request travels from the harness to an operator and back, **as a denial when denied**, never as a cancellation."),
        step("watch", "Watch", "Watch what it does, live, from the journal",
             "The conversation stream is Server-Sent Events over the execution's durable journal: `state` snapshots, `events` pages by sequence, `reset` when a new execution replaces an old one, and `reconnect` when authorization must be refreshed. Nothing is held only in memory; a browser can reconnect and catch up by sequence.",
             status={"browser": "active", "api": "active"},
             slots={"browser.stream": "events after seq 11 · transcript_revision 3", "api.sse": "state → events → reset → reconnect", "api.rev": "transcript_revision bumps on every projected piece"},
             edges={"ab": "request"},
             animations=[packet("ab", ["state", "events", "events"], count=3, gapMs=600)],
             captions=[{"label": "per conversation", "text": "The store notifies subscribers per conversation id, so one chat's events do not wake every tab."}],
             evidence=ev("Conversation stream · state / reset / reconnect", "pyrit/backend/routes/executions.py", start=r"with manager.store.subscribe\(conversation_id\)", lines=14, focus=[r"store.subscribe\(conversation_id\)", r'"reconnect", \{"reason": "runtime_replaced"\}', r'_sse\("reset"', r"event: state"], tag="PR #22")),
        step("ask", "Ask", "The agent asks; the execution is held",
             "With `permission_policy=ask`, an ACP `session/request_permission` becomes an **approval** owned by the runtime. The harness must have offered a `reject_once` option, otherwise the request is invalid: a denial must be expressible as a denial. The execution enters `held` with a deadline.",
             status={"browser": "ok", "api": "ok", "manager": "active", "harness": "active"},
             slots={"manager.state": "working → held", "manager.pending": "1 approval · approval_timeout_seconds", "harness.ask": "write orders.json?"},
             edges={"hm": "response", "ab": "dim"},
             animations=[packet("hm", "request_permission", tone="amber")],
             captions=[{"label": "attacks", "text": "`ask` is only valid for interactive sends; attacks with `ask` fail fast instead of hanging on a prompt nobody will answer."}],
             evidence=ev("ACP client · request_permission", "pyrit/agent/acp_connection.py", start=r"async def request_permission", lines=20, focus=[r"AgentPermissionPolicy.ASK", r'option.kind == "reject_once"', r"await self.approvals.request_async"], tag="PR #24")),
        step("decide", "Decide", "An operator decides",
             "The browser posts a decision. `AgentApprovals.decide_async` accepts exactly four decisions: `operator_allow`, `operator_deny`, `timeout_deny`, `cancelled`. It resolves the pending future with the matching ACP option (`allow_once` or `reject_once`) and journals `permission.resolved` with the actor.",
             status={"browser": "active", "api": "active", "manager": "active", "approvals": "active", "harness": "ok"},
             slots={"browser.decision": "allow once", "approvals.decision": "operator_allow → allow_once", "manager.state": "held → working", "harness.outcome": "selected: allow_once"},
             edges={"ba": "operator", "am": "operator", "ma": "operator", "hm": "dim"},
             animations=[packet("ba", "allow", tone="green"), packet("am", "decide_permission_async", tone="green", delayMs=1100), packet("ma", "operator_allow", tone="green", delayMs=2000)],
             captions=[{"label": "commit point", "text": "The journal write is the commit point; a stale execution snapshot cannot reverse a decision."}],
             evidence=ev("AgentApprovals · decide", "pyrit/agent/approvals.py", start=r'if decision not in \("operator_allow"', before=1, lines=14, focus=[r'"operator_allow", "operator_deny", "timeout_deny", "cancelled"', r'"allow_once" if decision == "operator_allow" else "reject_once"'], tag="PR #24")),
        step("deny", "Deny", "A denial is a denial, not a cancellation",
             "If nobody answers before the deadline, the approval settles as `timeout_deny` by **policy**; if the execution is cancelled, pending approvals settle as `cancelled` by the **runtime**. Each is told to the harness through the protocol option it offered, so the agent's own account of what happened stays truthful.",
             status={"approvals": "active", "harness": "active", "browser": "idle", "api": "idle"},
             slots={"approvals.decision": "timeout_deny (policy) · cancelled (runtime)", "approvals.timeout": "deadline pauses while the hold is extended", "harness.outcome": "reject_once"},
             edges={"ma": "request", "hm": "response", "ba": "dim", "am": "dim"},
             animations=[packet("hm", "reject_once", reverse=True)],
             captions=[{"label": "projection", "text": "A denied call never becomes a `function_call_output`; it is omitted as `permission_not_granted`."}],
             evidence=ev("AgentApprovals · settle on timeout / cancel", "pyrit/agent/approvals.py", start=r'decision="timeout_deny"', before=3, lines=8, focus=[r'decision="timeout_deny", actor="policy"', r'decision="cancelled", actor="runtime"'], tag="PR #24")),
        step("hold", "Hold", "Hold, cancel, close",
             "Execution states are explicit: `starting · idle · working · held · closing · closed · cleanup_failed`. An operator can extend a hold, cancel the current turn (ACP `session/cancel` with a grace period), or close the execution; administrators close anyone's from Registry → Agent executions. `cleanup_failed` is a state, not a log line.",
             status={"manager": "active", "approvals": "ok", "harness": "ok"},
             slots={"manager.state": "held → closing → closed", "manager.pending": "0"},
             edges={"ma": "dim", "hm": "dim"},
             captions=[{"label": "replacement", "text": "An idle or held execution still blocks configuration replacement; it is a live resource."}],
             evidence=ev("AgentExecutionState", "pyrit/models/agent_execution.py", start=r"class AgentExecutionState", lines=10, focus=[r'HELD = "held"', r'CLEANUP_FAILED = "cleanup_failed"'], tag="PR #17")),
        step("identity", "Identity", "Who decided is visible, by default",
             "Decisions name the operator who made them to everyone with access to the attack. With `show_agent_operator_identity: false` the routes report roles instead (`operator`), while runtime actors (`policy`, `runtime`) are always shown as themselves. The journal keeps the real actor either way.",
             status={"approvals": "ok", "journal": "active", "manager": "idle"},
             slots={"journal.ev": "permission.pending → permission.resolved · execution.held · execution.operator_action", "journal.actor": "actor: alice@contoso  (or “operator”)"},
             edges={"aj": "request"},
             animations=[packet("aj", "permission.resolved")],
             captions=[{"label": "setting", "text": "Loader + app-state setting; default **show**."}],
             evidence=ev("Masking operator identity", "pyrit/backend/routes/executions.py", start=r"def _operator_role", lines=18, focus=[r"_RUNTIME_ACTORS", r'return "operator"', r"def _mask_event_actors"], tag="PR #24")),
    ],
}

# ---------------------------------------------------------------------------------------------- chapter 7
CH7 = {
    "id": "harnesses", "index": 7, "short": "topology", "accent": "amber",
    "eyebrow": "Chapter 07 · harnesses",
    "title": "Copilot CLI, Agent Framework, any ACP agent",
    "summary": "One adapter contract; Copilot CLI first, an in-repo ACP host for Agent Framework agents, and a login-free CI proof.",
    "takeaway": "`HarnessAdapter` is the second seam: how a harness is **launched** and how it is **pointed at a model**. Copilot CLI is the MVP harness. The optional Agent Framework host makes PyRIT itself an ACP agent for Microsoft Agent Framework agents, and doubles as the CI proof of the full ACP path without any login.",
    "breadcrumb": ["adapter", "copilot", "agent framework", "ci"],
    "legend": [{"swatch": "mint", "label": "Adapter contract · PyRIT"}, {"swatch": "amber", "label": "Harness · under test"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("seam", "HarnessAdapter", 0, 40, 340, tag="ABC", cols=1, slots=[("info", "info"), ("launch", "launch_environment"), ("infer", "inference_environment")]),
            actor("copilot", "copilot", 420, 40, 280, tone="amber", tag="GitHub Copilot CLI", sub="`copilot --acp --stdio`", slots=[("creds", "credentials")]),
            actor("generic", "generic ACP", 420, 300, 280, tone="amber", tag="any ACP agent", sub="command from the harness profile"),
            actor("af", "agent_framework", 780, 40, 340, tone="amber", tag="PyRIT ACP host", sub="`python -m pyrit.agent.hosts.agent_framework_acp`", cols=1, slots=[("tools", "tool names"), ("cancel", "cancel")]),
            actor("conf", "verify_harness_adapter", 780, 300, 340, tag="executable", sub="reserved names, credential rules, environment translation"),
            actor("ci", "CI proof", 1180, 40, 320, tag="login-free", sub="scripted ACP agent + Agent Framework agent through the relay"),
        ],
        "edges": [
            edge("s_cop", "seam", "copilot"),
            edge("s_gen", "seam", "generic"),
            edge("s_af", "seam", "af", points=[[340, 150], [380, 150], [380, 250], [740, 250], [740, 110], [780, 110]]),
            edge("c_af", "conf", "af"),
            edge("af_ci", "af", "ci"),
        ],
        "initial": {},
    },
    "steps": [
        card("intro", "<em>Harnesses</em>", "The agent under test is whatever speaks ACP on stdio. PyRIT ships adapters for the harness it targets first, Copilot CLI, and for its own host of Microsoft Agent Framework agents; everything else is the generic adapter."),
        step("contract", "Contract", "One adapter contract",
             "An adapter declares `info` (name, default command, supported wire APIs, native credential names, reserved variables), `launch_environment` (how the harness finds its private home) and `inference_environment` (how it is pointed at the relay). Process identity and lookup paths are reserved for the execution and can never be credential references.",
             status={"seam": "active"},
             slots={"seam.info": "name · default_command · wire_apis · native_model_credentials · reserved", "seam.launch": "HOME / COPILOT_HOME → private home", "seam.infer": "base_url · token · model · wire_api → env"},
             captions=[{"label": "registry", "text": "`HarnessAdapterRegistry` is setup-owned; profiles reference adapters by name."}],
             evidence=ev("HarnessAdapter · reserved environment", "pyrit/agent/harness.py", start=r"class HarnessAdapter\(ABC\)", lines=20, focus=[r"EXECUTION_RESERVED_ENVIRONMENT", r'"HOME"', r'"PATH"'], tag="PR #17")),
        step("copilot", "Copilot", "Copilot CLI over ACP",
             "`copilot --acp --stdio`. Native credentials (`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`) may be passed when no model is bound; with a binding, BYOK refuses them and the Copilot provider variables are reserved so nothing can redirect the harness.",
             status={"seam": "ok", "copilot": "active"},
             slots={"copilot.creds": "COPILOT_GITHUB_TOKEN (native) · or relay via COPILOT_PROVIDER_*"},
             edges={"s_cop": "request", "s_gen": "dim"},
             animations=[{"type": "draw", "edge": "s_cop"}, {"type": "draw", "edge": "s_gen", "delayMs": 300}],
             captions=[{"label": "verified", "text": "Integration-tested locally and in Docker with BYOK through the relay."}],
             evidence=ev("_CopilotAdapter · info", "pyrit/agent/harness.py", start=r"class _CopilotAdapter", lines=18, focus=[r'default_command=\("copilot", "--acp", "--stdio"\)', r"native_model_credentials", r"reserved_environment"], tag="PR #17")),
        step("af", "Agent Framework", "Agent Framework: PyRIT's own ACP host",
             "An optional extra. `pyrit.agent.hosts.agent_framework_acp` wraps any Agent Framework agent as an ACP agent: it owns the agent and its tools inside the environment, emits `tool_call` / `tool_call_update` with **declared** tool names, and reports `cancelled` only for a real ACP `session/cancel`. The model side is the `OPENAI_*` family, so the relay works unchanged.",
             status={"copilot": "ok", "af": "active"},
             slots={"af.tools": "TOOL_NAME_META_KEY = pyrit.tool_name", "af.cancel": "cancel_requested → stopReason cancelled"},
             edges={"s_af": "request"},
             animations=[{"type": "draw", "edge": "s_af", "durationMs": 1200}],
             captions=[{"label": "declined review item", "text": "The host imports `pyrit.models` inside the tested process by design; lazy package inits keep that to a pydantic model import."}],
             evidence=ev("_AgentFrameworkAdapter · info", "pyrit/agent/harness.py", start=r"class _AgentFrameworkAdapter", lines=16, focus=[r'"pyrit.agent.hosts.agent_framework_acp"', r"OPENAI_API_KEY is the relay channel", r'reserved_environment=\("OPENAI_BASE_URL"'], tag="PR #25")),
        step("ci", "CI", "The seam is executable; CI proves the path",
             "`verify_harness_adapter` checks an adapter's promises the same way the environment suite checks a provider. Integration tests run a scripted ACP agent and an Agent Framework agent through the relay with **no login**, so the full path (ACP, journal, projection, relay, approvals) is proven on every CI run.",
             status={"af": "ok", "conf": "active", "ci": "active"},
             edges={"c_af": "request", "af_ci": "request"},
             animations=[{"type": "draw", "edge": "c_af"}, packet("af_ci", "7 integration tests", delayMs=600)],
             captions=[{"label": "fixtures in this deck", "text": "Chapters 01 and 03 use a journal and projection captured from that scripted agent."}],
             evidence=ev("verify_harness_adapter", "pyrit/agent/conformance.py", start=r"def verify_harness_adapter", lines=16, focus=[r"def verify_harness_adapter"], tag="PR #17")),
    ],
}

# ---------------------------------------------------------------------------------------------- recap + stack + epilogue
RECAP = {
    "id": "recap", "index": None, "accent": "mint", "eyebrow": "Recap", "title": "One receipt, five ways",
    "steps": [
        card("recap", "One receipt, <em>five ways</em>",
             "The same `{\"total\": 31}` appeared as an ACP `tool_call_update` frame, as a journal line, as a `function_call_output` piece with provenance, as the thing an operator approved, and as what a scorer read. Five views, one source of truth: the journal.",
             bullets=[
                 {"title": "Agents are targets", "text": "`AgentTarget` is a `PromptTarget`; attacks, converters and scorers are unchanged."},
                 {"title": "Core owns policy, providers own resources", "text": "Two executable seams; `local`, `docker`, and your own sandbox."},
                 {"title": "Did, not said", "text": "Append-only journal; explicit projection; omissions carry reasons."},
                 {"title": "Bring your own model", "text": "Relay + lease + binding; the credential never enters the environment."},
                 {"title": "People in the loop", "text": "Watch live; approve, deny, hold, cancel, close; identity visible by default."},
             ], autoplay=12000),
    ],
}

STACK_NODES = [
    {"id": "p01", "label": "1", "title": "Explicit target outcomes", "pr": "#15", "branch": "agents/stack-01-outcomes", "x": 40, "y": 10, "w": 260, "url": f"{PR}/15",
     "claim": "A target can complete without assistant text. Every `TargetResponse` declares an explicit outcome instead of letting an empty string mean anything.",
     "facts": [["why separate", "Core-model change touching every target; approved on its own merits."], ["needs", "—"], ["reviewer", "target maintainers"]]},
    {"id": "p03", "label": "3", "title": "Single-inference capability", "pr": "#16", "branch": "agents/stack-03-single-inference", "x": 680, "y": 10, "w": 260, "url": f"{PR}/16",
     "claim": "Targets expose a single-inference capability: transport and auth without history or a tool loop. Independent of 1; the relay (4) is its first consumer.",
     "facts": [["why separate", "General target capability owned by target maintainers; sequencing-free."], ["needs", "—"]]},
    {"id": "p2a", "label": "2a", "title": "ACP runtime + environment seam", "pr": "#17", "branch": "agents/stack-02a-acp-runtime", "x": 40, "y": 100, "w": 260, "url": f"{PR}/17", "needs": ["p01"],
     "claim": "Existing attacks run against an ACP agent in an owned execution; the environment is a provider contract PyRIT verifies with an executable conformance suite.",
     "facts": [["ships", "`AgentTarget`, `AgentExecutionManager`, journal, `local` provider, Copilot + generic adapters, `requires_admin_creation`, `JsonParameterValue` opt-in, conformance suites"], ["needs", "1"], ["review focus", "policy in core; nothing provider-specific leaks in"]]},
    {"id": "aca", "label": "ext", "title": "ACA sandbox provider", "kind": "out-of-tree · optional package", "tone": "grey", "deferred": True, "x": 680, "y": 190, "w": 260,
     "claim": "Built against the 2a contract outside the repository with zero core changes; packaging waits on the preview CLI/SDK.",
     "facts": [["status", "proven, not shipped"], ["needs", "2a contract only"]]},
    {"id": "p2b", "label": "2b", "title": "Docker reference provider", "pr": "#18", "branch": "agents/stack-02b-docker-provider", "x": 40, "y": 190, "w": 260, "url": f"{PR}/18", "needs": ["p2a"],
     "claim": "Docker is the reference isolated provider others base theirs on: labels, capability drops, limits, image provenance, ownership-checked teardown.",
     "facts": [["why separate", "PyRIT does not own environments; this is the worked example."], ["needs", "2a"]]},
    {"id": "p2c", "label": "2c", "title": "Tool calls as pieces", "pr": "#19", "branch": "agents/stack-02c-tool-calls", "x": 360, "y": 190, "w": 260, "url": f"{PR}/19", "needs": ["p2a"],
     "claim": "What the agent did lands as `function_call` / `function_call_output` pieces PyRIT's tool-call scorers already read, with provenance and explicit omissions.",
     "facts": [["why separate", "Faithfulness of tool records is the false-evidence review; it deserves undivided attention."], ["needs", "2a"]]},
    {"id": "ckpt", "label": "later", "title": "Checkpoint save / restore", "kind": "deferred · after 2b", "tone": "grey", "deferred": True, "x": 40, "y": 280, "w": 260, "needs": ["p2b"],
     "claim": "Re-lands as an optional Docker-provider capability once the base provider is reviewed.", "facts": [["status", "deferred on purpose"]]},
    {"id": "p04", "label": "4", "title": "Target-backed model (BYOK)", "pr": "#20", "branch": "agents/stack-04-model-binding", "x": 360, "y": 280, "w": 260, "url": f"{PR}/20", "needs": ["p2c", "p03"],
     "claim": "The model behind the agent is a PyRIT target: relay, lease, binding and parameter-conflict rules. A distinct trust boundary.",
     "facts": [["why separate", "Scoped authority; the credential never leaves the server."], ["needs", "2a, 3"]]},
    {"id": "p05", "label": "5", "title": "Reusable configuration", "pr": "#21", "branch": "agents/stack-05-configuration", "x": 360, "y": 370, "w": 260, "url": f"{PR}/21", "needs": ["p04"],
     "claim": "Agents compose from initializers and registries; names are provenance, not identity.",
     "facts": [["why separate", "Config-plane change: YAML contract and provenance."], ["needs", "4"]]},
    {"id": "p08", "label": "8", "title": "Agent Framework ACP host", "pr": "#25", "branch": "agents/stack-08-agent-framework", "x": 680, "y": 370, "w": 260, "url": f"{PR}/25", "needs": ["p04"],
     "claim": "Host Microsoft Agent Framework agents over ACP as an optional extra; doubles as the login-free CI proof of the full ACP path.",
     "facts": [["why separate", "Separately releasable."], ["needs", "4"], ["stacked on", "7, so the fork shows one linear chain"]]},
    {"id": "relaydef", "label": "later", "title": "Relay modality · per-turn budgets", "kind": "deferred · after 4", "tone": "grey", "deferred": True, "x": 680, "y": 460, "w": 260,
     "claim": "Per-request modality enforcement and per-turn inference budgets change evidence semantics; deferred with the limits documented in chapter 04.", "facts": [["status", "deferred on purpose"]]},
    {"id": "startup", "label": "later", "title": "Durable startup authoring", "kind": "deferred · after 5", "tone": "grey", "deferred": True, "x": 40, "y": 460, "w": 260, "needs": ["p05"],
     "claim": "Persisting a reproducible definition is independent of basic agent testing.", "facts": [["status", "deferred on purpose"]]},
    {"id": "p6a", "label": "6a", "title": "Co-PyRIT API", "pr": "#22", "branch": "agents/stack-06a-copyrit-api", "x": 360, "y": 460, "w": 260, "url": f"{PR}/22", "needs": ["p05"],
     "claim": "Create agent targets, send, stream activity, inspect executions: curl-verifiable without the UI.",
     "facts": [["why separate", "API reviewers; SSE contract (`state/events/reset/reconnect`)."], ["needs", "5"]]},
    {"id": "playback", "label": "later", "title": "Evidence packages / playback", "kind": "deferred · after 6a", "tone": "grey", "deferred": True, "x": 680, "y": 550, "w": 260, "needs": ["p6a"],
     "claim": "“Download journal” covers the need; playback is its own UX.", "facts": [["status", "deferred on purpose"]]},
    {"id": "p6b", "label": "6b", "title": "Co-PyRIT UI", "pr": "#23", "branch": "agents/stack-06b-copyrit-ui", "x": 360, "y": 550, "w": 260, "url": f"{PR}/23", "needs": ["p6a"],
     "claim": "Configure agents (three-panel Create Target form) and watch what they do, integrated with the hook-based ChatWindow.",
     "facts": [["why separate", "Frontend reviewers."], ["needs", "6a"]]},
    {"id": "p07", "label": "7", "title": "Interactive controls", "pr": "#24", "branch": "agents/stack-07-approvals", "x": 360, "y": 640, "w": 260, "url": f"{PR}/24", "needs": ["p6b"],
     "claim": "Operators approve, cancel and hold; a control-plane change separate from observation.",
     "facts": [["why separate", "Changes the agent's behaviour mid-run."], ["needs", "6b"], ["also", "`show_agent_operator_identity` (default show)"]]},
    {"id": "registry", "label": "later", "title": "Agent configuration registry UI", "kind": "deferred · after 6b", "tone": "grey", "deferred": True, "x": 680, "y": 640, "w": 260, "needs": ["p6b"],
     "claim": "A Registry tab to browse presets and an optional “create by preset name” path. Does **not** replace the three-panel form.", "facts": [["status", "deferred on purpose"]]},
]

STACK = {
    "id": "stack", "index": 8, "short": "explorable", "accent": "mint",
    "eyebrow": "Epilogue · the pull-request stack",
    "title": "Eleven claims, one linear chain",
    "summary": "Hover a PR to read its claim, why it is separate, and what it needs. Dashed nodes are deferred on purpose.",
    "steps": [
        {"id": "explore", "kind": "stack", "title": "Eleven claims, one linear chain", "nodes": STACK_NODES, "infoEyebrow": "Hover or focus a node", "autoplayMs": 15000,
         "footer": "**Promotion.** #15 and #16 are independent and go first; each later PR is rebased onto `main` once its predecessor merges. Base: upstream `microsoft/PyRIT` main at `6a2319644`. The unsliced proof of concept lives on `agents/integration` (reference only). Story gist: gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90"},
    ],
}

EPILOGUE = {
    "id": "epilogue", "index": None, "accent": "mint", "eyebrow": "Epilogue", "title": "Limits and how to review",
    "steps": [
        card("limits", "Known <em>limits</em>", "Said plainly, so reviewers do not have to find them.",
             bullets=[
                 {"title": "Budgets are per execution", "text": "Default 100 inference requests, max 1,000; per-turn budgets are deferred."},
                 {"title": "Modality is declared, not policed per request", "text": "The binding declares input modalities; undeclared image parts are not yet rejected by the relay."},
                 {"title": "`local` is not a sandbox", "text": "Private home and workspace only; it requires an explicit acknowledgement."},
                 {"title": "Permission policy is not enforcement", "text": "It is a deterministic ACP answer; isolation comes from the environment."},
                 {"title": "Catalog shows credential *names*", "text": "And the resolved fixture path, to authenticated users; values never appear (declined review item, documented)."},
             ], autoplay=12000),
        card("review", "How to <em>review</em>", "Each PR carries its feature commit plus the fixes from two skeptical reviews as separate `FIX`/`TEST` commits, so you can see what the review changed. Each PR body lists the exact commands run and their results.",
             bullets=[
                 {"title": "Start with #15 and #16", "text": "Independent core/target changes; natural first promotions to `microsoft/PyRIT`."},
                 {"title": "Read 2a against the conformance report", "text": "Run `verify_environment_provider_async` for `local`; the Docker report is in 2b's tests."},
                 {"title": "Give 2c undivided attention", "text": "The projection rules and omission reasons are the false-evidence boundary."},
                 {"title": "Treat 4 as a trust boundary", "text": "Lease, body validation, reserved variables, redaction of the token."},
                 {"title": "Validation on top of the stack", "text": "`pytest tests/unit -n 4` 30,904 passed · frontend 166 suites / 3,767 tests · ruff, tsc, eslint, `uv lock --check` · 7 agent integration tests against a real scripted ACP process and the Agent Framework host."},
             ], autoplay=12000),
        card("end", "Questions, <em>objections</em>, better cuts", "Drafts #15–#25 on `spencrr/PyRIT`, GitHub stack #26. The story gist links every PR, the four diagrams, and this deck. Nothing is promoted to `microsoft/PyRIT` yet; that starts when you say so.",
             bullets=[
                 {"title": "Deck", "text": "Single-file HTML, deck-as-data; evidence resolved from pinned branch tips at build time (SHAs in the footer)."},
                 {"title": "Fixtures", "text": "Journal, record, projection and receipt captured from the scripted ACP agent in this repository: `fixtures/generate.py`."},
                 {"title": "Keys", "text": "← → steps · Tab chapter · Space autoplay · E evidence · P presenter · Esc overview · `?mode=present`"},
             ], autoplay=12000),
    ],
}

TITLE = {
    "id": "title", "index": None, "accent": "mint", "eyebrow": "PyRIT", "title": "PyRIT · agent targets",
    "steps": [
        {"id": "hero", "kind": "hero", "eyebrow": "PyRIT · agents as targets · design walkthrough", "word": "AGENTS", "sub": "as the thing under test",
         "lede": "PyRIT can red-team an **agent**, not just a model: a harness that plans, calls tools and changes state inside an environment PyRIT owns and disposes of. One receipt, five ways.",
         "status": "11 PRs · drafts #15–#25", "autoplayMs": 9000,
         "legend": [{"swatch": "mint", "label": "PyRIT-owned"}, {"swatch": "amber", "label": "Agent-owned"}, {"swatch": "grey", "label": "Infrastructure"}]},
        {"id": "agenda", "kind": "agenda", "eyebrow": "Seven chapters, one epilogue", "title": "What you will <em>see</em>",
         "lede": "Each chapter has one stage that evolves step by step, a claim on the left, and the code that makes it true on the right, pinned to the branch tips of the stack.",
         "principles": [
             {"title": "Unchanged above the target", "text": "Attacks, converters and scorers do not know an agent is on the other side."},
             {"title": "Core owns policy, providers own resources", "text": "Two executable seams: environment and harness."},
             {"title": "Did, not said", "text": "An append-only journal and an explicit projection; silence has reasons."},
             {"title": "Bring your own model, keep your own key", "text": "The relay is how the model becomes a PyRIT target and how no credential enters the environment."},
         ], "autoplayMs": 12000},
    ],
}

DECK = {
    "meta": {
        "title": "PyRIT · agent targets",
        "subtitle": "design walkthrough for drafts #15–#25 on spencrr/PyRIT",
        "version": "deck v1 · fork spencrr/PyRIT · base microsoft/PyRIT 6a2319644",
        "gist": "https://gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90",
        "autoplayMs": 8000,
    },
    "chapters": [TITLE, CH1, CH2, CH3, CH4, ZOOM, CH5, CH6, CH7, RECAP, STACK, EPILOGUE],
}
