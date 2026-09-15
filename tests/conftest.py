"""Guards for every test: no API keys from the shell, no network, no sleeping."""

import urllib.request

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "JOBWATCH_JUDGE",
        "JOBWATCH_MODEL",
        "JOBWATCH_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("tests must not open network connections; monkeypatch urllib.request.urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr("jobwatch.sources.time.sleep", lambda seconds: None)
