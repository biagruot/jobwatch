"""Baseline snapshots and the diff between a baseline and the current postings."""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import html
import re
from dataclasses import asdict, dataclass

from .sources import Posting

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def hash_html(description_html: str) -> str:
    """sha256 of the raw HTML, first 16 hex chars. Catches any edit, including markup."""
    return hashlib.sha256(description_html.encode("utf-8")).hexdigest()[:16]


def text_of(description_html: str) -> str:
    """Readable text of the description. Two HTMLs with equal text differ only in markup."""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", description_html))).strip()


def normalize_timestamp(value: str) -> str:
    """An ISO timestamp as UTC at second precision, so a format change on the board is not a re-publish.

    Anything unparseable is compared as the string it is.
    """
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Snapshot:
    """What the baseline stores per posting. No HTML, just the hash and the readable text."""

    id: str
    title: str
    department: str
    published_at: str
    hash: str
    compensation: str
    url: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Snapshot:
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


def snapshot(p: Posting) -> Snapshot:
    return Snapshot(
        id=p.id,
        title=p.title,
        department=p.department,
        published_at=p.published_at,
        hash=hash_html(p.description_html),
        compensation=p.compensation,
        url=p.url,
        text=text_of(p.description_html),
    )


@dataclass
class Change:
    before: Snapshot
    after: Snapshot
    kinds: list[str]  # any of: published_at, text, wording_only, markup_only, compensation, title, department
    judge: dict[str, str] | None = None  # {"kind", "reason"} once a judge classified a text edit


def text_delta(before: str, after: str, limit: int = 1500) -> str:
    """Sentence-level diff of two readable texts, for the report."""
    a = [x for x in _SENTENCE.split(before) if x]
    b = [x for x in _SENTENCE.split(after) if x]
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out += [f"- {x}" for x in a[i1:i2]] + [f"+ {x}" for x in b[j1:j2]]
    text = "\n".join(out)
    return text if len(text) <= limit else text[:limit] + " ..."


@dataclass
class Diff:
    added: list[Snapshot]
    removed: list[Snapshot]
    changed: list[Change]
    unchanged: int


def compute(baseline: dict[str, Snapshot], current: list[Posting]) -> Diff:
    """Diff the current postings against the baseline.

    Text is compared on its own, not only when the hash differs, so a hand-edited
    baseline (the README recipe) shows up as a text edit like a real one would.
    """
    now = {p.id: snapshot(p) for p in current}
    added = [now[i] for i in now if i not in baseline]
    removed = [baseline[i] for i in baseline if i not in now]
    changed: list[Change] = []
    unchanged = 0
    for pid, cur in now.items():
        if pid not in baseline:
            continue
        old = baseline[pid]
        kinds = []
        if normalize_timestamp(old.published_at) != normalize_timestamp(cur.published_at):
            kinds.append("published_at")
        if old.text != cur.text:
            kinds.append("text")
        elif old.hash != cur.hash:
            kinds.append("markup_only")
        if old.compensation != cur.compensation:
            kinds.append("compensation")
        if old.title != cur.title:
            kinds.append("title")
        if old.department != cur.department:
            kinds.append("department")
        if kinds:
            changed.append(Change(old, cur, kinds))
        else:
            unchanged += 1
    return Diff(added, removed, changed, unchanged)
