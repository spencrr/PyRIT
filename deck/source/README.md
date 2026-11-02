# PyRIT · agent targets — interactive deck (v2)

An interactive, animated walkthrough of the PyRIT agent-testing design behind drafts
[#15–#25 on `spencrr/PyRIT`](https://github.com/spencrr/PyRIT/pulls) and the
[story gist](https://gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90).
Built the way the [KYLN example](../kyln/) and the [code-to-deck guide](../../GUIDE-code-to-interactive-deck.md) prescribe:
deck as data, one stateful stage per chapter, claim + pinned evidence, two semantic accents.

v2 maps the design doc's **pitch** onto visual form and serves two audiences from one artifact:

| Track | Who | What | Length |
| --- | --- | --- | --- |
| **Tour** (`?track=tour`, default) | anyone | cold open → the pitch (four claims) → *everything is swappable* (live recipe builder with a real SHA-256 identity hash) → the map → a day in the life of an **operator** (Co-PyRIT replay driven by a real journal + real screenshots), an **administrator** (control panel + real screenshots) and an **extender** (contract + a real conformance report ticking through) → limits → the PR stack → how to review | 44 steps, ≈ 6 min autoplay |
| **Deep dive** (`?track=deep`, or press `T`) | maintainers, reviewers | everything above **plus** the mechanism chapters with pinned code: one turn end to end, the environment seam, journal → pieces, the inference relay, identity, approvals, harnesses, and a *decisions* explorable (hover a decision → rationale + the exact lines) | 91 steps, ≈ 15 min |

A **minimap** in the header shows which region of the architecture each chapter zooms into; dotted `terms` have glossary tooltips; every tour step that has a mechanism behind it carries a *Go deeper →* link into the deep track.

## What is in here

| Path | Purpose |
| --- | --- |
| `deck_tour.py` | Tour chapters, glossary, minimap regions, decisions explorable, limits. |
| `deck.py` | Deep-dive chapters (mechanism), stack explorable, title, epilogue, and the `DECK` assembly. `build.py` emits everything as `deck.json`. |
| `deck_helpers.py` | Small constructors (`actor`, `edge`, `step`, `card`, `ev`, `packet`). |
| `template.html` | The engine: tokens, layout zones, reducer, tracks, minimap, glossary, stage renderer (cards, slots, measured SVG edges with rounded routes, packets, camera pans), and the special kinds: hero, agenda, card, stack/decisions explorable, **recipe** builder, Co-PyRIT **replay**, conformance **checklist**. Pure JS, no network, one file. |
| `build.py` | Resolves every evidence panel with `git show <ref>:<file>` from the PyRIT worktree using **anchor regexes** (start / end / focus), compacts snippets around the focus lines, summarizes the replay journal, inlines the conformance report and the real screenshots, records the pinned SHA, writes `dist/index.html`. Fails on any unresolved anchor. |
| `fixtures/` | Real artifacts: `events.jsonl` / `record.json` / `projection.json` / `receipt.json` (allow_once run), `ask_events.jsonl` / `ask_record.json` / `ask_turns.json` (interactive `ask` run with an operator decision, 31 → 36), `conformance_local.json` (17/17), and `generate.py` / `generate_more.py` to re-capture them. `fixtures/real/` holds Co-PyRIT screenshots captured headlessly by `capture.mjs` against this branch (scripted agent, local provider, no credentials). |
| `export.mjs` | Playwright export: PNG per step with a layout-overflow check (`--track tour|deep`), or an autoplay recording (`--video --name … --dwell … [--steps …]`). |
| `handout.py` | Builds `dist/handout.pdf` from the tour PNGs. |
| `dist/` | Build output: `index.html`, `png/` (deep = all steps), `png-tour/`, `trailer.mp4`, `tour.mp4`, `deep-dive.mp4`, `handout.pdf`. |

## Build and export

```powershell
python build.py
node export.mjs --track tour          # dist/png-tour + overflow warnings
node export.mjs --track deep          # dist/png (all steps)
node export.mjs --video --track tour --name tour --dwell 8000
node export.mjs --video --track deep --name deep-dive --dwell 9000
node export.mjs --video --track tour --name trailer --dwell 6000 --steps 0,2,3,4,5,7,8,11,13,16,21,22,25,36,41,43
ffmpeg -i dist/tour.webm -c:v libx264 -pix_fmt yuv420p -crf 23 -movflags +faststart dist/tour.mp4
python handout.py                     # dist/handout.pdf from the tour PNGs
```

Playwright is taken from the PyRIT frontend's `node_modules` (`PLAYWRIGHT_ROOT` overrides the path).
Re-capturing fixtures and screenshots needs the agent-stack branch checked out with the `agents` extra
(`fixtures/generate.py`, `fixtures/generate_more.py`, `fixtures/real/capture.mjs`; the latter expects the demo backend on `:8000` and Vite on `:3000`).

## Using the deck

- `←` `→` steps · `Tab` / `Shift+Tab` chapter · `T` switch track · `Space` autoplay (progress ring) · `E` toggle evidence ·
  `P` presenter mode · `Esc` overview (with the track toggle) · `Home` / `End`.
- Deep links: `#/<chapter>/<step>` (a link into deep-only content switches the track). Query flags: `?track=tour|deep`, `?mode=present`, `?play=1`, `?motion=off`.
- In the recipe builder, `‹ ›` swaps a part and the hash recomputes; in the replay, **Allow once** advances the story.
- Respects `prefers-reduced-motion`.

## Design notes

- **Accents.** Mint = PyRIT-owned; amber = agent-owned / untrusted; green = operator decision; red dashed = refused path; grey = infrastructure or unchanged PyRIT. Each chapter declares its own legend.
- **Motif.** `{"total": 31}` (then `36`) from the captured receipt: a wire frame, a journal line, a piece with provenance, the thing an operator approves, what a scorer reads.
- **Truthfulness.** Code panels are pinned to a branch tip and shown with file, branch and SHA; the recipe hash is labelled as an illustration of the rule (the bytes PyRIT hashes are pydantic's serialization); the replay says it is a mock and is followed by the real screenshots; the conformance report is the real one.
- **Limits and declined items are on the slides**, not in a footnote.

## Not done (deliberately)

Captions only, no voiceover. No live Copilot CLI or Docker captures (the fixtures and screenshots use the repository's scripted ACP agent so everything is reproducible without credentials). No scroll-mode variant.
