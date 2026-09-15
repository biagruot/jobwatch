"""Turn a Diff into a verdict and a markdown report."""

from __future__ import annotations

from dataclasses import dataclass, field

from .diff import Diff, Snapshot, text_delta


@dataclass
class Verdict:
    """level is one of BASELINE, QUIET, NOTABLE, STRONG, DEGRADED."""

    level: str
    reasons: list[str] = field(default_factory=list)
    degraded: str | None = None
    judge: dict | None = None  # {"model": str | None, "notes": list[str], "summary": str | None}


def cell(value: str) -> str:
    """One line, pipes escaped: safe inside a markdown table row."""
    return " ".join(str(value).split()).replace("|", "\\|")


def _label(s: Snapshot) -> str:
    return f'{s.id[:8]} "{cell(s.title)}" ({s.department or "no department"})'


def assess(diff: Diff, cfg: dict) -> Verdict:
    """Classify a diff using the config's alert and ignore departments."""
    alert = set(cfg.get("alert_departments") or [])  # empty = every department alerts
    ignore = set(cfg.get("ignore_departments") or [])

    def alerts(s: Snapshot) -> bool:
        return not alert or s.department in alert

    strong: list[str] = []
    notable: list[str] = []

    for s in diff.added:
        if s.department in ignore:
            continue
        msg = f"new posting {_label(s)}, published {s.published_at[:10] or 'n/a'}"
        (strong if alerts(s) else notable).append(msg)
    for s in diff.removed:
        if s.department in ignore:
            continue
        msg = f"posting {_label(s)} disappeared from the board"
        (strong if alerts(s) else notable).append(msg)
    for change in diff.changed:
        s = change.after
        if s.department in ignore and change.before.department in ignore:
            continue
        for kind in change.kinds:
            if kind == "markup_only":
                notable.append(f"markup-only churn on {_label(s)}: hash changed, readable text identical")
            elif kind == "wording_only":
                why = cell((change.judge or {}).get("reason", ""))
                notable.append(f"wording-only edit on {_label(s)} (judge: {why or 'no reason given'})")
            elif kind == "published_at":
                msg = (
                    f"re-published: {_label(s)} publishedAt {change.before.published_at[:10]} -> {s.published_at[:10]}"
                )
                (strong if alerts(s) else notable).append(msg)
            elif kind == "text":
                (strong if alerts(s) else notable).append(f"description text edited on {_label(s)}")
            elif kind == "compensation":
                before, after = cell(change.before.compensation), cell(s.compensation)
                msg = f"compensation changed on {_label(s)}: {before!r} -> {after!r}"
                (strong if alerts(s) else notable).append(msg)
            else:  # title, department
                before, after = cell(getattr(change.before, kind)), cell(getattr(s, kind))
                notable.append(f"{kind} changed on {_label(s)}: {before!r} -> {after!r}")

    if strong:
        return Verdict("STRONG", strong + notable)
    if notable:
        return Verdict("NOTABLE", notable)
    n = diff.unchanged
    return Verdict("QUIET", [f"{n} posting{'s' if n != 1 else ''} unchanged"])


def render(
    cfg: dict,
    date: str,
    verdict: Verdict,
    postings: list[Snapshot] | None,
    baseline_date: str | None,
    diff: Diff | None = None,
) -> str:
    src = cfg.get("source", {})
    src_label = "/".join(str(v) for v in (src.get("type"), src.get("org")) if v)
    lines = [f"# {cfg['name']} · {date} · {verdict.level}", ""]
    meta = f"Source: {src_label}"
    if postings is not None:
        meta += f" · {len(postings)} postings"
    if baseline_date:
        meta += f" · baseline from {baseline_date}"
    lines += [meta, "", "## Reasons", ""]
    lines += [f"- {r}" for r in verdict.reasons] or ["- (none)"]
    if verdict.degraded:
        lines += [
            "",
            f"> DEGRADED: {verdict.degraded}. Nothing was concluded from this run and the baseline was left untouched.",
        ]
    if diff is not None:
        edits = [c for c in diff.changed if "text" in c.kinds or "wording_only" in c.kinds]
        if edits:
            lines += ["", "## Text edits", ""]
            for c in edits:
                lines += [f"### {_label(c.after)}", "", "```diff", text_delta(c.before.text, c.after.text), "```", ""]
    if verdict.judge is not None:
        info = verdict.judge
        lines += ["", "## Judge", "", f"Model: {info.get('model') or 'off'}"]
        lines += [f"- {cell(note)}" for note in info.get("notes") or []]
        if info.get("summary"):
            lines += ["", info["summary"].strip()]
    if postings:
        lines += [
            "",
            "## Postings",
            "",
            "| id | title | department | published | hash | compensation |",
            "|---|---|---|---|---|---|",
        ]
        for s in sorted(postings, key=lambda x: (x.department, x.title)):
            row = [s.id[:8], cell(s.title), cell(s.department), s.published_at[:10], s.hash, cell(s.compensation)]
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def log_line(date: str, verdict: Verdict) -> str:
    return f"| {date} | {verdict.level} | {cell('; '.join(verdict.reasons))[:400]} |\n"
