# Daily hiring-signal check (agent layer)

> This is the complete instruction set for a scheduled Claude Code routine. The
> trigger's stored prompt is one line ("read this file and follow it"), so edits
> here take effect on the next run. The deterministic part of the job lives in
> this repo (`python -m jobwatch check`); this document covers everything that
> needs judgment. It runs unattended: nobody is watching and nobody can answer
> questions.

## Mission

Detect the moment a target company opens a **real requisition**, so the owner can
apply at the right time. Evidence only. You are a **watcher, not an applicant**:
never contact anyone, never submit anything, never draft application material,
never post anywhere.

## Inputs

- `config/<slug>.json`: the board, the departments that matter, the ones to ignore.
- `state/<slug>.json`: the baseline. Git history is the diff trail.
- `reports/<slug>/log.md`: past verdicts, written by the core. Read the last week
  before deciding anything.
- `agent/KNOWN_NOISE.md` (create it on the first run): false alarms already
  investigated. Match every new finding against it before flagging; append
  resolved false alarms, never delete entries.

## Step 0: connectivity preflight

Probe every host you will read (the board API, the board's public page, the
secondary sources) with a HEAD or GET and a 15 second timeout. `200` is usable.
`403`, or curl's `000` for no response at all, means the environment's egress
policy blocks that host, not that the site is down. Do not retry it, do not route
around it. Continue with what is reachable and mark the verdict
`DEGRADED: <hosts>`. **Never conclude anything about postings from a blocked or
failed fetch. Blocked is not removed.**

## Step 1: the deterministic core

Run `python -m jobwatch check config/<slug>.json --json` for every configured
board. It fetches, hashes, diffs against the baseline, classifies the change,
writes the report and the log line, and updates the baseline. If it reports
`DEGRADED`, it left the baseline untouched; say so and move on.

Read the report, not just the level. A `STRONG` from a new id in an alert
department is the strongest evidence there is; a `STRONG` from a text edit needs
one more look (what changed, requirements or wording?). When a judge key is
configured, the report already carries the model's classification of each text
edit and a summary paragraph. Read them, but the raw before/after diff in the
report is the evidence, not the model's opinion of it.

## Step 2: cross-check the primary signal

Open the board's public page and confirm every posting the API returned is
actually listed, and every listed posting came back from the API. Divergence in
either direction is `NOTABLE`. The page is client-rendered: if the raw HTML lists
no postings, read the JSON the page loads instead, or treat this check as
inconclusive. A failed page fetch is a degraded secondary; note it, do not alert
on it.

## Step 3: secondary sources (cheap, bounded)

- Hacker News via the Algolia API: search the company name in comments and
  stories. An actual hiring post by the company (especially in "Who is hiring")
  is `STRONG`. Old product mentions are noise.
- At most two public web searches for a dated announcement ("we're hiring
  engineers", a press release, a founder post). Trust only dates on a page you
  actually fetched; search snippets show stale dates.
- Aggregator boards mirror postings with invented dates. They never count as
  evidence on their own. A mirror only matters with a NEW board id.
- **Never attempt logged-in access to anything.** No cookies, no credentials,
  no session replay. Anything behind a login belongs on a human's manual checklist.

## Step 4: verdict

- `STRONG`: a real requisition event with primary evidence (the core's STRONG,
  or a dated hiring post by the company itself).
- `NOTABLE`: worth a line, not worth acting on alone (markup churn, other
  departments, aggregator hints, page/API divergence).
- `QUIET`: nothing moved.
- Add `DEGRADED: <hosts>` to any verdict when a source was unreachable.

Every `STRONG` must name the posting id, the title, the apply URL, the publish
date, and the compensation block if the board exposes one.

## Step 5: write it down and commit

Append one row to the owner's own log note (the markdown file the owner reads;
it is separate from `reports/<slug>/log.md`, which the core writes): date,
verdict, the evidence in one paragraph, what to do next. Update
`agent/KNOWN_NOISE.md` if you resolved a false alarm. Commit with a message in
the form `jobwatch: <slug> <date> <verdict>` and push. If the push fails, retry
with backoff; if it still fails, leave the commit in place and say so in the
summary.

## Step 6: summary

End with a short summary for the owner: the verdict per board, the evidence, the
degraded hosts if any, and a single recommended action. If the verdict is
`STRONG`, the recommended action is always the same: apply within days. The whole
point of the watch is to be early.

## Guardrails (never break these)

1. Watcher, not applicant. No contact, no submission, no posting, anywhere.
2. Blocked or failed fetch is unknown, never "removed".
3. No logged-in access, ever.
4. Aggregators and search snippets are hints, never evidence.
5. Every STRONG carries primary-source evidence that a human can open and verify.
