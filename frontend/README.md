# PyRIT Frontend

Modern TypeScript + React frontend for PyRIT, built with Fluent UI.

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

## Conversation tree (preview)

Open **Conversation tree** in the sidebar (`/tree`). This workspace is for
authorized, human-directed evaluations of multi-turn text targets with editable
history. It reuses the existing attack, converter, message, and history APIs.

- **Workspace options:** the three-dot menu opens workspace settings and scoring.
  Choose breadth-first (BFS) or depth-first (DFS), an operation budget (default
  50), confirmation preferences, automatic additions, and the failure policy.
  Execution remains one request at a time; traversal and concurrency are separate
  concepts. The budget includes target sends, converter applications, and automatic
  scoring requests, not provider-internal retries or a token/dollar estimate.
  All-applicable-message scoring and composite scorers can make several scorer
  invocations within one scoring request.
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
  not follow-ups. Samples default to a collapsed stack; prompt/pipeline variant
  groups default to expanded. Select **Stack group / Expand group** to change
  either presentation. Stacking never prunes or merges evidence. Each member's
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
  Existing drafts and unrun ancestors are never included automatically; dependent
  branches remain drafts if their existing parent has not run. Errors and stopped
  runs do not resume themselves. It does not run on typing, selection, import, or reload.
  Explicit retries always follow the workspace's confirmation preference.
- **Keep and prune:** choosing a completed turn marks a human preference and
  hides its siblings from subsequent execution. This is not a scorer verdict.
  Pruning is reversible, includes descendants, and never deletes backend data.
  Use **Show pruned** and **Restore branch** to revisit alternatives.
- **Fork and cascade:** rewriting a historical turn clones its active subtree,
  preserves the original observations, and resets every cloned response to a
  draft. Replay regenerates descendant history in order. Arbitrary edge
  reconnection is intentionally disallowed: an edge means conversation history,
  not a shader-style data cable.
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
- **Layout:** fixed-size graph previews with stable positions. Responses and scores
  do not trigger automatic relayout or zooming. New nodes are placed near their
  parent without moving existing nodes. Dragging stays inside React Flow, with
  persistence at the end of the gesture. **Auto layout** explicitly rearranges
  cards; **Fit all** and **Focus selected** are separate viewport actions.
  State badges distinguish drafts, queued/running work, completed requests and errors.
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
  Each scoring pass appends results anchored to that attempt and scorer identity.
  A primary metric drives an output-only red/green meter, with explicit polarity
  and numeric labels. Unscored, inapplicable, undetermined, and failed evaluations
  stay neutral; multiple scores are not silently averaged.
- **Interruption:** keep the page open while running. Stop or navigation prevents
  subsequent requests but cannot cancel an in-flight provider call. Uncertain
  sends are not automatically retried: inspect their backend conversation and
  explicitly fork a new variant. **Recover recorded result** can reconcile a
  `running`/interrupted turn from backend history without another send. Target
  identity and cloned ancestor history are checked before execution, so a
  browser workspace cannot silently continue against a reset backend database.
- **Save failures:** received evidence remains available for inspection and
  export if its browser write fails. **Retry saving only** writes the retained
  snapshot without calling a model. Revision conflicts remain conflicts;
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

### Strategy plans and future agent assistance

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
when creating the workspace. Automatic conditional strategies, scoring-driven
pruning and an agent chat pane are intentionally not implemented in this preview.

The pure editing command model and revision-bound run proposals separate
suggestions from approval. A future agent can consume an evidence snapshot and
propose the same commands a human uses, subject to revision checks and explicit
approval. Autonomous branching algorithms should remain in PyRIT executors,
with judgments supplied by scorers, not duplicated in the UI.

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
