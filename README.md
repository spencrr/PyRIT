# Assets for the PyRIT agent-testing PR stack

Referenced by the [stack story gist](https://gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90) and the PR bodies of drafts #15–#25.

## Diagrams (`diagrams/`)

SVG sources, Mermaid drafts, and 2x PNG renders (`node diagrams/render.mjs`).

## Interactive deck, videos, handout (`deck/`)

| File | What |
| --- | --- |
| [`deck/index.html`](deck/index.html) | Single-file interactive deck, two tracks. **Tour** (default, 44 steps): the pitch in visual form; a live recipe builder; the map; an operator's, an administrator's and an extender's day with real captures. **Deep dive** (press `T`, 91 steps): adds the mechanism chapters with code pinned to branch tips and a decisions explorable. View online via [htmlpreview](https://htmlpreview.github.io/?https://raw.githubusercontent.com/spencrr/PyRIT/agents/stack-assets/deck/index.html); deep dive: [`#/turn/intro`](https://htmlpreview.github.io/?https://raw.githubusercontent.com/spencrr/PyRIT/agents/stack-assets/deck/index.html#/turn/intro). |
| [`deck/trailer.mp4`](deck/trailer.mp4) | 1:43 cut of the key beats (1080p, captions only). |
| [`deck/tour.mp4`](deck/tour.mp4) | 6:00 autoplay of the tour track. |
| [`deck/deep-dive.mp4`](deck/deep-dive.mp4) | 13:45 autoplay of the deep-dive track (all 91 steps). |
| [`deck/handout.pdf`](deck/handout.pdf) | 44-page handout: one page per tour step. |
| `deck/png-tour/`, `deck/png/` | One PNG per step (tour / all). |
| `deck/source/` | Deck as data (`deck_tour.py`, `deck.py` → `deck.json`), engine (`template.html`), evidence resolver (`build.py`), exporter (`export.mjs`), handout builder, and the real fixtures: journals, projection, conformance report, and the Co-PyRIT screenshots with the script that captured them. See `deck/source/README.md`. |

Evidence panels are resolved at build time with `git show <branch>:<file>` using anchor regexes, so the deck states the
branch and short SHA it was built from (footer and every panel). Rebuild against a moved branch re-resolves or fails loudly.
