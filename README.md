# jobwatch

Watch a job board and get told when a real requisition moves, not when a posting just sits there.

Postings can stay online for months. What matters to an applicant is the day something real happens: a new posting in a department you care about, a re-publish, an edited description, a changed compensation block. `jobwatch` reads a board's public API once a day, diffs it against a baseline kept in git, and writes a one-page report with a verdict.

I built it to apply to one company at the right moment. On 2026-09-11 it flagged a new requisition the morning after it went live. It started as a Claude Code routine with a short diff script in my notes. This repo is that core rewritten as a package with tests, plus a model step that is new here, and the routine's prompt with private details removed.

## Three layers

| Layer | What it does | Where |
|---|---|---|
| Core | Fetch, snapshot, diff, verdict, report. Deterministic, tested, no model. | `jobwatch/` |
| Judge | Only when something changed: a model classifies a text edit (requirements, compensation, process, or wording only) and writes the day's summary. It can downgrade a wording-only edit, never upgrade, and never touches the baseline. | `jobwatch/judge.py` |
| Agent | A scheduled Claude Code routine that runs the core, cross-checks the board page and Hacker News, keeps a list of known false alarms, and writes the verdict into my notes. | `agent/ROUTINE.md` |

## Quickstart

```bash
cp config/examples/ashby.json config/acme.json   # set "org" to the slug from jobs.ashbyhq.com/<org>
python -m jobwatch check config/acme.json        # first run captures the baseline
python -m jobwatch check config/acme.json        # later runs diff, report, update
python -m jobwatch check config/acme.json --json # one-line verdict for automation
python -m pytest                                 # no network, no keys
```

Python 3.10 or newer, standard library only. State goes to `state/<slug>.json`, reports to `reports/<slug>/<date>.md` with one row per run in `log.md`. Dates are UTC.

## Verdicts

| Level | Meaning |
|---|---|
| `STRONG` | A new posting, a re-publish, an edited description, a changed compensation block, or a disappearance, in a department you watch |
| `NOTABLE` | The same events outside watched departments; markup-only churn; a text edit the judge called wording only |
| `QUIET` | Nothing moved |
| `BASELINE` | First run, nothing to compare yet |
| `DEGRADED` | The board could not be read, or came back empty against a non-empty baseline. The baseline is left untouched |

## The judge

Set `ANTHROPIC_API_KEY` and it is on (Claude, `claude-opus-5`). `--judge openai --base-url … --model …` switches to any OpenAI-compatible endpoint: OpenAI, xAI, a local Ollama. `--judge off` turns it off. Environment equivalents: `JOBWATCH_JUDGE`, `JOBWATCH_MODEL`, `JOBWATCH_BASE_URL`. It runs only when something changed, so quiet days cost nothing. Every failure is written into the report as `judge skipped` and the deterministic verdict stands.

One residual risk, stated plainly: the posting text goes to the model, so a posting that contains instructions could talk it into calling a real edit "wording only". The worst case is a text-edit alert dropping from STRONG to NOTABLE. Nothing else passes through the model, and the raw diff is in the report either way.

To see it work without waiting for a real edit: change a few words of one posting's `text` in `state/<slug>.json`, run a check, and read the Judge section of the report.

## Config

```json
{
  "name": "Acme",
  "slug": "acme",
  "source": { "type": "ashby", "org": "acme" },
  "alert_departments": ["Engineering"],
  "ignore_departments": ["Warehouse"]
}
```

An empty `alert_departments` means every department alerts. `ignore_departments` never even make NOTABLE. A new board type is one function that returns `Posting` objects, see `jobwatch/sources.py`.

## Design decisions

- The model is downstream of the diff, never upstream. It cannot invent a change or hide one.
- Hash the raw HTML, then compare the readable text. Markup churn and real edits are told apart.
- A failed fetch, an unexpected payload, or an empty board against a baseline is DEGRADED, never "removed".
- Timestamps are compared in UTC at second precision. A date-format change on the board is not a re-publish.
- Departments decide alerts, not titles. The first run is a baseline, never an alert.
- The baseline lives in git. History is the diff trail.

## Unattended

`.github/workflows/watch.yml` runs every config daily, commits `state/` and `reports/`, opens an issue on STRONG, and prints a warning on DEGRADED. Add `ANTHROPIC_API_KEY` as a repository secret and the judge runs there too. `tests.yml` runs pytest and ruff on Python 3.10 and 3.12 on every push.

## Limitations

- It reads one kind of board: Ashby, the service behind `jobs.ashbyhq.com/<company>`. Companies on Greenhouse, Lever, or Workable need one more adapter function each, about thirty lines; none is written yet.
- It sees only what the company publishes. A new posting means something moved on the board, not that a headcount is open; a posting that never changes says nothing either way.

MIT license.
