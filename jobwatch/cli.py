"""Command line: `python -m jobwatch check config/<board>.json`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from . import sources
from .diff import Diff, Snapshot, compute, snapshot
from .judge import Judge, JudgeError
from .report import Verdict, assess, log_line, render

SLUG = re.compile(r"^[a-z0-9-]+$")


class ConfigError(ValueError):
    """The config file is missing something the run needs."""


def load_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(cfg, dict) or not cfg.get("name"):
        raise ConfigError(f"{path}: 'name' is required")
    if not isinstance(cfg.get("slug"), str) or not SLUG.match(cfg["slug"]):
        raise ConfigError(f"{path}: 'slug' must match {SLUG.pattern}; it names the state and report files")
    source = cfg.get("source") or {}
    if source.get("type") not in sources.KNOWN_TYPES:
        raise ConfigError(f"{path}: source.type must be one of {sources.KNOWN_TYPES}")
    if source["type"] == "ashby" and not source.get("org"):
        raise ConfigError(f"{path}: source.org is required for an Ashby board")
    return cfg


def load_baseline(path: Path) -> tuple[dict[str, Snapshot] | None, str | None]:
    if not path.exists():
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: Snapshot.from_dict(v) for k, v in data["postings"].items()}, data.get("captured")


def save_baseline(path: Path, snaps: list[Snapshot], date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"captured": date, "postings": {s.id: s.to_dict() for s in snaps}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def report_path_for(report_dir: Path, today: str) -> Path:
    """One file per run. A second run on the same day gets a -2, -3 suffix instead of overwriting the first."""
    path = report_dir / f"{today}.md"
    n = 2
    while path.exists():
        path = report_dir / f"{today}-{n}.md"
        n += 1
    return path


def run_judge(args: argparse.Namespace, cfg: dict, today: str, diff: Diff, verdict: Verdict) -> Verdict:
    """Let a model classify text edits and write the summary.

    Rewrites `kinds` on the changes the model calls wording-only (in place) and re-assesses.
    Never raises, never upgrades a verdict, never looks at ignored departments.
    """
    if args.judge == "off":
        verdict.judge = {"model": None, "notes": ["judge off (--judge off)"], "summary": None}
        return verdict
    notes: list[str] = []
    summary = None
    try:
        judge = Judge.from_env(args.judge, args.model, args.base_url)
    except JudgeError as exc:
        judge = None
        notes.append(f"judge skipped: {exc}")
    if judge is None:
        if not notes:
            notes.append("judge off: set ANTHROPIC_API_KEY, or pass --judge, to classify edits and get a summary")
        verdict.judge = {"model": None, "notes": notes, "summary": None}
        return verdict
    ignore = set(cfg.get("ignore_departments") or [])
    for change in diff.changed:
        if "text" not in change.kinds:
            continue
        if change.before.department in ignore and change.after.department in ignore:
            continue
        try:
            result = judge.classify_edit(change.after.title, change.before.text, change.after.text)
        except JudgeError as exc:
            notes.append(f"judge skipped on {change.after.id[:8]}: {exc}")
            continue
        change.judge = result
        if result["kind"] == "wording":
            change.kinds = ["wording_only" if k == "text" else k for k in change.kinds]
        notes.append(f'{change.after.id[:8]} "{change.after.title}": {result["kind"]} edit. {result["reason"]}')
    verdict = assess(diff, cfg)
    try:
        summary = judge.summarize(cfg["name"], today, verdict.level, verdict.reasons)
    except JudgeError as exc:
        notes.append(f"summary skipped: {exc}")
    verdict.judge = {"model": judge.label, "notes": notes, "summary": summary}
    return verdict


def run_check(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"jobwatch: {exc}", file=sys.stderr)
        return 2
    slug = cfg["slug"]
    today = args.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    state_path = Path(args.state) / f"{slug}.json"
    report_dir = Path(args.reports) / slug

    baseline, baseline_date = load_baseline(state_path)
    snaps: list[Snapshot] | None = None
    diff: Diff | None = None
    try:
        postings = sources.fetch(cfg["source"])
    except sources.FetchError as exc:
        verdict = Verdict(
            "DEGRADED",
            [f"fetch failed: {exc}", "a failed fetch never means a posting was removed"],
            degraded=str(exc),
        )
        if baseline:
            snaps = list(baseline.values())
    else:
        snaps = [snapshot(p) for p in postings]
        if baseline and not snaps:
            verdict = Verdict(
                "DEGRADED",
                [
                    "the board returned zero postings against a non-empty baseline",
                    "verify by hand before trusting a removal",
                ],
                degraded="empty board",
            )
            snaps = list(baseline.values())
        elif baseline is None:
            verdict = Verdict("BASELINE", [f"baseline captured: {len(snaps)} postings"])
            if not args.dry_run:
                save_baseline(state_path, snaps, today)
        else:
            diff = compute(baseline, postings)
            verdict = assess(diff, cfg)
            if verdict.level in ("STRONG", "NOTABLE"):
                verdict = run_judge(args, cfg, today, diff, verdict)
            if not args.dry_run:
                save_baseline(state_path, snaps, today)

    md = render(cfg, today, verdict, snaps, baseline_date, diff)
    report_path = report_dir / f"{today}.md" if args.dry_run else report_path_for(report_dir, today)
    if not args.dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8")
        log = report_dir / "log.md"
        if not log.exists():
            log.write_text(
                f"# {cfg['name']} · run log\n\n| date | verdict | reasons |\n|---|---|---|\n", encoding="utf-8"
            )
        with log.open("a", encoding="utf-8") as fh:
            fh.write(log_line(today, verdict))

    if args.json:
        print(
            json.dumps(
                {
                    "name": cfg["name"],
                    "slug": slug,
                    "date": today,
                    "level": verdict.level,
                    "degraded": verdict.degraded,
                    "reasons": verdict.reasons,
                    "judge": verdict.judge,
                    "report": None if args.dry_run else str(report_path),
                },
                ensure_ascii=False,
            )
        )
    else:
        sys.stdout.write(md)
    return 0


def _iso_date(value: str) -> str:
    return dt.date.fromisoformat(value).isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="jobwatch", description="Detect real hiring signals on job boards where postings stay up for months."
    )
    sub = ap.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check", help="fetch the board, diff against the baseline, write the report, update the baseline"
    )
    check.add_argument("config", help="path to a config JSON (see config/)")
    check.add_argument("--state", default="state", help="directory for baseline files (default: state)")
    check.add_argument("--reports", default="reports", help="directory for reports and logs (default: reports)")
    check.add_argument(
        "--json", action="store_true", help="print a one-line JSON verdict instead of the markdown report"
    )
    check.add_argument("--dry-run", action="store_true", help="never write the baseline, the report, or the log")
    check.add_argument("--today", type=_iso_date, help="override the run date, YYYY-MM-DD (default: today in UTC)")
    check.add_argument(
        "--judge",
        choices=["anthropic", "openai", "off"],
        help="model provider for the judge (default: anthropic when ANTHROPIC_API_KEY is set, else off)",
    )
    check.add_argument(
        "--model", help="judge model id (default claude-opus-5 for anthropic; required for openai-compatible)"
    )
    check.add_argument(
        "--base-url", help="OpenAI-compatible base URL, e.g. https://api.x.ai/v1 or http://localhost:11434/v1"
    )
    return run_check(ap.parse_args(argv))
