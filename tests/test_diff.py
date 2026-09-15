import pytest

from jobwatch.diff import Snapshot, compute, hash_html, normalize_timestamp, snapshot, text_delta, text_of
from jobwatch.sources import Posting


def P(
    pid,
    title="Software engineer",
    dept="Development",
    pub="2025-07-28T06:46:39.581+00:00",
    html="<p>Hello <b>world</b></p>",
):
    return Posting(pid, title, dept, pub, html, "$150K", "")


def base(*postings):
    return {p.id: snapshot(p) for p in postings}


def test_text_of_strips_markup_and_entities():
    assert text_of("<p>Hello&nbsp;<b>world</b>\n</p>") == "Hello world"


def test_hash_is_stable_and_short():
    assert hash_html("<p>x</p>") == hash_html("<p>x</p>")
    assert len(hash_html("<p>x</p>")) == 16
    assert hash_html("<p>x</p>") != hash_html("<div>x</div>")


def test_added_removed_unchanged():
    d = compute(base(P("a"), P("b")), [P("a"), P("c")])
    assert [s.id for s in d.added] == ["c"]
    assert [s.id for s in d.removed] == ["b"]
    assert d.unchanged == 1
    assert d.changed == []


def test_markup_only_change_is_told_apart_from_text_change():
    d = compute(base(P("a")), [P("a", html="<div>Hello <strong>world</strong></div>")])
    assert d.changed[0].kinds == ["markup_only"]
    d = compute(base(P("a")), [P("a", html="<p>Hello <b>there</b></p>")])
    assert d.changed[0].kinds == ["text"]
    assert d.changed[0].before.text == "Hello world" and d.changed[0].after.text == "Hello there"


def test_text_change_is_seen_even_when_the_hash_matches():
    b = base(P("a"))
    b["a"] = Snapshot(**{**b["a"].to_dict(), "text": "Hello there"})  # a hand-edited baseline, as in the README recipe
    assert compute(b, [P("a")]).changed[0].kinds == ["text"]


def test_published_at_and_compensation_changes():
    changed = Posting(
        "a", "Software engineer", "Development", "2026-09-10T17:17:10Z", "<p>Hello <b>world</b></p>", "$160K", ""
    )
    d = compute(base(P("a")), [changed])
    assert set(d.changed[0].kinds) == {"published_at", "compensation"}
    assert d.changed[0].before.compensation == "$150K"


@pytest.mark.parametrize(
    "a,b",
    [
        ("2026-09-10T17:17:10.146+00:00", "2026-09-10T17:17:10Z"),
        ("2026-09-10T19:17:10+02:00", "2026-09-10T17:17:10+00:00"),
    ],
    ids=["precision-and-Z", "offset"],
)
def test_timestamp_format_changes_are_not_republishes(a, b):
    assert normalize_timestamp(a) == normalize_timestamp(b)
    assert compute(base(P("a", pub=a)), [P("a", pub=b)]).unchanged == 1


def test_unparseable_timestamps_compare_as_strings():
    assert normalize_timestamp("soon") == "soon"
    assert compute(base(P("a", pub="soon")), [P("a", pub="later")]).changed[0].kinds == ["published_at"]


def test_snapshot_round_trips_through_dict():
    s = snapshot(P("a"))
    assert Snapshot.from_dict(s.to_dict()) == s


def test_text_delta_shows_changed_sentences_only():
    assert text_delta("One. Two. Three.", "One. Two changed. Three.") == "- Two.\n+ Two changed."


def test_text_delta_is_capped():
    out = text_delta("A. " * 400, "B. " * 400, limit=50)
    assert out.endswith(" ...") and len(out) == 54
