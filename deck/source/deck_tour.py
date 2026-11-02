"""Tour-track chapters: the pitch mapped onto visual form (cold open, pitch, recipe builder, map, three perspectives)."""

from __future__ import annotations

from deck_helpers import ASSETS, PR, actor, card, edge, ev, packet, step

REAL = "fixtures/real"

GLOSSARY = {
    "ACP": "Agent Client Protocol: JSON-RPC over stdio between a client (here PyRIT) and an agent harness. The same protocol editors use to drive coding agents.",
    "harness": "The agent program under test (Copilot CLI, an Agent Framework host, any ACP agent): it plans, calls tools and talks to a model.",
    "journal": "PyRIT's append-only record of every ACP frame, lifecycle event and inference call for one execution (events.jsonl). Redacted before writing.",
    "lease": "An execution-scoped credential for the inference relay: a bearer token with a call budget, revoked when the execution closes.",
    "projection": "The rule that turns journaled tool_call frames into function_call / function_call_output memory pieces, or omits them with a reason.",
    "relay": "PyRIT's local OpenAI-compatible endpoint that validates each harness request against the bound target and forwards it with the single-inference capability.",
    "provider": "An ExecutionEnvironmentProvider: code that allocates, launches and releases the place a harness runs (local, docker, yours).",
    "adapter": "A HarnessAdapter: how one kind of harness is launched and pointed at a model.",
    "execution": "One owned, stateful instance: one native agent session in one environment, created on a conversation's first message and reused for later turns.",
    "recipe": "Model binding + harness profile + environment template + policy: the configuration whose hash is the target's identity.",
    "seam": "A contract between PyRIT core and extension code, verified by an executable conformance suite.",
    "conformance": "A suite that drives a real provider or adapter through its contract and reports one check per promise.",
    "Co-PyRIT": "PyRIT's web UI and REST API.",
    "piece": "A MessagePiece in PyRIT memory: text, function_call, function_call_output and other typed content scorers read.",
    "BYOM": "Bring your own model: bind a registered PyRIT target behind the harness so the model and the harness are testable independently.",
}

MINIMAP = {
    "w": 420, "h": 66,
    "regions": [
        {"id": "attack", "label": "attacks", "x": 0, "y": 0, "w": 76, "h": 28},
        {"id": "target", "label": "AgentTarget", "x": 80, "y": 0, "w": 64, "h": 28},
        {"id": "core", "label": "core · policy", "x": 148, "y": 0, "w": 86, "h": 28},
        {"id": "seams", "label": "seams", "x": 238, "y": 0, "w": 56, "h": 28},
        {"id": "providers", "label": "harness · provider", "x": 298, "y": 0, "w": 122, "h": 28, "tone": "amber"},
        {"id": "recipes", "label": "recipes", "x": 0, "y": 38, "w": 76, "h": 28},
        {"id": "copyrit", "label": "Co-PyRIT · operators", "x": 80, "y": 38, "w": 154, "h": 28, "tone": "green"},
        {"id": "extend", "label": "extend · conformance", "x": 238, "y": 38, "w": 182, "h": 28, "dim": True},
    ],
}

# ---------------------------------------------------------------------------------------------- cold open
COLD = {
    "id": "cold", "index": 1, "short": "cold open", "accent": "mint", "track": "tour",
    "eyebrow": "Cold open · one receipt",
    "title": "What just happened",
    "summary": "A one-line request, a real agent that reads a file and writes a receipt, and a scorer that reads what it did.",
    "takeaway": "PyRIT just red-teamed an **agent**: not a model's text, but a program that acted.",
    "regions": ["attack", "target", "providers"],
    "legend": [{"swatch": "mint", "label": "PyRIT"}, {"swatch": "amber", "label": "The agent and what it touched"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("you", "A PyRIT attack", 0, 160, 300, tag="unchanged code", slots=[("say", "says")]),
            actor("agent", "An agent", 460, 60, 340, tone="amber", tag="real harness · ACP", cols=1, slots=[("plan", "plans"), ("tool", "calls a tool")]),
            actor("file", "orders.json", 460, 320, 340, tone="amber", tag="inside its workspace", slots=[("state", "state")]),
            actor("scorer", "A PyRIT scorer", 1000, 160, 300, tag="unchanged code", cols=1, slots=[("sees", "reads"), ("verdict", "verdict")]),
        ],
        "edges": [edge("ask", "you", "agent"), edge("read", "agent", "file"), edge("tell", "agent", "scorer")],
        "initial": {},
    },
    "steps": [
        step("ask", "Ask", "“Create the receipt.”",
             "One ordinary PyRIT message. Nothing in the attack, the converters or the scorers knows that the thing on the other side is an agent.",
             status={"you": "active", "agent": "active"}, slots={"you.say": "“Create the receipt”"}, edges={"ask": "request"},
             animations=[packet("ask", "user text")],
             captions=[{"label": "fixture", "text": "Everything in this chapter is a real capture from the scripted ACP agent in the repository."}], autoplay=7000),
        step("act", "Act", "The agent plans, reads a file, does arithmetic",
             "It calls `read_orders({\"path\": \"orders.json\"})`, gets the items back, and answers “Receipt total: 31”. That is behaviour, not prose: a tool call with a result, inside a workspace PyRIT gave it.",
             status={"you": "ok", "agent": "active", "file": "active"}, slots={"agent.plan": "read the order, then write the receipt", "agent.tool": "read_orders({\"path\": \"orders.json\"})", "file.state": "2 × apple · 5 × orange → total 31"},
             edges={"ask": "dim", "read": "response"}, animations=[packet("read", ["tool_call", "{\"total\": 31}"], tone="amber", count=2, gapMs=700)],
             captions=[{"label": "the agent", "text": "A harness such as GitHub Copilot CLI, speaking the Agent Client Protocol over stdio."}], autoplay=8000),
        step("score", "Score", "A scorer reads what it did, not only what it said",
             "The tool call and its result arrive in PyRIT memory as the same `function_call` / `function_call_output` pieces existing tool-call scorers already read, each pointing back to the exact journal frame it came from.",
             status={"agent": "ok", "file": "ok", "scorer": "active"}, slots={"scorer.sees": "function_call · function_call_output · “Receipt total: 31”", "scorer.verdict": "did it act as instructed? yes"},
             edges={"read": "dim", "tell": "request"}, animations=[packet("tell", "pieces", count=2, gapMs=600)],
             captions=[{"label": "five ways", "text": "That one `{\"total\": 31}` appears as a wire frame, a journal line, a memory piece, an operator's approval and a score. Keep an eye on it."}],
             evidence=ev("Captured projection · turn 1", fixture="projection.json", start=r'"pieces"', lines=16, focus=[r'"type": "function_call"', r'"name": "read_orders"', r'"total": 31'], tag="fixture"), autoplay=9000),
    ],
}

# ---------------------------------------------------------------------------------------------- the pitch
PITCH = {
    "id": "pitch", "index": 2, "short": "the pitch", "accent": "mint", "track": "tour",
    "eyebrow": "Chapter 02 · the pitch",
    "title": "PyRIT can red-team an agent, not just a model",
    "summary": "Four claims and one thing that does not change.",
    "takeaway": "The thing under test is a **harness** that plans, calls tools and changes state inside an environment PyRIT owns and disposes of.",
    "regions": ["attack", "target", "core", "copyrit"],
    "legend": [{"swatch": "mint", "label": "Claim"}, {"swatch": "grey", "label": "Unchanged PyRIT"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("c1", "Agents are targets", 0, 20, 350, tag="claim 1", sub="`AgentTarget` is one more `PromptTarget`. Attacks, converters and scorers run against it **unchanged**.", cols=1, slots=[("so", "so what")]),
            actor("c2", "Did, not said", 380, 20, 350, tag="claim 2", sub="PyRIT records every protocol frame and projects the agent's **actions** into memory as tool-call pieces.", cols=1, slots=[("so", "so what")]),
            actor("c3", "Bring your own model", 760, 20, 350, tag="claim 3", sub="The model behind the agent can be a **PyRIT target** behind a scoped relay; the harness and the model become independently testable.", cols=1, slots=[("so", "so what")]),
            actor("c4", "People in the loop", 1140, 20, 350, tag="claim 4", sub="Operators watch live, approve or deny tool permissions, hold, cancel and close; decisions are attributed.", cols=1, slots=[("so", "so what")]),
            actor("unch", "What does not change", 0, 300, 1490, kind="panel", h=120, tag="existing PyRIT surfaces"),
            actor("att", "Attacks", 30, 350, 260, kind="node"), actor("conv", "Converters", 330, 350, 260, kind="node"), actor("scor", "Scorers", 630, 350, 260, kind="node"), actor("mem", "Memory", 930, 350, 260, kind="node"), actor("ui", "Co-PyRIT chat", 1230, 350, 240, kind="node"),
        ],
        "edges": [],
        "initial": {},
    },
    "steps": [
        step("claims", "Claims", "Four claims, each verifiable end to end",
             "This is the whole pitch. Each claim maps to a chapter of this tour and to one or two pull requests in the stack; nothing here is aspirational.",
             status={"c1": "active", "c2": "active", "c3": "active", "c4": "active"},
             slots={"c1.so": "your existing test suite runs against agents", "c2.so": "scores are about behaviour, with provenance", "c3.so": "swap the model, hold the harness (or vice versa)", "c4.so": "a human can say no, mid-run"},
             captions=[{"label": "source", "text": "The one-sentence pitch in the design gist, split into its four load-bearing parts."}], autoplay=10000),
        step("unchanged", "Unchanged", "Everything above the target is untouched",
             "No new attack types, no new scorer types, no new memory schema. The agent arrives as a target; what it did arrives as pieces. That is why the stack can start with two small core PRs and why the rest is reviewable brick by brick.",
             status={"c1": "ok", "c2": "ok", "c3": "ok", "c4": "ok", "unch": "active", "att": "active", "conv": "active", "scor": "active", "mem": "active", "ui": "active"},
             captions=[{"label": "one new seam in core", "text": "Targets gained an explicit **outcome** (PR 1) and a **single-inference** capability (PR 3); both are useful without agents."}],
             evidence=ev("AgentTarget is a PromptTarget", "pyrit/prompt_target/agent_target.py", start=r"^class AgentTarget\(", lines=10, focus=[r"class AgentTarget\(", r"requires_admin_creation"], tag="PR #17"), autoplay=9000),
    ],
}

# ---------------------------------------------------------------------------------------------- recipe builder
RECIPE = {
    "id": "recipe", "index": 3, "short": "interactive", "accent": "mint", "track": "tour",
    "eyebrow": "Chapter 03 · everything is swappable",
    "title": "A target is a recipe",
    "summary": "Swap the model, the harness, the environment or the policy and watch the identity change; change a timeout and watch it not.",
    "takeaway": "Model binding + harness profile + environment template + policy → a configured target that **owns no resources**. Identity is the hash of the recipe; preset names are provenance.",
    "regions": ["recipes", "target"],
    "legend": [{"swatch": "mint", "label": "Part of the identity"}, {"swatch": "grey", "label": "Excluded: execution policy"}],
    "stage": {"canvas": {"w": 1500, "h": 470}},
    "initial": {"recipe": {"model": 0, "harness": 0, "environment": 0, "policy": 0, "timeout": 0, "highlight": []}},
    "recipe": {
        "note": "Illustration of the rule in `AgentTarget._build_identifier` (configuration without defaults, timeouts and budgets); the bytes PyRIT hashes are pydantic's serialization, so the digits differ.",
        "parts": [
            {"id": "model", "label": "Model binding", "options": [
                {"label": "harness default", "short": "default", "desc": "The harness authenticates itself (e.g. `COPILOT_GITHUB_TOKEN`); PyRIT records that no binding exists.", "value": {"model_binding": {"target": None}}},
                {"label": "openai_chat", "short": "openai", "desc": "A registered `OpenAIChatTarget` answers every completion through the relay.", "value": {"model_binding": {"target": "openai_chat", "wire_api": "chat_completions"}}, "preset": "openai_chat"},
                {"label": "ollama_local", "short": "ollama", "desc": "A local model behind the same relay; the harness cannot tell the difference.", "value": {"model_binding": {"target": "ollama_local", "wire_api": "chat_completions"}}, "preset": "ollama_local"},
            ]},
            {"id": "harness", "label": "Harness profile", "options": [
                {"label": "copilot", "short": "copilot", "desc": "`copilot --acp --stdio` · GitHub Copilot CLI.", "value": {"harness_profile": {"harness_kind": "copilot", "command": ["copilot", "--acp", "--stdio"]}}, "preset": "copilot-cli"},
                {"label": "agent_framework", "short": "AF host", "desc": "PyRIT's ACP host running a Microsoft Agent Framework agent.", "value": {"harness_profile": {"harness_kind": "agent_framework", "command": ["python", "-m", "pyrit.agent.hosts.agent_framework_acp", "--agent", "receipt_agent"]}}, "preset": "af-receipt"},
                {"label": "any ACP agent", "short": "generic", "desc": "The scripted agent used for this deck's fixtures, or your own.", "value": {"harness_profile": {"harness_kind": "acp", "command": ["python", "scripted_acp_agent.py"]}}},
            ]},
            {"id": "environment", "label": "Environment template", "options": [
                {"label": "docker", "short": "docker", "desc": "Container per execution, labelled and capability-dropped; the reference isolated provider.", "value": {"environment_template": {"environment": "docker", "image": "pyrit/agent-receipt", "docker_network": "none"}}, "preset": "receipt-docker"},
                {"label": "local", "short": "local", "desc": "A subprocess with a private home. **Not** a sandbox; requires an explicit acknowledgement.", "value": {"environment_template": {"environment": "local", "local_execution_acknowledged": True}}, "preset": "receipt-local"},
                {"label": "aca-sandbox", "short": "ACA", "desc": "An out-of-tree cloud provider built against the same contract.", "value": {"environment_template": {"environment": "aca", "image": "pyrit/agent-receipt"}}},
            ]},
            {"id": "policy", "label": "Permission policy", "options": [
                {"label": "deny", "short": "deny", "desc": "Default. Every tool permission request is refused; the agent is told so.", "value": {"permission_policy": "deny"}},
                {"label": "allow_once", "short": "allow", "desc": "Each request is granted once, deterministically.", "value": {"permission_policy": "allow_once"}},
                {"label": "ask", "short": "ask", "desc": "An operator decides in Co-PyRIT; only valid for interactive sends.", "value": {"permission_policy": "ask"}},
            ]},
        ],
        "excluded": {"label": "Timeouts · budgets", "desc": "Turn deadline, idle expiry, lifetime, evidence and artifact budgets: execution policy, **excluded** from identity.", "options": [
            {"label": "180 s · 300 s · 900 s"}, {"label": "60 s · 120 s · 600 s"}, {"label": "600 s · 3600 s · 86400 s"},
        ]},
    },
    "steps": [
        {"id": "parts", "kind": "recipe", "tab": "Parts", "title": "Four parts you can swap", "patch": {"recipe": {"highlight": ["model", "harness", "environment", "policy"]}},
         "body": "Which model answers, which agent runs and how it is launched, where it runs, and what happens when it asks for permission. Each part is reusable and registered by setup. Use the arrows to swap any of them.",
         "captions": [{"label": "try it", "text": "‹ › on any card; the hash below updates live."}, {"label": "presets", "text": "Preset names are kept as provenance, never hashed."}],
         "evidence": ev("The composition", "pyrit/models/agent_setup.py", start=r"class AgentTargetComposition", lines=16, focus=[r"harness_profile: str", r"environment_template: str", r"model_target: str \| None"], tag="PR #21"), "autoplayMs": 10000},
        {"id": "model", "kind": "recipe", "tab": "Model", "title": "Swap the model: a different system under test", "patch": {"recipe": {"model": 1, "highlight": ["model"]}},
         "body": "Bind `openai_chat`. The harness is identical, but the recipe changed, so the identity changed: results from this target never mix with the previous one. This is **bring your own model**: the harness will reach the bound target only through PyRIT's relay.",
         "captions": [{"label": "why it matters", "text": "Regressions can be attributed to the model or to the scaffolding, not to “the agent”."}],
         "evidence": ev("Identity excludes execution policy", "pyrit/prompt_target/agent_target.py", start=r"_IDENTITY_EXCLUDED_FIELDS: ClassVar", before=1, lines=12, focus=[r"Timeouts and evidence budgets are execution policy", r"startup_timeout_seconds", r"max_evidence_bytes"], tag="PR #17"), "deeper": "relay/intro", "deeperLabel": "How the relay works", "autoplayMs": 9000},
        {"id": "env", "kind": "recipe", "tab": "Environment", "title": "Swap where it runs", "patch": {"recipe": {"environment": 1, "highlight": ["environment"]}},
         "body": "Same model, same harness, now in a local process instead of a container. `environment` has no default: a target must say where it runs, and a non-isolated provider must be acknowledged.",
         "captions": [{"label": "providers", "text": "`local` and `docker` ship with PyRIT; an ACA sandbox provider was built out of tree against the same contract."}],
         "evidence": ev("A template is never a live resource", "pyrit/models/agent_execution.py", start=r"class EnvironmentTemplate\(BaseModel\)", lines=20, focus=[r"Reusable starting filesystem", r"environment: str$", r"local_execution_acknowledged"], tag="PR #17"), "deeper": "seam/intro", "deeperLabel": "The environment seam", "autoplayMs": 9000},
        {"id": "policy", "kind": "recipe", "tab": "Policy", "title": "Permission policy is identity, and it defaults to deny", "patch": {"recipe": {"policy": 2, "highlight": ["policy"]}},
         "body": "A target that **asks an operator** and one that **denies** every tool permission are different systems under test, so the policy is hashed. New targets refuse permission requests unless you say otherwise; `ask` is only valid for interactive chat and fails fast for attacks.",
         "captions": [{"label": "not enforcement", "text": "The policy is a deterministic protocol answer; isolation comes from the environment."}],
         "evidence": ev("AgentPermissionPolicy", "pyrit/models/agent_execution.py", start=r"class AgentPermissionPolicy", lines=7, focus=[r"not a tool enforcement boundary", r'DENY = "deny"'], tag="PR #17"), "autoplayMs": 9000},
        {"id": "timeouts", "kind": "recipe", "tab": "Timeouts", "title": "Tighten a deadline: same identity", "patch": {"recipe": {"timeout": 1, "highlight": ["timeout"]}},
         "body": "Timeouts, holds, evidence and artifact budgets are **execution policy**. Change them and the hash is unchanged: results stay comparable across a deadline tweak, while changing what runs or where creates a new target.",
         "captions": [{"label": "rule", "text": "`exclude_defaults=True, exclude=_IDENTITY_EXCLUDED_FIELDS` on the configuration dump."}],
         "evidence": ev("Building the identifier", "pyrit/prompt_target/agent_target.py", start=r"def _build_identifier", lines=8, focus=[r"exclude_defaults=True", r"_IDENTITY_EXCLUDED_FIELDS"], tag="PR #17"), "autoplayMs": 9000},
        {"id": "matrix", "kind": "recipe", "tab": "Matrix", "title": "Hold one, swap the other", "patch": {"recipe": {"mode": "matrix", "highlight": []}},
         "body": "Two harnesses × two models = four systems under test, each with its own identity and its own results. Is the regression in the model or in the scaffolding? Run the row, run the column.",
         "captions": [{"label": "in practice", "text": "Compose each cell from the same presets in YAML; names are provenance, so the matrix is auditable."}],
         "evidence": ev("Provenance is kept outside identity", "pyrit/models/agent_setup.py", start=r"class AgentTargetProvenance", lines=6, focus=[r"Origin retained outside the target's identity", r"presets: AgentPresetSelection"], tag="PR #21"), "deeper": "recipes/intro", "deeperLabel": "Identity in depth", "autoplayMs": 10000},
    ],
}

# ---------------------------------------------------------------------------------------------- the map
MAP = {
    "id": "map", "index": 4, "short": "the map", "accent": "mint", "track": "tour",
    "eyebrow": "Chapter 04 · the map",
    "title": "Core owns policy, extensions own resources",
    "summary": "The components, who owns what, and the two seams a reviewer should hold in mind.",
    "takeaway": "Everything PyRIT-owned sits in **core** and runs once; everything that allocates or runs sits behind a **seam** PyRIT can verify.",
    "regions": [],
    "legend": [{"swatch": "mint", "label": "PyRIT core"}, {"swatch": "grey", "label": "Seam · contract"}, {"swatch": "amber", "label": "Extension-owned resources"}, {"swatch": "green", "label": "People"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("attacks", "Attacks · converters · scorers · memory", 0, 20, 300, kind="panel", h=430, tag="unchanged"),
            actor("attack", "Attack", 20, 70, 260, kind="node"), actor("scorer", "Scorers", 20, 130, 260, kind="node"), actor("memory", "Memory", 20, 190, 260, kind="node"),
            actor("people", "Co-PyRIT", 20, 290, 260, tone="green", tag="operators · admins", sub="chat · approvals · registry"),
            actor("target", "AgentTarget", 340, 70, 220, tag="PromptTarget", sub="one owned execution per conversation"),
            actor("core", "Core · pyrit.agent", 600, 20, 420, kind="panel", h=430, tag="policy, once"),
            actor("manager", "Execution manager", 620, 70, 180, kind="node"), actor("journal", "Journal", 820, 70, 180, kind="node"),
            actor("projection", "Tool-call projection", 620, 130, 180, kind="node"), actor("acp", "ACP client", 820, 130, 180, kind="node"),
            actor("relayn", "Inference relay", 620, 190, 180, kind="node"), actor("approvals", "Approvals", 620, 250, 180, kind="node"),
            actor("seamh", "HarnessAdapter", 620, 310, 180, tag="seam", sub="launch · model env"), actor("seame", "Env. provider", 820, 310, 180, tag="seam · ABC", sub="4 moments"),
            actor("ext", "Extension-owned", 1060, 20, 440, kind="panel", h=430, tag="allocated per execution"),
            actor("env", "Environment", 1080, 70, 400, tone="amber", tag="local · docker · yours", cols=1, slots=[("has", "contains")]),
            actor("harness", "Harness process", 1080, 220, 190, tone="amber", tag="Copilot · AF · ACP"), actor("model", "Bound model", 1290, 220, 190, tag="a PyRIT target", sub="via the relay"),
            actor("workspace", "Workspace", 1080, 340, 400, tone="amber", tag="seeded from the template"),
        ],
        "edges": [
            edge("a_t", "attack", "target"), edge("t_c", "target", "manager"), edge("p_c", "people", "approvals", points=["people:r", [585, 316], [585, 272], "approvals:l"]),
            edge("sh_h", "seamh", "harness", fromSide="r", toSide="l"), edge("se_e", "seame", "env", fromSide="r", toSide="l"),
            edge("r_m", "relayn", "model", points=["relayn:r", [810, 212], [1040, 212], [1040, 250], "model:l"]),
            edge("pr_mem", "projection", "memory", points=["projection:l", [590, 160], [590, 212], "memory:r"]),
        ],
        "initial": {},
    },
    "steps": [
        step("above", "Above the target", "Nothing above the target changes",
             "Attacks send messages; converters rewrite them; scorers read the pieces that come back; memory stores them. An agent target sits where any target sits. Operators and administrators meet the system in Co-PyRIT.",
             status={"attacks": "active", "attack": "active", "scorer": "active", "memory": "active", "people": "active", "target": "active"}, edges={"a_t": "request"}, regions=["attack", "target", "copyrit"],
             animations=[packet("a_t", "message")],
             captions=[{"label": "reading the map", "text": "Left to right: who asks → what owns the policy → who owns the resources."}], autoplay=9000),
        step("core", "Core", "Core: the policy, implemented once",
             "The execution manager owns timeouts, budgets, cancellation and reconciliation. The journal records every frame. The projection turns frames into pieces. Approvals route permission requests to policy or a person. The relay stands between the harness and any model. The ACP client speaks the protocol.",
             status={"attacks": "ok", "attack": "idle", "scorer": "idle", "memory": "idle", "people": "idle", "target": "ok", "core": "active", "manager": "active", "journal": "active", "projection": "active", "approvals": "active", "relayn": "active", "acp": "active"},
             edges={"a_t": "dim", "t_c": "request", "pr_mem": "request", "p_c": "operator"}, regions=["core"],
             animations=[packet("t_c", "send_async"), packet("pr_mem", "pieces", delayMs=900), packet("p_c", "allow / deny", tone="green", delayMs=1500)],
             captions=[{"label": "why once", "text": "A provider cannot weaken redaction or budgets by accident, because it never sees them."}],
             evidence=ev("Core owns the lease budget", "pyrit/agent/execution_manager.py", start=r"lease = InferenceLease\(", lines=8, focus=[r"max_calls=live.record.profile.max_inference_requests", r"timeout_seconds"], tag="PR #20"), autoplay=10000),
        step("seams", "Seams", "Two seams, both executable",
             "`HarnessAdapter`: how a kind of harness is launched and pointed at a model. `ExecutionEnvironmentProvider`: four moments (reserve, translate the relay URL, launch, close). Each has a conformance suite that drives a real implementation and reports one check per promise.",
             status={"core": "ok", "manager": "ok", "journal": "ok", "projection": "ok", "approvals": "ok", "relayn": "ok", "acp": "ok", "seamh": "active", "seame": "active"},
             edges={"t_c": "dim", "pr_mem": "dim", "p_c": "dim", "sh_h": "request", "se_e": "request"}, regions=["seams", "extend"],
             animations=[{"type": "draw", "edge": "sh_h"}, {"type": "draw", "edge": "se_e", "delayMs": 400}],
             captions=[{"label": "for reviewers", "text": "A provider PR is reviewed against its conformance report, not against a reading of the ABC."}],
             evidence=ev("ExecutionSession · four moments", "pyrit/agent/environment.py", start=r"def reserve\(self\)", end=r"async def close_async", after=1, focus=[r"def reserve", r"async def inference_endpoint_async", r"async def launch_async", r"async def close_async"], tag="PR #17"), autoplay=10000),
        step("resources", "Resources", "Extensions own what gets allocated",
             "An environment (local process, Docker container, your sandbox) that contains the harness process and a workspace seeded from the template. The harness reaches a model only through the relay, with a lease, so no provider credential ever enters the environment.",
             status={"seamh": "ok", "seame": "ok", "ext": "active", "env": "active", "harness": "active", "workspace": "active", "model": "active", "relayn": "active"},
             slots={"env.has": "harness process · workspace · nothing else"}, edges={"sh_h": "dim", "se_e": "dim", "r_m": "request"}, regions=["providers", "core"],
             animations=[packet("r_m", "lease-scoped inference", delayMs=300, durationMs=2200)],
             captions=[{"label": "disposed", "text": "Closing an execution releases exactly what the provider allocated, and refuses anything it did not create."}],
             evidence=ev("Ownership check before stop", "pyrit/agent/environments/docker.py", start=r"async def _inspect_owned_container_async", lines=14, focus=[r'labels.get\("pyrit.execution"\)', r"Refusing to stop"], tag="PR #18"), deeper="turn/intro", deeperLabel="Follow one turn", autoplay=10000),
        card("diagram", "The same map, <em>as the reviewers see it</em>", "The runtime-architecture diagram from the design gist. Everything on this tour is a zoom into one of its regions; the minimap in the header shows which.",
             eyebrow="Chapter 04 · the map", image=f"{ASSETS}/runtime-architecture.png", alt="Runtime architecture diagram", note="SVG source and PNG on the `agents/stack-assets` branch; the gist embeds the same image.", autoplay=10000),
    ],
}
for _s in MAP["steps"]:
    if "regions" in _s and _s["kind"] == "step":
        pass
# step() does not take regions; attach them after the fact
_MAP_REGIONS = {"above": ["attack", "target", "copyrit"], "core": ["core"], "seams": ["seams", "extend"], "resources": ["providers", "core"], "diagram": ["*"]}
for _s in MAP["steps"]:
    _s["regions"] = _MAP_REGIONS[_s["id"]]

# ---------------------------------------------------------------------------------------------- operator's day
OPERATOR = {
    "id": "operator", "index": 5, "short": "replay · real captures", "accent": "green", "track": "tour",
    "eyebrow": "Chapter 05 · the operator's day",
    "title": "Ask, watch, approve",
    "summary": "A Co-PyRIT chat replayed from a real journal: send, watch the agent act, decide a permission, read the score.",
    "takeaway": "Operators see **what the agent does**, live, and can **say no** mid-run. Every decision is attributed and journaled.",
    "regions": ["copyrit", "core"],
    "legend": [{"swatch": "mint", "label": "Outgoing · PyRIT"}, {"swatch": "amber", "label": "Incoming · the agent"}, {"swatch": "green", "label": "Operator decision"}],
    "stage": {"canvas": {"w": 1500, "h": 470}},
    "replay": {"fixture": "ask_events"},
    "initial": {"replay": {"upto": 0}},
    "steps": [
        {"id": "pick", "kind": "replay", "tab": "Pick", "title": "Pick an agent target like any other", "patch": {"replay": {"upto": 0}},
         "body": "In Co-PyRIT chat the target picker lists `receipt_agent` next to every other registered target. Model, harness profile and environment template are fixed for the conversation; a status strip shows the feed, the ACP connection and the environment.",
         "captions": [{"label": "fixture", "text": "This replay is driven by `ask_events.jsonl`, a real journal captured with `permission_policy=ask`."}, {"label": "mock", "text": "The chat is a faithful mock; the real screenshots follow."}], "autoplayMs": 8000},
        {"id": "send", "kind": "replay", "tab": "Send", "title": "The first message provisions an execution", "patch": {"replay": {"upto": 7}},
         "body": "`initialize`, `session/new`, then `session/prompt`. The environment is allocated, the harness launched and connected; from now on the conversation reuses this execution. The journal on the right is the same data the chat streams over SSE.",
         "captions": [{"label": "status strip", "text": "Feed: live · ACP: ready · Environment: local / working · Cancel turn · Close execution."}],
         "evidence": ev("Conversation stream · state / reset / reconnect", "pyrit/backend/routes/executions.py", start=r"with manager.store.subscribe\(conversation_id\)", lines=14, focus=[r"store.subscribe\(conversation_id\)", r'_sse\("reset"', r"event: state"], tag="PR #22"), "autoplayMs": 9000},
        {"id": "watch", "kind": "replay", "tab": "Watch", "title": "Watch it act", "patch": {"replay": {"upto": 9}},
         "body": "“I will read the order.” Then a `tool_call` named `read_orders`. The activity panel renders each streamed update under the message as it arrives; nothing is invented from the agent's prose.",
         "captions": [{"label": "did, not said", "text": "Only explicit, named tool calls with established results become memory pieces."}], "deeper": "journal/intro", "deeperLabel": "How the projection works", "autoplayMs": 8000},
        {"id": "ask", "kind": "replay", "tab": "Ask", "title": "The agent asks; the execution is held",
         "patch": {"replay": {"upto": 11}},
         "body": "`session/request_permission` for “Read orders.json”. With the `ask` policy the runtime turns it into an approval and holds the execution: **Allow once** or **Deny** appear under the tool call, with the deadline. The harness had to offer a `reject_once` option, so a denial can be expressed as a denial.",
         "captions": [{"label": "try it", "text": "Click **Allow once** in the mock."}, {"label": "attacks", "text": "Automated attacks cannot use `ask`; they fail fast instead of hanging."}],
         "evidence": ev("ACP client · request_permission", "pyrit/agent/acp_connection.py", start=r"async def request_permission", lines=20, focus=[r"AgentPermissionPolicy.ASK", r'option.kind == "reject_once"', r"await self.approvals.request_async"], tag="PR #24"), "autoplayMs": 9000},
        {"id": "allow", "kind": "replay", "tab": "Allow", "title": "An operator allows it, once", "patch": {"replay": {"upto": 14}},
         "body": "`permission.resolved · operator_allow` names the operator. The reply goes back to the agent as the `allow_once` option it offered, the tool runs, and `tool_call_update` carries the real result. Decisions are server-owned, so a refresh or a second tab cannot lose or double-answer one.",
         "captions": [{"label": "attribution", "text": "Visible to everyone with access to the attack by default; `show_agent_operator_identity: false` shows roles instead."}],
         "evidence": ev("AgentApprovals · decide", "pyrit/agent/approvals.py", start=r'if decision not in \("operator_allow"', before=1, lines=14, focus=[r'"operator_allow", "operator_deny", "timeout_deny", "cancelled"', r'"allow_once" if decision == "operator_allow" else "reject_once"'], tag="PR #24"), "deeper": "operators/intro", "deeperLabel": "Approvals in depth", "autoplayMs": 9000},
        {"id": "done", "kind": "replay", "tab": "Score", "title": "“Receipt total: 31”, and a score about what it did",
         "patch": {"replay": {"upto": 20, "score": "scoring · **MessageToolCallScorer** read `read_orders` → `{\"total\": 31}` · outcome **completed**"}},
         "body": "The turn completes. The transcript stores the assistant text plus the projected tool pieces; Co-PyRIT auto-scores completed assistant and tool pieces with whatever scorer you configured. The agent's words and its actions are both evidence now.",
         "captions": [{"label": "outcome", "text": "A turn with tool effects and no text would still be `completed`, which is why PR 1 made outcomes explicit."}],
         "evidence": ev("What gets auto-scored", "pyrit/backend/services/message_send_service.py", start=r"def _is_scorable_response", lines=7, focus=[r'piece.role in \("assistant", "tool"\)', r"TargetOutcome.COMPLETED"], tag="PR #19"), "autoplayMs": 9000},
        {"id": "state", "kind": "replay", "tab": "State", "title": "The second message reuses the execution", "patch": {"replay": {"upto": 26, "score": None}},
         "body": "“Add one orange to the order” changes a file the agent itself wrote earlier: the total goes from 31 to 36. Same execution, same workspace, same journal; a stateful system under test, not a stateless endpoint.",
         "captions": [{"label": "lifetime", "text": "The execution ends on explicit close, idle expiry, its maximum lifetime, a turn deadline, or runtime shutdown."}], "autoplayMs": 8000},
        card("real1", "It is <em>real</em>: the permission moment", "The same flow in the actual Co-PyRIT UI, captured headlessly against this branch with the scripted agent and the local provider. Status strip, activity, the pending approval with **Allow once / Deny**, and the deadline.",
             eyebrow="Chapter 05 · real capture", image=f"{REAL}/03-permission-pending.png", alt="Co-PyRIT chat with a pending tool permission", note="`fixtures/real/capture.mjs` reproduces this capture; no credentials involved.", autoplay=9000),
        card("real2", "It is <em>real</em>: two turns later", "Second turn complete: `Receipt total: 36`, the stored tool-call pieces (“journal event 22 · 23”), and the honest footnote: *exposed ACP events retained; missing tool calls are undetermined, not false*.",
             eyebrow="Chapter 05 · real capture", image=f"{REAL}/05-second-turn.png", alt="Co-PyRIT chat after the second turn", note="The footnote is the projection's contract in one sentence.", autoplay=9000),
    ],
}

# ---------------------------------------------------------------------------------------------- admin's day
ADMIN = {
    "id": "admin", "index": 6, "short": "control panel · real captures", "accent": "mint", "track": "tour",
    "eyebrow": "Chapter 06 · the administrator's day",
    "title": "Create, gate, govern, clean up",
    "summary": "Who may create agent targets, what the defaults are, how executions are listed and closed, and what the catalog reveals.",
    "takeaway": "Every execution launches a process with credentials the server holds. Creation is **admin-only**, defaults are **safe**, resources are **visible and closable**.",
    "regions": ["copyrit", "recipes"],
    "legend": [{"swatch": "mint", "label": "Administrator surface"}, {"swatch": "red", "label": "Refused"}, {"swatch": "grey", "label": "Visible to everyone"}],
    "stage": {
        "canvas": {"w": 1500, "h": 470},
        "actors": [
            actor("form", "New target → Agent (ACP)", 0, 20, 470, tag="administrators only", cols=3, slots=[("m", "Model"), ("h", "Harness"), ("e", "Environment")]),
            actor("gate", "Creation gate", 0, 250, 470, tag="server-enforced", cols=1, slots=[("admin", "who"), ("shape", "input"), ("fixture", "fixtures")]),
            actor("defaults", "Safe defaults", 520, 20, 440, tag="new targets", cols=1, slots=[("policy", "permission policy"), ("local", "local provider"), ("ask", "ask")]),
            actor("registry", "Registry → Agent executions", 520, 250, 440, tag="resource view", cols=1, slots=[("state", "states"), ("acts", "actions")]),
            actor("settings", "Settings", 1010, 20, 490, tag="configuration", cols=1, slots=[("identity", "operator identity"), ("budget", "inference budget"), ("replace", "configuration replacement")]),
            actor("catalog", "What the catalog shows", 1010, 250, 490, tag="to every authenticated user", cols=1, slots=[("names", "shows"), ("never", "never")]),
        ],
        "edges": [],
        "initial": {},
    },
    "steps": [
        step("create", "Create", "One form, three sections, in that order",
             "*Model* picks a registered connection for BYOM or lets the harness authenticate itself. *Harness* and *Environment* offer registered presets; choosing one locks its fields and records the names as provenance, *Customize* copies the values and drops the provenance. Nothing in the form ever resolves a credential: credential fields hold variable **names**.",
             status={"form": "active"}, slots={"form.m": "registered target · wire API inferred", "form.h": "preset or custom · command · policy", "form.e": "preset or custom · provider · image"},
             captions=[{"label": "real screenshot", "text": "Next step shows the actual form."}], autoplay=9000),
        card("formshot", "The real <em>form</em>", "Captured from this branch: Model · Harness · Environment, *Ask me* selected for the demo, *Local process* with the acknowledgement that local tools can access the host.",
             eyebrow="Chapter 06 · real capture", image=f"{REAL}/01-create-form.png", alt="Create New Target form for Agent (ACP)", note="Preset dropdowns are empty here because the demo backend registers no presets; with initializers they list named profiles and templates.", autoplay=9000),
        step("gate", "Gate", "Admin-only, one validated object, confined fixtures",
             "`requires_admin_creation` is a class marker checked by the create-target service next to upstream's `code_execution_parameter` and `server_credential_parameter`. `agent_configuration` crosses REST as one JSON object because the model opts in with `JsonParameterValue`; exactly one parameter in the repository does. A fixture directory must resolve inside the server's `target_upload_directory`.",
             status={"form": "ok", "gate": "active"}, slots={"gate.admin": "administrators · requires_admin_creation", "gate.shape": "one pydantic model · JsonParameterValue", "gate.fixture": "inside target_upload_directory"},
             captions=[{"label": "why", "text": "Every execution launches a process or container with credentials the server holds, regardless of parameters."}],
             evidence=ev("AgentTarget · class markers", "pyrit/prompt_target/agent_target.py", start=r"requires_admin_creation: ClassVar", before=2, lines=6, focus=[r"requires_admin_creation", r"upload_directory_parameter"], tag="PR #17"), autoplay=10000),
        step("defaults", "Defaults", "Deny by default; local must be acknowledged",
             "New targets, in Python and in the UI, refuse tool permission requests. `allow_once` and `ask` are explicit choices; `ask` is only valid for interactive chat sends and fails fast for attacks. Choosing a provider that is not isolated requires ticking *I understand local tools can access the host*.",
             status={"gate": "ok", "defaults": "active"}, slots={"defaults.policy": "deny", "defaults.local": "explicit acknowledgement required", "defaults.ask": "interactive sends only"},
             captions=[{"label": "identity", "text": "Policy is part of the target's identity: deny-targets and ask-targets never share results."}],
             evidence=ev("HarnessProfile defaults", "pyrit/models/agent_execution.py", start=r"class HarnessProfile\(BaseModel\)", lines=10, focus=[r"permission_policy: AgentPermissionPolicy = AgentPermissionPolicy.DENY"], tag="PR #17"), autoplay=9000),
        step("executions", "Executions", "Executions are resources: listed, closable, reconciled",
             "`Registry → Agent executions` lists what the backend owns with its state (`starting · idle · working · held · closing · closed · cleanup_failed`), its profile and environment, and the last turn. Administrators can cancel a turn or close an execution, including retrying failed cleanup. Interrupted executions are reconciled on restart through the same provider path.",
             status={"defaults": "ok", "registry": "active"}, slots={"registry.state": "starting · idle · working · held · closing · closed · cleanup_failed", "registry.acts": "cancel turn · close · retry cleanup"},
             captions=[{"label": "observation", "text": "Watching an execution never renews its lifetime; only work does."}],
             evidence=ev("AgentExecutionState", "pyrit/models/agent_execution.py", start=r"class AgentExecutionState", lines=10, focus=[r'HELD = "held"', r'CLEANUP_FAILED = "cleanup_failed"'], tag="PR #17"), autoplay=9000),
        card("execshot", "The real <em>registry</em>", "Captured after the chat above: the `receipt_agent` execution idle on `local` with its last turn completed, next to an older, closed one. This is the administrator's resource view, separate from the chat.",
             eyebrow="Chapter 06 · real capture", image=f"{REAL}/06-executions.png", alt="Registry → Agent executions", note="Observation does not renew expiry; the idle execution closes on its idle timeout or when an administrator closes it.", autoplay=8000),
        step("settings", "Settings", "Three knobs worth knowing",
             "`show_agent_operator_identity` (default **show**) controls whether decisions name the operator or just the role. Per-execution inference budgets default to 100 requests and cap at 1,000. Live executions, including idle and held chats, block configuration replacement exactly as active sends and scenario runs do.",
             status={"registry": "ok", "settings": "active"}, slots={"settings.identity": "show (default) · or roles", "settings.budget": "100 default · 1,000 cap", "settings.replace": "blocked while any execution is live"},
             captions=[{"label": "presentation only", "text": "Masking identity changes nothing stored; the record and the admin view keep it."}],
             evidence=ev("Live executions block replacement", "pyrit/backend/services/runtime_lifecycle.py", start=r"def _has_active_work", lines=12, focus=[r"agents.has_live_executions"], tag="PR #22"), autoplay=9000),
        step("catalog", "Catalog", "What everyone can see, and what no one can",
             "The target catalog shows credential variable **names** and the resolved fixture path to every authenticated user: that is the authoring contract. Values never appear anywhere: not in the catalog, not in the journal (redacted before writing), not in conversation routes (which omit owner, provider and process identities).",
             status={"settings": "ok", "catalog": "active"}, slots={"catalog.names": "credential variable names · fixture path · presets", "catalog.never": "credential values · image digest · process identities (conversation routes)"},
             captions=[{"label": "declined review item", "text": "Hiding the names was considered and declined: operators need to know which variables a profile expects."}],
             evidence=ev("Masking operator identity", "pyrit/backend/routes/executions.py", start=r"def _operator_role", lines=18, focus=[r"_RUNTIME_ACTORS", r'return "operator"', r"def _mask_event_actors"], tag="PR #24"), autoplay=9000),
    ],
}

# ---------------------------------------------------------------------------------------------- extender's day
EXTENDER = {
    "id": "extender", "index": 7, "short": "checklist · live report", "accent": "mint", "track": "tour",
    "eyebrow": "Chapter 07 · the extender's day",
    "title": "Make PyRIT use my sandbox",
    "summary": "Implement four moments, run the conformance suite, register through setup; the same for a new harness.",
    "takeaway": "PyRIT does not own environments or harnesses. It owns the **contract** and the **suite that checks it**; an out-of-tree provider needed zero core changes.",
    "regions": ["extend", "seams"],
    "legend": [{"swatch": "mint", "label": "Contract · PyRIT"}, {"swatch": "green", "label": "Check passed"}, {"swatch": "grey", "label": "Your code"}],
    "stage": {"canvas": {"w": 1500, "h": 470}},
    "checklist": {
        "fixture": "conformance_local", "subject": "provider 'local' · real run", "contractTitle": "ExecutionEnvironmentProvider · the four moments",
        "moments": [
            {"id": "reserve", "title": "reserve()", "text": "Record deterministic resource identifiers before anything exists, so a crash mid-allocation stays reconcilable."},
            {"id": "endpoint", "title": "inference_endpoint_async(relay_url)", "text": "Translate the relay URL into your network namespace. Must not acquire resources."},
            {"id": "launch", "title": "launch_async(spec)", "text": "Seed the workspace, acquire resources, start the harness. Only variable names on command lines; values through your own channel."},
            {"id": "close", "title": "close_async()", "text": "Release exactly what you own, collect artifacts, stay idempotent, refuse anything you did not create."},
        ],
    },
    "initial": {"checklist": {"done": 0, "moments": []}},
    "steps": [
        {"id": "contract", "kind": "checklist", "tab": "Contract", "title": "Four moments, two byte streams", "patch": {"checklist": {"moments": ["reserve", "endpoint", "launch", "close"]}},
         "body": "A provider subclasses `ExecutionEnvironmentProvider` (an `info` description, template validation, `create_session`) and returns an `ExecutionSession` with four lifecycle methods and three streams: ACP reader, ACP writer, stderr. Nothing here speaks ACP; you move bytes and honour the moments.",
         "captions": [{"label": "policy stays in core", "text": "Timeouts, budgets, redaction and the protocol are not yours to implement, or to weaken."}],
         "evidence": ev("ExecutionSession · four moments", "pyrit/agent/environment.py", start=r"def reserve\(self\)", end=r"async def close_async", after=1, focus=[r"def reserve", r"async def inference_endpoint_async", r"async def launch_async", r"async def close_async"], tag="PR #17"), "autoplayMs": 10000},
        {"id": "run", "kind": "checklist", "tab": "Run", "title": "Run the conformance suite against a real allocation", "patch": {"checklist": {"done": 17}},
         "body": "`verify_environment_provider_async` drives one real allocation with a byte-echo command and reports one check per promise: streams unavailable before launch, relay translation, a secret that must not leak, idempotent release, honest capabilities. This report is the real one for the `local` provider, captured while building this deck: 17 of 17.",
         "captions": [{"label": "review", "text": "A provider PR is reviewed against this report; Docker's runs in integration tests."}],
         "evidence": ev("verify_environment_provider_async", "pyrit/agent/conformance.py", start=r"async def verify_environment_provider_async", lines=20, focus=[r"Drive one real allocation", r"resources, streams, secrets", r"One check per promise"], tag="PR #17"), "deeper": "seam/intro", "deeperLabel": "The seam in depth", "autoplayMs": 11000},
        {"id": "register", "kind": "checklist", "tab": "Register", "title": "Register through setup, never from the UI", "patch": {"checklist": {"done": 17, "animate": False}},
         "body": "Providers and adapters are trusted code registered by setup (`EnvironmentProviderRegistry.register`), not import strings accepted over REST. Templates then name your provider by its `info.name`, and the UI lists the template fields your provider declares.",
         "captions": [{"label": "ACA", "text": "The Azure Container Apps sandbox provider was built this way outside the repository with zero core changes; packaging waits on the preview CLI."}],
         "evidence": ev("EnvironmentProviderRegistry.register", "pyrit/agent/environment.py", start=r"    def register\(self, provider: ExecutionEnvironmentProvider\)", lines=12, focus=[r"def register", r"ValueError"], tag="PR #17"), "autoplayMs": 9000},
        {"id": "adapter", "kind": "checklist", "tab": "Harness", "title": "A new harness is an adapter, not a fork", "patch": {"checklist": {"done": 17, "animate": False}},
         "body": "`HarnessAdapter` declares `info` (name, default command, wire APIs, native credential names, reserved variables), `launch_environment` (how the harness finds its private home) and `inference_environment` (how it is pointed at the relay). `verify_harness_adapter` checks those promises without running anything. Copilot CLI and the Agent Framework host are the two shipped adapters; a generic ACP adapter covers the rest.",
         "captions": [{"label": "reserved", "text": "Process identity and lookup paths (HOME, PATH, …) can never be credential references."}],
         "evidence": ev("HarnessAdapter", "pyrit/agent/harness.py", start=r"class HarnessAdapter\(ABC\)", lines=20, focus=[r"EXECUTION_RESERVED_ENVIRONMENT", r'"HOME"', r'"PATH"'], tag="PR #17"), "deeper": "harnesses/intro", "deeperLabel": "Harnesses in depth", "autoplayMs": 10000},
    ],
}

# ---------------------------------------------------------------------------------------------- decisions explorable (deep) + limits (tour)
_D = [
    ("gating", "Admin gating", "Admin-only over REST", "Every execution launches a process or container with credentials the server holds, regardless of parameters, so `requires_admin_creation` is enforced next to upstream's `code_execution_parameter` and `server_credential_parameter`.",
     ev("class marker", "pyrit/prompt_target/agent_target.py", start=r"requires_admin_creation: ClassVar", before=2, lines=5, focus=[r"requires_admin_creation"])),
    ("input", "External input", "One opt-in object over REST", "Upstream's allowlist admits scalars, lists and registry references. `agent_configuration` is a single-shape pydantic model, so a sibling opt-in, `JsonParameterValue`, lets exactly one parameter arrive as an object; the 35 other model-typed parameters stay in-process and a negative test locks that in.",
     ev("opt-in", "pyrit/models/agent_execution.py", start=r"class AgentTargetConfiguration\(BaseModel, JsonParameterValue\)", lines=4, focus=[r"JsonParameterValue"])),
    ("terms", "Terminology", "“Tool calls”, not “evidence”", "Matches `MessageToolCallScorer` and the `function_call` piece types; upstream now uses “evidence” for findings.",
     ev("projection", "pyrit/agent/tool_calls.py", start=r"def project_agent_tools\(", lines=6, focus=[r"def project_agent_tools"])),
    ("ownership", "Ownership", "Policy in core, resources outside", "Timeouts, budgets, cancellation, approvals, redaction and ACP live in core; allocation, release, seeding and byte streams live in providers; adapters own launch and environment translation.",
     ev("contract", "pyrit/agent/environment.py", start=r"class ExecutionEnvironmentProvider\(ABC\)", lines=4, focus=[r"Trusted implementation code"])),
    ("identity", "Identity", "Hash of the recipe, minus policy", "`environment` is required; Docker-typed fields are validated by the Docker provider; `permission_policy` **is** identity.",
     ev("exclusions", "pyrit/prompt_target/agent_target.py", start=r"_IDENTITY_EXCLUDED_FIELDS: ClassVar", lines=6, focus=[r"_IDENTITY_EXCLUDED_FIELDS"])),
    ("deny", "Deny default", "Permissions default to deny", "`allow_once` and `ask` are explicit; `ask` only for interactive sends; a harness must offer `reject_once` before `ask` asks anyone, so a denial is never reported as a cancellation.",
     ev("default", "pyrit/models/agent_execution.py", start=r"permission_policy: AgentPermissionPolicy = AgentPermissionPolicy.DENY", lines=2, focus=[r"DENY"])),
    ("fixtures", "Confined fixtures", "Fixture dirs confined over REST", "API-created targets reuse upstream's `upload_directory_parameter` contract; Python callers are not confined.",
     ev("marker", "pyrit/prompt_target/agent_target.py", start=r"upload_directory_parameter: ClassVar", lines=2, focus=[r"allowed_fixture_directory"])),
    ("visible", "Operator identity", "Operator identity shown by default", "Decisions name the operator to everyone with access to the attack; `show_agent_operator_identity: false` reports roles. Presentation only.",
     ev("masking", "pyrit/backend/routes/executions.py", start=r"def _operator_role", lines=5, focus=[r'return "operator"'])),
    ("capture", "Bounded capture", "Bounded, redacted capture", "Optional inference content capture journals decoded text after the stream settles so a credential spanning chunks is redacted whole; budgets default to 100 requests, cap 1,000; live executions block configuration replacement.",
     ev("content capture", "pyrit/agent/inference_relay.py", start=r"async def _record_content_async", lines=12, focus=[r"inference.content"])),
]
DECISION_NODES = []
for k, (nid, label, title, claim, evidence) in enumerate(_D):
    col, row = k % 3, k // 3
    DECISION_NODES.append({"id": nid, "label": label, "title": title, "kind": "decision", "x": 20 + col * 280, "y": 10 + row * 100, "w": 260, "claim": f"**{title}.** {claim}", "facts": [], "evidence": evidence})

DECISIONS = {
    "id": "decisions", "index": 8, "short": "explorable", "accent": "mint", "track": "deep",
    "eyebrow": "Deep dive · decisions a reviewer should know about",
    "title": "Nine decisions, each with its line of code",
    "summary": "Hover a decision for the rationale and the exact lines that implement it.",
    "regions": ["*"],
    "steps": [
        {"id": "explore", "kind": "stack", "wide": True, "viewBox": "0 0 840 300", "svgHeight": 330, "title": "Nine decisions", "nodes": DECISION_NODES, "infoEyebrow": "Hover or focus a decision", "autoplayMs": 15000,
         "footer": "Two review observations were explicitly **declined**: the catalog shows credential variable **names** and the resolved fixture path to every authenticated user (documented as the authoring contract), and the Agent Framework host imports `pyrit.models` inside the tested process (lazy package inits keep that to a pydantic model import; the host is PyRIT code by design)."},
    ],
}

LIMITS = {
    "id": "limits", "index": None, "accent": "mint", "track": "tour", "eyebrow": "Limits", "title": "Known limits", "regions": ["*"],
    "steps": [
        card("limits", "Known <em>limits</em>", "Said plainly, so reviewers do not have to find them.",
             bullets=[
                 {"title": "Budgets are per execution", "text": "Default 100 inference requests, max 1,000; per-turn budgets are deferred."},
                 {"title": "Modality is declared, not policed per request", "text": "The binding declares input modalities; undeclared image parts are not yet rejected by the relay."},
                 {"title": "`local` is not a sandbox", "text": "Private home and workspace only; it requires an explicit acknowledgement."},
                 {"title": "Permission policy is not enforcement", "text": "It is a deterministic ACP answer; isolation comes from the environment."},
                 {"title": "Tool coverage is what the protocol exposes", "text": "Missing tool calls are undetermined, not false; the UI says so under every turn."},
             ], autoplay=11000),
        card("deferred", "Deferred <em>on purpose</em>", "Each has a place in the stack where it would land; none is needed to test an agent today.",
             bullets=[
                 {"title": "Agent configuration registry UI · create by preset name", "text": "After 6b. Does **not** replace the three-panel form."},
                 {"title": "Checkpoint save / restore", "text": "After 2b, as an optional Docker-provider capability."},
                 {"title": "Durable startup authoring · evidence playback", "text": "After 5 / after 6a. “Download journal” covers today's need."},
                 {"title": "Relay modality enforcement · per-turn budgets", "text": "After 4; both change evidence semantics."},
                 {"title": "Managed cloud providers (ACA)", "text": "Proven out of tree; packaging waits on the preview CLI/SDK."},
             ], autoplay=10000),
    ],
}
