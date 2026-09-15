import io
import json
import urllib.error
import urllib.request

import pytest

from jobwatch import sources


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def respond(monkeypatch, *bodies):
    """urlopen returns the given bodies in order; an Exception instance is raised instead."""
    queue = list(bodies)
    attempts = []

    def fake_urlopen(req, timeout=0):
        attempts.append(req)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return attempts


def test_parse_ashby_maps_fields_and_strips_titles():
    payload = {
        "jobs": [
            {
                "id": "2f01bd23",
                "title": " AI agent engineer ",
                "department": "Development",
                "publishedAt": "2026-09-10T17:17:10.146+00:00",
                "descriptionHtml": "<p>x</p>",
                "compensation": {"compensationTierSummary": "$150K – $250K • $20k signing bonus"},
                "jobUrl": "https://jobs.ashbyhq.com/acme/2f01bd23",
            }
        ]
    }
    (p,) = sources.parse_ashby(payload, "acme")
    assert p.title == "AI agent engineer"
    assert p.department == "Development"
    assert p.compensation.startswith("$150K")
    assert p.url.endswith("/acme/2f01bd23")


def test_parse_ashby_tolerates_missing_fields():
    (p,) = sources.parse_ashby({"jobs": [{"id": "x"}]}, "acme")
    assert (p.title, p.department, p.published_at, p.description_html, p.compensation) == ("", "", "", "", "")
    assert p.url == "https://jobs.ashbyhq.com/acme/x"


@pytest.mark.parametrize(
    "payload",
    [{"error": "rate limited"}, [], "nope", {"jobs": "x"}, {"jobs": [1]}],
    ids=["error-object", "list", "string", "jobs-not-a-list", "job-not-an-object"],
)
def test_unexpected_payload_shapes_are_fetch_errors(payload):
    with pytest.raises(sources.FetchError):
        sources.parse_ashby(payload, "acme")


def test_network_failure_becomes_fetch_error(monkeypatch):
    respond(monkeypatch, OSError("connection refused"), OSError("connection refused"), OSError("connection refused"))
    with pytest.raises(sources.FetchError, match="OSError"):
        sources.fetch({"type": "ashby", "org": "acme"})


def test_http_error_and_bad_json_become_fetch_errors(monkeypatch):
    err = urllib.error.HTTPError("https://x", 503, "unavailable", {}, io.BytesIO(b""))
    respond(monkeypatch, err, err, err)
    with pytest.raises(sources.FetchError, match="HTTPError"):
        sources.fetch({"type": "ashby", "org": "acme"})
    respond(monkeypatch, b"<html>", b"<html>", b"<html>")
    with pytest.raises(sources.FetchError, match="JSONDecodeError"):
        sources.fetch({"type": "ashby", "org": "acme"})


def test_transient_failure_is_retried(monkeypatch):
    ok = json.dumps({"jobs": [{"id": "1", "title": "Software engineer"}]}).encode()
    attempts = respond(monkeypatch, OSError("reset"), OSError("reset"), ok)
    (p,) = sources.fetch({"type": "ashby", "org": "acme"})
    assert p.id == "1" and len(attempts) == 3


def test_unknown_source_type_is_a_config_error():
    with pytest.raises(ValueError):
        sources.fetch({"type": "greenhouse"})
