import pytest

from jobwatch.diff import compute, snapshot
from jobwatch.report import assess, cell, log_line, render
from jobwatch.sources import Posting

CFG = {
    "name": "Acme",
    "slug": "acme",
    "source": {"type": "ashby", "org": "acme"},
    "alert_departments": ["Development"],
    "ignore_departments": ["Factory"],
}


def P(
    pid, title="Software engineer", dept="Development", pub="2025-07-28T00:00:00Z", html="<p>Hello</p>", comp="$150K"
):
    return Posting(pid, title, dept, pub, html, comp, "")


def base(*postings):
    return {p.id: snapshot(p) for p in postings}


def test_new_posting_in_alert_department_is_strong():
    v = assess(compute(base(P("a")), [P("a"), P("b", title="AI agent engineer")]), CFG)
    assert v.level == "STRONG"
    assert "new posting" in v.reasons[0] and "AI agent engineer" in v.reasons[0]


def test_new_posting_elsewhere_is_notable():
    assert assess(compute(base(P("a")), [P("a"), P("b", dept="Marketing")]), CFG).level == "NOTABLE"


def test_ignored_department_never_shows_up():
    assert assess(compute(base(P("a")), [P("a"), P("b", dept="Factory")]), CFG).level == "QUIET"


def test_markup_only_churn_is_notable():
    v = assess(compute(base(P("a")), [P("a", html="<div>Hello</div>")]), CFG)
    assert v.level == "NOTABLE" and "markup-only" in v.reasons[0]


@pytest.mark.parametrize(
    "changed",
    [P("a", pub="2026-09-10T00:00:00Z"), P("a", html="<p>Hello there</p>"), P("a", comp="$200K")],
    ids=["re-published", "text-edited", "compensation-changed"],
)
def test_requisition_events_in_alert_department_are_strong(changed):
    assert assess(compute(base(P("a")), [changed]), CFG).level == "STRONG"


def test_disappearance_in_alert_department_is_strong():
    v = assess(compute(base(P("a"), P("b")), [P("b")]), CFG)
    assert v.level == "STRONG" and "disappeared" in v.reasons[0]


def test_quiet_when_nothing_moved():
    assert assess(compute(base(P("a")), [P("a")]), CFG).reasons == ["1 posting unchanged"]
    assert assess(compute(base(P("a"), P("b")), [P("a"), P("b")]), CFG).reasons == ["2 postings unchanged"]


def test_empty_alert_list_means_every_department_alerts():
    cfg = {**CFG, "alert_departments": []}
    assert assess(compute(base(P("a")), [P("a"), P("b", dept="Marketing")]), cfg).level == "STRONG"


def test_render_has_title_reasons_diff_and_table():
    current = [P("a", html="<p>Hello there</p>"), P("b", title="AI agent engineer")]
    diff = compute(base(P("a")), current)
    v = assess(diff, CFG)
    md = render(CFG, "2026-09-11", v, [snapshot(p) for p in current], "2026-09-10", diff)
    assert md.startswith("# Acme · 2026-09-11 · STRONG")
    assert "baseline from 2026-09-10" in md
    assert "```diff\n- Hello\n+ Hello there\n```" in md
    assert "| AI agent engineer |" in md
    assert log_line("2026-09-11", v).startswith("| 2026-09-11 | STRONG |")


def test_cells_are_table_safe():
    assert cell("a | b\nc") == "a \\| b c"
    current = [P("a"), P("b", title="Weird | title")]
    md = render(CFG, "2026-09-11", assess(compute(base(P("a")), current), CFG), [snapshot(p) for p in current], None)
    assert "Weird \\| title" in md and "Weird | title" not in md
