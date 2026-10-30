# PyRIT · agent targets — interactive deck

An interactive, animated walkthrough of the PyRIT agent-testing design behind drafts
[#15–#25 on `spencrr/PyRIT`](https://github.com/spencrr/PyRIT/pulls) and the
[story gist](https://gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90).
Built the way the [KYLN example](../kyln/) and the [code-to-deck guide](../../GUIDE-code-to-interactive-deck.md) prescribe:
deck as data, one stateful stage per chapter, claim + evidence, two semantic accents.

## What is in here

| Path | Purpose |
| --- | --- |
| `deck.py` | The content: chapters, stages (actors, edges), steps (patches, camera, animations, captions, evidence anchors), stack explorable, epilogue cards. `build.py` emits it as `deck.json`. |
| `template.html` | The engine: tokens, layout zones, reducer, stage renderer (cards, slots, SVG edges, packets, camera pans), hero / agenda / card / stack renderers, keyboard + autoplay + presenter mode. |
| `build.py` | Resolves every evidence panel with `git show <ref>:<file>` from the PyRIT worktree using **anchor regexes** (start / end / focus), compacts long snippets around the focus lines, records the pinned SHA, inlines everything into `dist/index.html`. Fails on any unresolved anchor. |
| `fixtures/` | Real artifacts captured from the scripted ACP agent in the PyRIT repo (`events.jsonl`, `record.json`, `projection.json`, `receipt.json`) and `generate.py` to re-capture them. |
| `export.mjs` | Playwright export: PNG per step with an overflow check, or an autoplay recording (`--video`). |
| `dist/` | Build output: `index.html` (single file), `png/`, `walkthrough.mp4`, `trailer.mp4`. |

## Build and export

```powershell
python build.py                       # → dist/index.html, deck.json
node export.mjs                       # → dist/png/NN-chapter--step.png + layout warnings
node export.mjs --video --name walkthrough --dwell 7000
node export.mjs --video --name trailer --dwell 5000 --steps 0,2,3,5,6,7,9,13,15,16,18,20,23,25,27,30,31,33,37,40,41,44,47,49,50,53
ffmpeg -i dist/walkthrough.webm -c:v libx264 -pix_fmt yuv420p -crf 23 dist/walkthrough.mp4
```

Playwright is taken from the PyRIT frontend's `node_modules` (`PLAYWRIGHT_ROOT` overrides the path).

## Using the deck

- `←` `→` steps · `Tab` / `Shift+Tab` chapter · `Space` autoplay (progress ring on the play button) · `E` toggle evidence ·
  `P` presenter mode (bigger type, evidence hidden, inactive actors dimmed) · `Esc` overview · `Home` / `End`.
- Deep links: `#/<chapter>/<step>`. Query flags: `?mode=present`, `?play=1`, `?motion=off`.
- Respects `prefers-reduced-motion` (final state only, no packets or pans).

## Design notes

- **Accents.** Mint = PyRIT-owned (runtime, policy, journal, relay, projection, attack). Amber = agent-owned / untrusted
  (harness, its ACP frames, its tool effects, model output). Green = operator decision. Red dashed = refused path.
  Grey = infrastructure. Each chapter declares its own legend.
- **Motif.** `{"total": 31}` from the captured receipt appears as a wire frame (ch 1), a journal line and a piece with
  provenance (ch 3), the thing an operator approves (ch 6) and what a scorer reads; the recap names the five views.
- **Evidence is pinned.** Every code panel shows file, branch and short SHA; the footer lists the pins. Rebuilding against a
  moved branch either re-resolves the anchors or fails loudly.
- **Limits are on the slides.** Steps carry `limit` / `review` callouts; the epilogue has "Known limits" and "How to review".

## Not done (deliberately)

No voiceover (captions only). No live Copilot CLI / Docker / Co-PyRIT captures: the fixtures come from the repository's
scripted ACP agent so the deck is reproducible without credentials. No scroll-mode variant.
