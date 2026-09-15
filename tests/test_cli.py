import json

import pytest

from jobwatch import cli, sources
from jobwatch.judge import JudgeError
from jobwatch.sources import Posting

CFG = {
    "name": "Acme",
    "slug": "acme",
    "source": {"type": "ashby", "org": "acme"},
    "alert_departments": ["Development"],
    "ignore_departments": ["Factory"],
}


def P(pid, title="Software engineer", dept="Development", html="<p>Hello</p>"):
    return Posting(pid, title, dept, "2025-07-28T00:00:00Z", html, "$150K", "")


EDITED = P("a", html="<p>Hello there</p>")


def write_config(tmp_path, cfg=CFG):
    path = tmp_path / "acme.json"
    path.write_text(json.dumps(cfg))
    return path


def run(tmp_path, monkeypatch, capsys, result, today, extra=()):
    """One check against a fake board. `result` is a list of postings, or an exception to raise."""

    def fake_fetch(source):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    argv = [
        "check",
        str(write_config(tmp_path)),
        "--state",
        str(tmp_path / "state"),
        "--reports",
        str(tmp_path / "reports"),
    ]
    assert cli.main([*argv, "--json", "--today", today, *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_full_lifecycle(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state" / "acme.json"
    log = tmp_path / "reports" / "acme" / "log.md"

    first = run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    assert first["level"] == "BASELINE"
    assert state.exists() and (tmp_path / "reports" / "acme" / "2026-09-10.md").exists()

    assert run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-11")["level"] == "QUIET"

    strong = run(tmp_path, monkeypatch, capsys, [P("a"), P("b", title="AI agent engineer")], "2026-09-12")
    assert strong["level"] == "STRONG"
    assert any("AI agent engineer" in r for r in strong["reasons"])
    assert '"b"' in state.read_text()  # the baseline now includes the new posting

    before = state.read_bytes()
    degraded = run(tmp_path, monkeypatch, capsys, sources.FetchError("HTTP 403"), "2026-09-13")
    assert degraded["level"] == "DEGRADED" and degraded["degraded"] == "HTTP 403"
    assert state.read_bytes() == before  # a failed fetch never touches the baseline
    assert "DEGRADED" in (tmp_path / "reports" / "acme" / "2026-09-13.md").read_text()

    rows = [line for line in log.read_text().splitlines() if line.startswith("| 2026-")]
    assert [r.split(" | ")[1] for r in rows] == ["BASELINE", "QUIET", "STRONG", "DEGRADED"]


def test_empty_board_against_a_baseline_is_degraded(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    state = tmp_path / "state" / "acme.json"
    before = state.read_bytes()
    out = run(tmp_path, monkeypatch, capsys, [], "2026-09-11")
    assert out["level"] == "DEGRADED" and out["degraded"] == "empty board"
    assert state.read_bytes() == before


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    out = run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10", extra=["--dry-run"])
    assert out["level"] == "BASELINE" and out["report"] is None
    assert not (tmp_path / "state").exists() and not (tmp_path / "reports").exists()


def test_same_day_reruns_do_not_overwrite_the_report(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    second = run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    assert second["report"].endswith("2026-09-10-2.md")
    assert (tmp_path / "reports" / "acme" / "2026-09-10.md").exists()


@pytest.mark.parametrize(
    "cfg",
    [
        {**CFG, "slug": "Bad Slug"},
        {**CFG, "source": {"type": "greenhouse"}},
        {**CFG, "source": {"type": "ashby"}},
        {"name": "Acme"},
    ],
    ids=["bad-slug", "unknown-source", "missing-org", "missing-slug-and-source"],
)
def test_invalid_config_exits_2_with_a_message(tmp_path, capsys, cfg):
    path = write_config(tmp_path, cfg)
    assert cli.main(["check", str(path), "--state", str(tmp_path / "s"), "--reports", str(tmp_path / "r")]) == 2
    assert "jobwatch:" in capsys.readouterr().err


def test_missing_config_file_exits_2(tmp_path, capsys):
    assert cli.main(["check", str(tmp_path / "nope.json")]) == 2
    assert "nope.json" in capsys.readouterr().err


def test_invalid_today_is_refused_by_argparse(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["check", str(write_config(tmp_path)), "--today", "yesterday"])


class FakeJudge:
    label = "fake/model"

    def __init__(self, kind="wording", fail=False):
        self.kind, self.fail, self.calls = kind, fail, []

    def classify_edit(self, title, before, after):
        self.calls.append(title)
        if self.fail:
            raise JudgeError("boom")
        return {"kind": self.kind, "reason": "test reason"}

    def summarize(self, company, date, level, reasons):
        return "Summary paragraph from the judge."


def with_judge(monkeypatch, judge):
    monkeypatch.setattr(cli.Judge, "from_env", classmethod(lambda cls, *a, **k: judge))


def test_judge_downgrades_wording_only_edit(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    with_judge(monkeypatch, FakeJudge("wording"))
    out = run(tmp_path, monkeypatch, capsys, [EDITED], "2026-09-11")
    assert out["level"] == "NOTABLE" and out["judge"]["model"] == "fake/model"
    report = (tmp_path / "reports" / "acme" / "2026-09-11.md").read_text()
    assert "wording-only edit" in report and "Summary paragraph from the judge." in report
    assert "```diff" in report and "+ Hello there" in report  # the raw diff always stays


def test_judge_keeps_requirement_edits_strong(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    with_judge(monkeypatch, FakeJudge("requirements"))
    out = run(tmp_path, monkeypatch, capsys, [EDITED], "2026-09-11")
    assert out["level"] == "STRONG"
    assert any("requirements edit" in n for n in out["judge"]["notes"])


def test_judge_failure_leaves_the_verdict_deterministic(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    with_judge(monkeypatch, FakeJudge(fail=True))
    out = run(tmp_path, monkeypatch, capsys, [EDITED], "2026-09-11")
    assert out["level"] == "STRONG"
    assert any(n.startswith("judge skipped") for n in out["judge"]["notes"])


def test_judge_skips_edits_in_ignored_departments(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a"), P("f", dept="Factory")], "2026-09-10")
    judge = FakeJudge("wording")
    with_judge(monkeypatch, judge)
    out = run(tmp_path, monkeypatch, capsys, [EDITED, P("f", dept="Factory", html="<p>Shift change</p>")], "2026-09-11")
    assert judge.calls == ["Software engineer"] and out["level"] == "NOTABLE"


def test_no_judge_configured_is_noted_not_fatal(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    out = run(tmp_path, monkeypatch, capsys, [P("a"), P("b", title="AI agent engineer")], "2026-09-11")
    assert out["level"] == "STRONG" and out["judge"]["model"] is None
    assert any(n.startswith("judge off") for n in out["judge"]["notes"])


def test_judge_off_flag_wins_over_the_key(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "would-be-used-otherwise")
    out = run(
        tmp_path,
        monkeypatch,
        capsys,
        [P("a"), P("b", title="AI agent engineer")],
        "2026-09-11",
        extra=["--judge", "off"],
    )
    assert out["judge"] == {"model": None, "notes": ["judge off (--judge off)"], "summary": None}


def test_quiet_runs_never_call_the_judge(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-10")
    monkeypatch.setattr(cli, "run_judge", lambda *a, **k: pytest.fail("run_judge called on a quiet day"))
    assert run(tmp_path, monkeypatch, capsys, [P("a")], "2026-09-11")["level"] == "QUIET"
