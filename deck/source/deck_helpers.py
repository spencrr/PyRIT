"""Small constructors shared by the deck content modules."""

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


def card(id, title, takeaway, *, eyebrow=None, bullets=None, image=None, note=None, alt=None, autoplay=9000, regions=None):
    c = {"id": id, "kind": "card", "title": title, "takeaway": takeaway, "autoplayMs": autoplay}
    for k, v in (("eyebrow", eyebrow), ("bullets", bullets), ("image", image), ("note", note), ("alt", alt), ("regions", regions)):
        if v is not None:
            c[k] = v
    return c


def packet(edge_id, label, tone="mint", count=1, **kw):
    return {"type": "packet", "edge": edge_id, "label": label, "tone": tone, "count": count, **kw}


def step(id, tab, title, body, *, status=None, slots=None, edges=None, camera=None, captions=None, animations=None, evidence=None, limit=None, review=None, autoplay=None, regions=None, deeper=None, deeperLabel=None):
    s = {"id": id, "kind": "step", "tab": tab, "title": title, "body": body, "patch": {}}
    if status:
        s["patch"]["status"] = status
    if slots:
        s["patch"]["slots"] = slots
    if edges:
        s["patch"]["edges"] = edges
    for k, v in (("camera", camera), ("captions", captions), ("animations", animations), ("evidence", evidence), ("limit", limit), ("review", review), ("autoplayMs", autoplay), ("regions", regions), ("deeper", deeper), ("deeperLabel", deeperLabel)):
        if v is not None:
            s[k] = v
    return s
