# PyRIT Frontend

Modern TypeScript + React frontend for PyRIT, built with Fluent UI.

## Appearance

The **Theme** menu at the bottom of the sidebar offers System, Light, Dark,
Raccoon, Jimothy, Pirate, Seattle Rain, Evergreen, Blueprint, and Night Sky.
Each named preset combines a fixed palette with a decorative workspace
background. Content panels remain solid for readability.

Your choice is saved in this browser. System follows the operating system's
light/dark preference. High-contrast mode overrides every palette and hides
decorations without forgetting the selected preset.

## Development

```bash
# Install dependencies
npm install

# Start both backend and frontend (cross-platform)
python dev.py start
# OR use npm script
npm run start

# Start backend only (with airt initializer by default)
python dev.py backend

# Start frontend only (backend must be started separately)
python dev.py frontend
# OR
npm run dev

# Restart both servers
python dev.py restart
# OR
npm run restart

# Stop all servers
python dev.py stop
# OR
npm run stop

# Build for production
npm run build

# Preview production build
npm run preview
```

### Backend CLI

The backend uses `pyrit_backend` CLI which supports initializers:

```bash
# Start with default airt initializer (loads targets from env vars)
pyrit_backend --initializers airt

# Start without initializers
pyrit_backend

# Start with custom initialization script
pyrit_backend --initialization-scripts ./my_targets.py

# List available initializers
pyrit_backend --list-initializers

# Custom host/port
pyrit_backend --host 127.0.0.1 --port 8080
```

**Development Mode**: The `dev.py` script sets `PYRIT_DEV_MODE=true` so the backend expects the frontend to run separately on port 3000.

**Production Mode**: When installed from PyPI, the backend serves the bundled frontend and will exit if frontend files are missing.

## Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Fluent UI v9** - Microsoft design system
- **Vite** - Fast build tool
- **Axios** - HTTP client

## Testing

```bash
# Unit & Integration Tests (Jest + React Testing Library)
npm test              # Run all tests
npm run test:watch    # Watch mode for development
npm run test:coverage # Run with coverage report (85%+ threshold)

# End-to-End Tests (Playwright)
npm run test:e2e          # Run headless (auto-starts frontend + backend via dev.py)
npm run test:e2e:headed   # Run with visible browser windows (requires display)
npm run test:e2e:ui       # Interactive UI mode (requires display)
```

Jest's shared setup in `src/setupTests.ts` supplies the minimal layout signals
Fluent UI needs for dialog focus. No per-suite layout mocks are needed. Hidden
and detached elements remain excluded. Await role queries after dialog
transitions, including when returning to background controls. This is not a
layout engine; use Playwright for assertions about element dimensions or positioning.

### E2E Test Modes

E2E flow tests run in two modes controlled by Playwright projects and an environment variable:

- **Seeded** (`--project seeded`, default for CI): Messages are stored directly in the database with `send: false` using dummy credentials. No real API keys needed. Tests cover the full UI flow (display, branching, conversation switching, promoting) without calling any external service.

- **Live** (`--project live`, requires `E2E_LIVE_MODE=true`): Messages are sent to real OpenAI endpoints with `send: true`. Each target variant requires endpoint and model environment variables plus either an API key or an Azure endpoint accessible through the current Entra identity. Variants without a usable configuration are automatically skipped. Tests verify that real target responses render correctly.

```bash
# Seeded integration (no credentials needed)
npx playwright test --project seeded

# Live integration (uses API keys when present, otherwise Entra authentication)
E2E_LIVE_MODE=true npx playwright test --project live

# Run both
E2E_LIVE_MODE=true npx playwright test
```

The mock and seeded projects run in the **GitHub Actions** pull-request workflow. The live project is intended for a protected pipeline with an Entra identity or API keys.

E2E tests use `dev.py` to automatically start both frontend and backend servers. If servers are already running, they will be reused.

> **Note**: `test:e2e:ui` and `test:e2e:headed` require a graphical display and won't work in headless environments like devcontainers. Use `npm run test:e2e` for CI/headless testing.

## Configuration

The frontend proxies API requests to `http://localhost:8000` in development.
Configure this in `vite.config.ts` if needed.

## Adding a theme preset

The catalog in `src/themes/themePresets.ts` is the source of truth for preset
IDs, labels, palettes, backgrounds, menu entries, and stored-value validation.

1. Draw a new, self-contained SVG in `public/backgrounds/`. Use a transparent
   background and keep prominent artwork away from the upper-left reading area.
   Do not embed scripts, external resources, fonts, or raster images.
2. Add one entry to `THEME_PRESETS`, using a unique, stable ID. For example:

   ```ts
   'my-background': {
     label: 'My Background',
     resolved: 'light',
     theme: webLightTheme,
     background: {
       imageUrl: '/backgrounds/my-background.svg',
       opacity: 0.08,
     },
   },
   ```

3. For a coordinated palette, follow a nearby preset's `createPaletteTheme`
   definition instead of changing colors in individual components. Keep
   `resolved` consistent with the palette's light/dark base. Its status
   foregrounds cover custom surfaces while preserving Fluent's semantic
   backgrounds and borders.
4. Document how the artwork was made and keep the palette accessibility tests
   passing. They check neutral/status text and button contrast, including the
   strongest possible artwork at the configured opacity, plus semantic
   foreground/background pairs used by badges and messages.

No hook, menu switch, or page-specific background needs to be added for a new
preset. Existing page canvases share one decorative layer; controls, dialogs,
cards, tables, and message bubbles continue using opaque Fluent UI tokens.
An unknown or removed stored preset returns to System.

### Background artwork provenance

All seven SVGs in `public/backgrounds/` were newly drawn from scratch for this
change with Copilot assistance and are provided under this repository's MIT
license. No artist's illustration, photograph, or stock wallpaper was copied,
traced, vectorized, or used as image-generation input.

The Jimothy drawing uses the real Seattle raccoon's distinctive compact,
rounded appearance. [Know Your Meme](https://knowyourmeme.com/memes/jimothy-the-raccoon)
and [Wikipedia](https://en.wikipedia.org/wiki/Jimothy_(Raccoon)) were consulted
for factual descriptions only. Their displayed artwork and photographs were
not reused. The existing CoPyRIT logo is unchanged.

## Workspace (preview)

Open **Workspace** in the sidebar (`/tree`). This workspace is for
authorized, human-directed evaluations of multi-turn text targets with editable
history. It reuses the existing attack, converter, message, and history APIs.

- **Workbench:** the graph stays central, with **Branches** hidden by default.
  Toggle the outline when needed. Both **Inspect / Assistant** detail panes are
  enabled by default on wide screens when Assistant is open; use the workspace
  menu to switch to one detail pane. Narrow screens use **Graph**,
  **Inspect**, or **Assistant** without unmounting drafts or conversation state.
- **New workspace:** choose a first prompt, an objective-only/empty workspace,
  or an imported plan in the same dialog. Creation never sends target requests.
  An empty workspace can be populated manually or by the assistant; target
  identity and system prompt are still chosen at creation.
- **Workspace options:** the three-dot menu collects new/import/export, display,
  undo/redo, workspace settings and scoring instead of competing with the graph.
  Choose breadth-first (BFS) or depth-first (DFS), an operation budget (default
  50), confirmation preferences, automatic additions, and the failure policy.
  Traversal and concurrency are separate choices (1, 2, or 4 requests).
  BFS finishes a level, including automatic scoring, before advancing.
  Judge scoring is serialized. The budget includes target sends, converter applications, and automatic
  scoring requests, not provider-internal retries or a token/dollar estimate.
  All-applicable-message scoring and composite scorers can make several scorer
  invocations within one scoring request.
  Parallel dispatch is enabled for the canonical OpenAI chat/response adapters;
  other adapters remain sequential, with the effective concurrency shown in Activity.
- **Confirmations:** the run modal offers **Don't ask again in this workspace**.
  This preference does not enable auto-run. Restore confirmations in workspace
  settings. Imported plans never change spending or approval preferences.
- **Prompt variants (Prompts):** add, duplicate, edit, or remove individual prompt
  cards. Each creates a child of the selected node and continues from its actual
  response. There is no delimiter syntax or sampling multiplier.
- **Converter fan-out (Pipelines):** enter one shared child prompt and compare
  complete converter pipelines. Each pipeline creates one child. Add, edit,
  reorder or remove steps, select typed parameters, or duplicate an entire
  pipeline. An empty pipeline is an unconverted baseline. LLM-backed converters
  have a registered **Rewrite target** selector (or backend default).
- **Sample again:** beside the response, request 1-20 additional samples of the
  saved prompt and pipeline using the same preceding history. These are comparisons,
  not follow-ups. The first sampling operation includes the original card in the
  stack; subsequent sampling extends it instead of nesting stacks.
  Sampling a member of a variant comparison separates that member into its own
  sample stack; unrelated conversation directions are not mixed into it.
  Samples default to a collapsed stack; prompt/pipeline variant groups default to
  expanded. **Focus group** isolates a comparison with its history and descendants;
  **Back to workspace** restores the overview viewport. **Stack group** collapses the
  comparison; **Expand & auto layout** explicitly rearranges the overview to prevent
  overlapping previously paged members. Stacking never prunes or merges evidence. Each member's
  descendants remain tied to that member; selecting a hidden member in the outline
  reveals its path. Repeated inputs may return identical responses.
- **Retry response / Retry subtree:** reuse the same logical cards and positions,
  creating fresh internal attempts rather than visible sibling variants. Earlier
  successes, errors, and scores remain accessible through the **Attempt** selector.
  Descendants of a retried node become drafts because their old responses belong
  to the old parent attempt. Retry response executes only the selected node;
  retry subtree replays active descendants. Use **Fork & cascade** to make a
  separate comparison branch. Recover an interrupted `running` node before retrying.
- **Auto-run new branches:** off by default and enabled explicitly for the current
  workspace/session. Newly added child branches, samples and forks run
  without another confirmation, within the configured operation budget.
  Approved agent additions use the same preference: the approval preview shows
  the exact new drafts and effective cost before sending. Explicit draft-only
  proposals override the preference. Receiving a proposal alone never executes it.
  Existing drafts and unrun ancestors are never included automatically; dependent
  branches remain drafts if their existing parent has not run. Errors and stopped
  runs do not resume themselves. It does not run on typing, selection, import, or reload.
  Explicit retries always follow the workspace's confirmation preference.
- **Keep and prune:** choosing a completed turn marks a human preference and
  hides its siblings from subsequent execution. This is not a scorer verdict.
  Pruning is reversible, includes descendants, and never deletes backend data.
  Use **Show pruned** and **Restore branch** to revisit alternatives.
  Pruned branches have dashed, subdued cards and an explicit badge; kept choices
  have a separate accent, independent of their model score.
- **Fork and cascade:** rewriting a historical turn clones its active subtree,
  preserves the original observations, and resets every cloned response to a
  draft. Replay regenerates descendant history in order. Arbitrary edge
  reconnection is intentionally disallowed: an edge means conversation history,
  not a shader-style data cable.
- **Fork path from ancestor:** choose a starting ancestor and ending descendant,
  preview that inclusive path, and rewrite its starting prompt/pipeline. Only
  those nodes are cloned as fresh drafts, with original provenance retained;
  side branches and descendants beyond the endpoint are excluded. The unchanged
  history preceding the starting node is shared. Auto-run, when enabled, applies
  only to the new path after its existing prefix is ready.
- **Persistence:** graph state, layouts and evidence snapshots live in this
  browser's local storage. Each executed turn also has a backend attack and
  conversation with source-message lineage, accessible from its inspector.
  Browser workspaces are not server-synchronized or shared across devices.
  Storage errors and revision conflicts are surfaced rather than silently
  discarding changes. HTTPS or localhost with Web Locks is required for writes.
- **Workspace history:** new backend attacks and their sent messages carry
  `pyrit_tree_id=<workspace ID>` and `pyrit_tree_node_id=<node ID>` in addition to
  configured labels. **Workspace history** opens the existing history view
  filtered by the workspace ID. Older untagged backend records are not modified;
  new attempts in an older workspace receive these labels too. Cloned history
  retains its original message provenance.
- **Layout:** compact, standard or expanded previews, plus per-card resize handles
  on the selected card. Sizes persist with the workspace; expanded cards provide
  scrollable previews. Choose curved Bezier, rounded-step or straight edges in settings.
  Responses and scores
  do not trigger automatic relayout or zooming. New nodes are placed near their
  parent without moving existing nodes. Dragging stays inside React Flow, with
  persistence at the end of the gesture. **Auto layout** explicitly rearranges
  cards; **Fit all** and **Focus selected** are separate viewport actions.
  State badges distinguish drafts, queued/running work, completed requests and errors.
- **Undo and activity:** undo/redo the last 20 local draft, pruning, size or layout
  edits in this session. Undo never erases provider attempts or rewrites observed
  prompts, and rejects conflicts with newer edits. Activity retains the outcome of
  the most recent run, including its attempt IDs, rather than recounting later
  node states. Stopping settles in-flight requests before ending the run.
- **Backend continuations:** selecting an observed node or returning to the tree
  checks for appended backend exchanges. **Check backend history** refreshes manually.
  Preview and explicitly import complete appended turns; incomplete exchanges remain
  pending. Imports never resend prompts, fabricate responses, or silently replace
  changed evidence. Each imported child remains anchored to its recorded exchange.
- **Editing while running:** other draft subtrees remain editable. Editing a queued
  draft removes that branch from the approved queue; run it again explicitly when
  ready. In-flight execution inputs and observed evidence remain protected.
  Serialized node-specific updates preserve unrelated edits when a response arrives.
- **Response inspector:** Prompt, converter pipeline, final recorded **Sent prompt**
  (only for converter-backed attempts), then Response. Multi-step pipelines show
  the final recorded payload even when it equals the original input. The Markdown
  toggle reuses chat's renderer; raw evidence is retained.
- **Scoring:** select/configure canonical PyRIT scorers, choose their evidence scope
  and judge target, and provide an evaluation objective in **Scoring**. Score an
  individual response, subtree, all eligible responses, or automatically after each
  attempt. Scoring reads persisted backend evidence and never resends target prompts.
  Each scoring pass appends results anchored to that attempt and scorer identity,
  with exact response IDs and sequence bounds so later backend appends cannot change
  which response is evaluated. Nested scorers receive the same checked, in-memory
  snapshot instead of re-reading mutable message content during resolution.
  A primary metric drives an output-only red/green meter, with explicit polarity
  and numeric labels. Unscored, inapplicable, undetermined, and failed evaluations
  stay neutral; multiple scores are not silently averaged.
  Workspaces without scorers do not display a score placeholder.
  New LLM scorers require an explicit registered judge target in the form.
  For `SelfAskScaleScorer`, supply the judge's `system_prompt` and a `scale`
  JSON object, for example `{"minimum_value":0,"maximum_value":10,"category":"math"}`.
  These are separate from the evaluation objective. Numeric range/rubric inputs
  are validated into canonical PyRIT models. Required Python-only objects are
  reported rather than silently omitted; configure those scorers in an initializer.
- **Scorer composition:** nested parameters accept a compatible registered scorer
  or an inline configuration, including ordered lists of sub-scorers. Construction
  is bounded to six levels and 32 inline components. Only the completed root
  configuration is registered. **Validate configuration** constructs the same
  configuration without registering it or sending judge requests.
  Structured fields expose their schemas and supported Text/JSON/YAML modes.
  Supported adapters include text `SeedPrompt` values with name/metadata,
  numeric ranges/rubrics, Likert scales, true/false questions, content classifiers,
  JSON schemas, dictionaries, string sets, validators/response handlers and
  named built-in aggregators. YAML is parsed as data, not executed: arbitrary
  file paths, Python callables and arbitrary `from_yaml` methods are not invoked.
  Uploaded SeedPrompt content cannot opt into trusted Jinja evaluation.
- **Interruption:** keep the page open while running. Stop or navigation prevents
  subsequent requests but cannot cancel an in-flight provider call. Uncertain
  sends are not automatically retried: inspect their backend conversation and
  determine the outcome before retrying. **Recover recorded result** can reconcile a
  `running`/interrupted turn from backend history without another send. Target
  identity and cloned ancestor history are checked before execution, so a
  browser workspace cannot silently continue against a reset backend database.
- **Save failures:** received evidence remains available for inspection and
  export if its browser write fails. **Retry saving only** writes the retained
  snapshot without calling a model. Editing locks immediately while in-flight
  requests settle; recovery retains their responses and scores alongside any
  already-saved same-session edits. Revision conflicts remain conflicts;
  recovery never overwrites another tab. Export before discarding an unsaved
  snapshot. Unsaved inspector edits block both subtree and global run approval.
  Sidebar navigation and browser Back retain the tree's unsaved editor and
  recovery state for the lifetime of the app; leaving the tab stops subsequent
  sends. Closing or reloading the app still requires saving/exporting work first
  (the browser's leave warning is enabled when work is unsaved).
- **Export:** export a portable strategy plan or a full evidence snapshot.
  Both may contain sensitive evaluation content. Plan import creates fresh
  drafts; evidence snapshots are for inspection, not importing as trusted
  execution history. No credentials belong in converter parameters.

### Tree assistant (preview)

Open **Assistant** in a tree workspace to start or resume a planning conversation.
The assistant uses **Microsoft Agent Framework on the backend**, with a separately
configured planning model. It does not reuse the selected attack target.

The interaction is deliberately **inspect → propose → approve → apply → report**:

- Read tools inspect the saved tree, node evidence, and available components.
  **No human approval is needed for these read-only calls.** Tree context is deferred:
  a compact outline provides topology, status and short prompt previews; node and
  evidence tools retrieve details when needed instead of embedding the whole tree
  in each planning message. The full workspace objective is available through
  bounded, paginated reads, so constraints beyond its preview remain accessible.
  Tools read saved state, not unsaved editor text.
- Mutation tools stage typed proposals for additions, draft edits, child variants,
  samples, forks, retries, keeping and pruning. They never write the tree themselves.
- Run and scoring proposals name an explicit node set and show planned operations.
  The existing execution engine enforces history, scorer identity, budgets,
  cancellation, and evidence recovery.
- By default, **every proposal requires approval**, independent of the workspace's
  manual-run confirmation setting. New agent drafts inherit workspace auto-run
  unless the action explicitly requests drafts only. Plans and edit batches show
  whether approval also executes their new drafts; the saved effective policy
  and costs are validated again before application.
- **Multi-level plans** describe ordered steps with a local step ID and an explicit
  existing-node or earlier-step parent reference. The whole plan is validated and
  added atomically. Cycles, forward references, unknown parents and oversized plans
  are rejected. Running a plan sends only its new nodes; it never silently includes
  an unrun ancestor.
- Proposals carry host-captured semantic preconditions. Arranging cards or changing
  display preferences does not invalidate approval; changes to prompts, topology,
  evidence, targets, scorers or execution policies require re-planning. Every save
  still compares the current storage revision and retains the latest layout.
  The editor temporarily locks while approved mutations are being saved, so a
  newly selected node cannot discard text entered during that commit.
- Results are reported back to the session. If reporting fails after local execution,
  **Retry reporting result** resends only the receipt, never the tree mutation or run.
  If the backend session has expired, **Discard unreported receipt** explicitly
  detaches the chat without undoing or repeating the finished action.
  Sending another chat message explicitly starts the next planning turn.
  Scoring receipts distinguish completed evaluations from non-applicable scorers;
  an evaluation producing no scores is not reported as successfully scored.

Install the backend's optional `pyrit[tree_assistant]` extra and configure these
variables in the backend process:

| Variable | Purpose |
| --- | --- |
| `PYRIT_TREE_ASSISTANT_MODEL` | Tool-capable planning model or deployment name |
| `PYRIT_TREE_ASSISTANT_API_KEY` | Server-side credential for that model |
| `PYRIT_TREE_ASSISTANT_API` | `chat_completions` (default) or `responses` |
| `PYRIT_TREE_ASSISTANT_BASE_URL` | Optional OpenAI-compatible base URL, ending in `/v1` (not a specific API route) |

Set `PYRIT_TREE_ASSISTANT_API=responses` for deployments requiring the Responses
API. The assistant then sends to `/v1/responses`; the default continues to use
`/v1/chat/completions`. Tool calling and explicit proposal approvals work with
either transport. Restart the backend and start a new assistant session after
changing the setting. Both transports use `store=false` and retain conversation
history locally in the ephemeral session, rather than relying on stored response IDs.
For the local Azure-auth proxy, use `http://127.0.0.1:4000/v1` as the base URL and
ensure the proxy forwards `/v1/responses` when Responses mode is selected.

Planning-provider credentials stay on the backend; chat requests do not configure
them. Existing backend authentication applies to session endpoints. Without authentication,
opaque session IDs act as capabilities; use that mode only for a trusted local
development environment.

Chat checkpoints are versioned and saved in **localStorage per workspace**, including
the transcript, draft text, completed tool traces, context summaries, proposal
results and any unresolved operation. **Resume session** reconnects to a live backend
session; if it has expired, it restores conversation history into a fresh session.
Restored history is data, not executable tool calls or authoritative system policy.
Old pending proposals from an expired session must be re-planned.
Up to the latest 50 conversation turns restore model context; older turns remain in
a local archive and in chat exports. Prior tool payloads and server instructions
are not re-injected as model history. **Restart with context** rotates the backend
session while retaining the conversation; it never replays actions or grants.
Model context has a separate byte budget, with a visible restoration notice when
fewer recent turns fit; the local transcript is not deleted.
The local archive is bounded to 1,000 turns and the checkpoint to 2 MiB; reaching
either limit pauses chat for explicit export/recovery, not silent deletion.

Saving an action's execution marker happens before applying it. If reload interrupts
an action, the UI requires review of the tree/backend outcome rather than guessing
whether it finished or replaying it. Checkpoint conflicts and quota failures stop
new actions and preserve an exportable local snapshot.
If the checkpoint has reached its storage limit, **Export and clear local chat**
requests a download of the retained transcript. Separately confirm that you saved
the file before the checkpoint is removed; cancel if the browser blocked or canceled
the download. Clearing never replays or undoes an action. Review any exported
interrupted-action or unreported receipt records before beginning again.
Only one tab can write a workspace's chat at a time: a Web Locks lease protects
the transcript and action journal. A second tab is read-only until the first is
closed and the second reloaded.

Backend sessions remain process-local; a single worker (or session affinity) is
required while a session is live. Browser storage is not encrypted or cross-device
synchronization and may contain sensitive evaluation content. Provider credentials
and authentication headers are not included in telemetry. Text you enter is
preserved, so do not paste secrets into chat or evaluation prompts. **Export chat** downloads a basic
versioned JSON transcript, tools, context and results without the live session token.
Idle sessions expire after one hour. The process admits at most 64 sessions,
50 fresh completed turns per session (restored turns do not consume this allowance),
and one in-flight turn per session. Each turn has
a 90-second timeout, at most 16 tool invocations, bounded tool output, and bounded
history; assistant-model calls have their own limits, separate from target-run budgets.

**Tool/context details** on each completed turn show tool arguments, returned data,
status, timing and truncation; model/transport, selected node, workspace revision,
instructions and available tool names; and token usage when supplied by the provider.
These are observable execution records, not hidden model reasoning.

#### Inspection tools and limits

All inspection calls are approval-free. They share **16 tool calls and 64,000
payload bytes per planning turn**, with at most six SDK tool-loop iterations,
75 seconds for that loop, and a 90-second outer request timeout. These limits are
separate from the target/converter/scorer operation budget.

| Tool | Context returned |
| --- | --- |
| `inspect_tree` | Compact paged topology and previews; effectively pruned branches are omitted unless requested. |
| `inspect_selected_node` | The actual selection captured for this planning turn, or an explicit no-selection result. |
| `inspect_subtree` | Parent-first subtree pages, with `include_pruned`, `max_depth`, and `detail="summary"` or `"nodes"` controls. |
| `inspect_node` | One node from the saved browser snapshot, with continuation for oversized content. |
| `inspect_objective` | Lossless pages of the full workspace objective. |
| `inspect_evidence_async` | Verified backend text evidence through the node's recorded cutoff; bounded pieces and previews are marked. |
| `converter_catalog_async` / `scorer_catalog_async` | Available types or safe metadata for one type. |
| `registered_scorers_async` | The workspace's selected registered scorer identities. |
| `propose_action` | A typed proposal only, never direct execution. |

Subtree filtering reports counts and depth/pruning information. Omitting a pruned
ancestor also omits its descendants; reading a pruned branch never restores it.
Node detail is still snapshot data, not independently verified backend evidence.
For an oversized node, concatenate `node_chunk.text` across `next_cursor` pages
and JSON-decode the result. Cursors bind the saved snapshot and inspection options,
so changes require restarting the read rather than mixing snapshots. A complete
subtree may take several pages or planning turns; the assistant must not describe
a partial result as the whole subtree.

### Auto mode

Use the normal chat composer and enable **Auto mode** for a task. Submitting opens
a budget modal before any planning or execution starts. The default scope is the
whole workspace; optionally restrict it to the selected subtree. The submitted
message, scope and semantic workspace state are captured for confirmation, rather
than retargeting if the selection changes. Within that task the assistant may
apply validated proposals without repeated approval.
The orchestrator submits actions to the workspace pipeline instead of asking
for additional permission in chat. Tool responses distinguish submission from
completion; execution receipts remain the source of action outcomes.

- The permission applies only to that submitted task, not future messages.
  Whole-workspace scope can create roots in an empty workspace. For subtree scope,
  root-level sample/fork/keep operations that affect outside siblings are rejected.
- The UI selection and execution scope are separate: read tools report the
  actual selection captured for the planning turn, not the granted root.
- Target sends, converter applications and scoring requests consume the grant's
  operation budget; there is also a ten-turn planning cap. Workspace per-run budgets
  still apply. Provider-internal retries/composite calls and tokens are not an exact
  monetary budget.
- Stop pauses after in-flight work settles. Human edits that invalidate a proposal,
  failed actions, reporting errors and persistence failures pause exploration.
- Reload/restoration **never rearms Auto mode execution**. Confirm a new task
  budget to resume automatic work. The toggle is off after reload.

### Assistant session API

The application API is independent of Agent Framework's internal message format:

| Endpoint | Behavior |
| --- | --- |
| `POST /api/tree-assistant/sessions` | Create a workspace-bound session, optionally restoring bounded history, without a model call |
| `GET /api/tree-assistant/sessions/{id}` | Read completed chat turns and proposal results |
| `POST /api/tree-assistant/sessions/{id}/messages` | Run one bounded planning turn using saved context and an idempotency ID |
| `POST /api/tree-assistant/sessions/{id}/proposals/{proposal_id}/result` | Record an applied/rejected/failed client receipt without a model call |
| `DELETE /api/tree-assistant/sessions/{id}` | Discard ephemeral session state |

The agent has no shell, arbitrary Python, filesystem, generic HTTP, or direct
attack-send tool. Proposal tools reuse a deliberately restricted subset of the
tree command contract. Approval and execution stay outside the LLM tool loop.
Streaming transport, a server-side durable session store and richer multi-agent
strategies can be added behind these boundaries rather than creating a second
tree mutation engine.

Run the deterministic full-stack assistant tests after installing the optional
backend extra and frontend dependencies:

```bash
cd frontend
npm run test:e2e:assistant
```

This suite starts isolated backend/frontend servers and a local OpenAI-compatible
model fixture. Agent Framework sessions, tool invocation, APIs, tree mutation,
and attack evidence remain real; no remote model credentials are needed.
Set `PYRIT_E2E_PYTHON` to select a Python executable with the extra installed.
`PYRIT_E2E_BACKEND_PORT` and `E2E_FRONTEND_PORT` override the dedicated test ports.
On narrow screens the **Assistant** toggle switches between chat and the tree
panes so the message composer remains accessible.

### Strategy plan format

A "seed vector" is represented as a **versioned declarative plan**, not an
embedding or executable skill file:

```json
{
  "schemaVersion": 1,
  "name": "Multi-turn probe",
  "steps": [
    {
      "id": "context",
      "parentId": null,
      "prompt": "Describe your intended use and limitations.",
      "converters": []
    },
    {
      "id": "example",
      "parentId": "context",
      "prompt": "Give a harmless example illustrating those limitations.",
      "converters": []
    }
  ]
}
```

Plans are bounded to 300 nodes. Parent references encode sequences and branches;
converter entries contain `type` and `params`. Import validates the schema and
graph and does not execute code. Target, labels and system prompt are selected
when creating the workspace. The assistant can propose multi-level draft plans
and explicitly requested execution, using the same commands as human editing.
Plans do not encode executable conditions or implement research attack algorithms.
Reusable autonomous attack algorithms belong in PyRIT executors, with judgments
supplied by scorers rather than inferred from response wording in the UI.

This design draws on:

- [TAP](https://arxiv.org/abs/2312.02119): branching and pruning are distinct
  operations; preserve alternatives and their provenance.
- [PAIR](https://jailbreaking-llms.github.io/): iterative prompt refinement
  benefits from explicit candidate versions and response evidence.
- [Crescendo](https://crescendo-the-multiturn-jailbreak.github.io/): the preceding
  dialogue matters; editing an ancestor requires fresh downstream observations.
- [Best-of-N](https://jplhughes.github.io/bon-jailbreaking/): sampling variants
  makes request budgets and comparison important. Identical retries alone do
  not implement the paper's augmentation-based method, and deterministic targets
  may return identical results.

These sources motivate interaction primitives, not claims of effectiveness
against a particular target. The workspace does not automatically run these
research algorithms or infer success from response wording.
