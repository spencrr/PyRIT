# Assets for the PyRIT agent-testing PR stack

Referenced by the [stack story gist](https://gist.github.com/spencrr/474c66a5f7c569106b5068b7477d4e90) and the PR bodies of drafts #15–#25.

## Diagrams (`diagrams/`)

SVG sources, Mermaid drafts, and 2x PNG renders (`node diagrams/render.mjs`).

## Interactive deck and videos (`deck/`)

| File | What |
| --- | --- |
| [`deck/index.html`](deck/index.html) | Single-file interactive deck: 7 chapters, 54 steps, 39 code panels pinned to branch tips. Open locally, or view online via [htmlpreview](https://htmlpreview.github.io/?https://raw.githubusercontent.com/spencrr/PyRIT/agents/stack-assets/deck/index.html). Keys: `←` `→` steps · `Tab` chapter · `Space` autoplay · `E` evidence · `P` presenter · `Esc` overview. |
| [`deck/trailer.mp4`](deck/trailer.mp4) | 2:17 autoplay cut of the key beats (1080p, captions only). |
| [`deck/walkthrough.mp4`](deck/walkthrough.mp4) | 6:25 full autoplay recording of all 54 steps. |
| `deck/png/` | One PNG per step (handout / review without a browser). |
| `deck/source/` | Deck as data (`deck.py` → `deck.json`), engine (`template.html`), evidence resolver (`build.py`), exporter (`export.mjs`), and the real fixtures captured from the repository's scripted ACP agent (`fixtures/`). See `deck/source/README.md`. |

Evidence panels are resolved at build time with `git show <branch>:<file>` using anchor regexes, so the deck states the
branch and short SHA it was built from (footer and every panel). Rebuild against a moved branch re-resolves or fails loudly.
