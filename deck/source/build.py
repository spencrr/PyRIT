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


def data_uri(path: Path) -> str:
    import base64

    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "webp": "image/webp"}[path.suffix.lstrip(".").lower()]
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def summarize_journal(path: Path) -> list[dict]:
    """Reduce journal events to the rows the Co-PyRIT replay renders; the full journal stays in fixtures/."""
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event["payload"]
        row = {"sequence": event["sequence"], "direction": event["direction"], "kind": "other", "label": ""}
        method = payload.get("method")
        update = payload.get("params", {}).get("update", {}) if isinstance(payload.get("params"), dict) else {}
        if event["direction"] == "lifecycle":
            kind = payload.get("type", "")
            row["label"] = kind
            if kind == "connection.state":
                row["label"] = f"connection.state · {payload.get('state')}"
                if payload.get("state") == "ready":
                    row["kind"] = "connected"
            elif kind == "permission.resolved":
                approval = payload.get("approval", {})
                row.update(kind="permission_resolved", decision=approval.get("decision", ""), actor=approval.get("actor", ""))
                row["label"] = f"permission.resolved · {approval.get('decision')} · {approval.get('actor')}"
            elif kind == "turn_finished":
                row.update(kind="turn_finished", status=payload.get("status"))
                row["label"] = f"turn_finished · {payload.get('status')}"
        elif method == "session/prompt":
            text = "".join(part.get("text", "") for part in payload["params"].get("prompt", []))
            row.update(kind="prompt", text=text, label=f"session/prompt · “{text}”")
        elif method == "session/update":
            which = update.get("sessionUpdate")
            if which == "agent_message_chunk":
                text = update.get("content", {}).get("text", "")
                row.update(kind="chunk", text=text, label=f"agent_message_chunk · “{text}”")
            elif which == "tool_call":
                row.update(kind="tool_call", call_id=update.get("toolCallId"), title=update.get("title"), name=update.get("name"), label=f"tool_call · {update.get('name')} ({update.get('toolCallId')})")
            elif which == "tool_call_update":
                output = json.dumps(update.get("rawOutput"), separators=(",", ":")) if "rawOutput" in update else ""
                row.update(kind="tool_update", call_id=update.get("toolCallId"), status=update.get("status"), output=(output[:60] + "…") if len(output) > 60 else output, label=f"tool_call_update · {update.get('status')} · {output[:40]}")
            else:
                row["label"] = f"session/update · {which}"
        elif method == "session/request_permission":
            tool_call = payload["params"].get("toolCall", {})
            row.update(kind="permission_request", title=tool_call.get("title"), label=f"session/request_permission · {tool_call.get('title')}")
        elif method:
            row["label"] = method
        elif "result" in payload:
            result = payload["result"]
            if isinstance(result, dict) and "stopReason" in result:
                row.update(kind="result", label=f"result · stopReason {result['stopReason']}")
            elif isinstance(result, dict) and "outcome" in result:
                row["label"] = f"permission reply · {result['outcome'].get('optionId')} ({result['outcome'].get('outcome')})"
            elif isinstance(result, dict) and "sessionId" in result:
                row["label"] = "session/new ✓"
            elif isinstance(result, dict) and "protocolVersion" in result:
                row["label"] = "initialize ✓"
            else:
                row["label"] = "result"
        rows.append(row)
    # attach the response text to each turn_finished row from the trailing chunks
    text = ""
    for row in rows:
        if row["kind"] == "prompt":
            text = ""
        elif row["kind"] == "chunk":
            text += row["text"]
        elif row["kind"] == "turn_finished":
            row["response"] = text
    return rows


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
            for node in step.get("nodes", []):
                if node.get("evidence") and "source" in node["evidence"]:
                    node["evidence"]["resolved"] = resolve_source(args.repo, node["evidence"]["source"], sha_cache)
                    count += 1
            if step.get("image") and not step["image"].startswith("http"):
                step["image"] = data_uri(HERE / step["image"])
    deck["fixtures"] = {
        "ask_events": summarize_journal(HERE / "fixtures" / "ask_events.jsonl"),
        "conformance_local": json.loads((HERE / "fixtures" / "conformance_local.json").read_text(encoding="utf-8")),
    }
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
