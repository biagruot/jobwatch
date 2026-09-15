"""Job-board adapters. Each one returns a list of Posting.

A board that cannot be read, or that returns something other than a job list, raises
FetchError. Callers must treat that as "unknown", never as "the postings are gone".
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.request
from dataclasses import dataclass

from . import __version__

USER_AGENT = f"jobwatch/{__version__} (+https://github.com/biagruot/jobwatch)"
KNOWN_TYPES = ("ashby",)


class FetchError(RuntimeError):
    """The board could not be read (network, HTTP status, bad JSON, unexpected shape)."""


@dataclass(frozen=True)
class Posting:
    id: str
    title: str
    department: str
    published_at: str  # ISO timestamp as the board returns it, "" if absent
    description_html: str
    compensation: str  # human-readable summary, "" if absent
    url: str


def _get_json(url: str, timeout: int = 20, attempts: int = 3) -> object:
    """GET a JSON document. Retries transient failures with a short backoff, then raises FetchError."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except (OSError, ValueError, http.client.HTTPException) as exc:  # network, HTTP status, timeout, bad JSON
            last = exc
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise FetchError(f"{url} -> {last.__class__.__name__}: {last}") from last


# ---- Ashby (jobs.ashbyhq.com boards) -----------------------------------------


def parse_ashby(payload: object, org: str) -> list[Posting]:
    """Map the Ashby posting API payload to Postings. Anything but a list of jobs is a FetchError."""
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise FetchError("unexpected payload shape: no 'jobs' list")
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            raise FetchError("unexpected payload shape: a job entry is not an object")
        comp = job.get("compensation") or {}
        jid = str(job.get("id") or "")
        out.append(
            Posting(
                id=jid,
                title=(job.get("title") or "").strip(),
                department=(job.get("department") or "").strip(),
                published_at=job.get("publishedAt") or "",
                description_html=job.get("descriptionHtml") or "",
                compensation=comp.get("compensationTierSummary")
                or comp.get("scrapeableCompensationSalarySummary")
                or "",
                url=job.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{jid}",
            )
        )
    return out


def fetch_ashby(org: str) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    return parse_ashby(_get_json(url), org)


# ---- dispatch ------------------------------------------------------------------


def fetch(source: dict) -> list[Posting]:
    """Fetch postings for a config `source` block, e.g. {"type": "ashby", "org": "acme"}."""
    kind = source.get("type")
    if kind == "ashby":
        return fetch_ashby(source["org"])
    raise ValueError(f"unknown source type: {kind!r}")
