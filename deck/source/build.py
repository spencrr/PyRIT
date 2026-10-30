"""Build the PyRIT agent-testing deck: resolve evidence from pinned git refs and inline everything into one HTML file.

Usage: python build.py [--repo PATH] [--out dist]

Evidence sources in deck.json name a branch (or SHA) plus a file and anchor regexes; the snippet is read with
`git show <ref>:<file>` so the deck never drifts from the code it cites. A missing anchor fails the build.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = Path(r"C:\Users\spencersch\src\PyRIT.worktrees\pyrt-agent-execution-environment")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_source(repo: Path, source: dict, sha_cache: dict[str, str]) -> dict:
    """Return the snippet lines, focus indexes (0-based within the snippet), and the pinned SHA."""
    if "fixture" in source:
        text = (HERE / "fixtures" / source["fixture"]).read_text(encoding="utf-8")
        ref, sha = "fixture", ""
        file_label = f"fixtures/{source['fixture']}"
        if "sequence" in source:
            # Journal lines are compact NDJSON; show one event pretty-printed with its envelope summarized.
            events = [json.loads(line) for line in text.splitlines() if line.strip()]
            event = next(e for e in events if e["sequence"] == source["sequence"])
            header = f"# sequence {event['sequence']} · {event['direction']} · turn {str(event.get('turn_id'))[:8]}"
            text = header + "\n" + json.dumps(event["payload"], indent=1)
            file_label += f" · sequence {event['sequence']}"
    else:
        ref = source["ref"]
        sha = sha_cache.setdefault(ref, git(repo, "rev-parse", "--short", ref).strip())
        text = git(repo, "show", f"{ref}:{source['file']}")
        file_label = source["file"]
    lines = text.splitlines()
    start = 0
    if "start" in source:
        start = next((i for i, line in enumerate(lines) if re.search(source["start"], line)), None)
        if start is None:
            raise SystemExit(f"anchor not found: {source['start']!r} in {file_label}@{ref}")
        start = max(0, start - source.get("before", 0))
    if "end" in source:
        end = next((i for i in range(start + 1, len(lines)) if re.search(source["end"], lines[i])), None)
        if end is None:
            raise SystemExit(f"end anchor not found: {source['end']!r} in {file_label}@{ref}")
        end += 1 + source.get("after", 0)
    else:
        end = start + source.get("lines", 12)
    snippet = lines[start:end]
    # Normalize indentation so deeply nested code reads at the panel width.
    indents = [len(line) - len(line.lstrip()) for line in snippet if line.strip()]
    trim = min(indents) if indents else 0
    snippet = [line[trim:] if line.strip() else "" for line in snippet]
    focus: set[int] = set()
    for pattern in source.get("focus", []):
        matched = [i for i, line in enumerate(snippet) if re.search(pattern, line)]
        if not matched:
            raise SystemExit(f"focus not found: {pattern!r} in {file_label}@{ref} lines {start + 1}-{end}")
        focus.update(matched)
    rows = compact(snippet, focus, first_line=start + 1, max_lines=source.get("max_lines", MAX_LINES))
    return {"file": file_label, "ref": ref, "sha": sha, "lines": rows}


MAX_LINES = 14


def compact(snippet: list[str], focus: set[int], *, first_line: int, max_lines: int) -> list[list]:
    """Keep the opening line and a window around each focus line; elide the rest with a marker row.

    Returns rows of [line_number | None, text, is_focus]; a None line number marks an elision.
    """
    if len(snippet) <= max_lines:
        return [[first_line + i, text, i in focus] for i, text in enumerate(snippet)]
    keep = {0} | set(focus)
    radius = 0
    while True:
        grown = set(keep)
        for i in focus:
            grown.update(range(max(0, i - radius - 1), min(len(snippet), i + radius + 2)))
        if len(grown) + _gaps(grown, len(snippet)) > max_lines:
            break
        keep = grown
        radius += 1
        if len(keep) == len(snippet):
            break
    rows: list[list] = []
    previous = -1
    for i in sorted(keep):
        if i != previous + 1:
            rows.append([None, f"⋯ {i - previous - 1} lines", False])
        rows.append([first_line + i, snippet[i], i in focus])
        previous = i
    if previous != len(snippet) - 1:
        rows.append([None, f"⋯ {len(snippet) - 1 - previous} lines", False])
    return rows


def _gaps(kept: set[int], total: int) -> int:
    ordered = sorted(kept)
    gaps = sum(1 for a, b in zip(ordered, ordered[1:]) if b != a + 1)
    return gaps + (1 if ordered and ordered[-1] != total - 1 else 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    args = parser.parse_args()

    sys.path.insert(0, str(HERE))
    from deck import DECK as deck  # noqa: E402  (authored as Python for helpers; deck.json is the emitted data)

    (HERE / "deck.json").write_text(json.dumps(deck, indent=1, ensure_ascii=False), encoding="utf-8")
    sha_cache: dict[str, str] = {}
    count = 0
    for chapter in deck["chapters"]:
        for step in chapter["steps"]:
            evidence = step.get("evidence")
            if evidence and "source" in evidence:
                evidence["resolved"] = resolve_source(args.repo, evidence["source"], sha_cache)
                count += 1
    deck["meta"]["pins"] = sha_cache
    deck["meta"]["built"] = git(args.repo, "log", "-1", "--format=%cs", "agents/stack-08-agent-framework").strip()

    template = (HERE / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(deck, ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("/*__DECK_JSON__*/", payload)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(html, encoding="utf-8")
    steps = sum(len(c["steps"]) for c in deck["chapters"])
    print(f"built {args.out / 'index.html'}: {len(deck['chapters'])} chapters, {steps} steps, {count} evidence panels, pins {sha_cache}")


if __name__ == "__main__":
    sys.exit(main())
