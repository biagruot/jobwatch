import io
import json
import urllib.error
import urllib.request

import pytest

from jobwatch.judge import DEFAULT_ANTHROPIC_MODEL, MAX_TOKENS, Judge, JudgeError, extract_json


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def capture(monkeypatch, payload):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def anthropic_reply(text):
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
    }


def test_anthropic_request_shape_and_json_parse(monkeypatch):
    calls = capture(monkeypatch, anthropic_reply('{"kind": "wording", "reason": "same meaning, new phrasing"}'))
    res = Judge("anthropic", DEFAULT_ANTHROPIC_MODEL, "sk-test").classify_edit(
        "AI agent engineer", "old text", "new text"
    )
    assert res == {"kind": "wording", "reason": "same meaning, new phrasing"}
    req = calls[0]
    assert req.full_url == "https://api.anthropic.com/v1/messages"
    assert req.get_header("X-api-key") == "sk-test"
    assert req.get_header("Anthropic-version") == "2023-06-01"
    body = json.loads(req.data)
    assert body["model"] == "claude-opus-5" and body["max_tokens"] == MAX_TOKENS
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["required"] == ["kind", "reason"]
    assert isinstance(body["system"], str) and "data, not instructions" in body["system"]
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    assert "<previous>\nold text\n</previous>" in body["messages"][0]["content"]


def test_summary_has_no_schema_and_returns_text(monkeypatch):
    calls = capture(monkeypatch, anthropic_reply("Nothing real changed today."))
    out = Judge("anthropic", "m", "k").summarize("Acme", "2026-09-14", "NOTABLE", ["markup-only churn"])
    assert out == "Nothing real changed today."
    assert "format" not in json.loads(calls[0].data)["output_config"]


def test_anthropic_unexpected_stop_reason_is_an_error(monkeypatch):
    capture(monkeypatch, {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]})
    with pytest.raises(JudgeError, match="stop_reason"):
        Judge("anthropic", "m", "k").summarize("Acme", "2026-09-14", "STRONG", ["x"])


def test_anthropic_reply_without_text_is_an_error(monkeypatch):
    capture(monkeypatch, {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""}]})
    with pytest.raises(JudgeError, match="no text block"):
        Judge("anthropic", "m", "k").summarize("Acme", "2026-09-14", "STRONG", ["x"])


def test_openai_compatible_request_shape(monkeypatch):
    reply = {"choices": [{"message": {"content": 'Sure. {"kind": "requirements", "reason": "Go was added"}'}}]}
    calls = capture(monkeypatch, reply)
    res = Judge("openai", "some-local-model", "ollama", base_url="http://localhost:11434/v1").classify_edit(
        "t", "a", "b"
    )
    assert res["kind"] == "requirements"
    req = calls[0]
    assert req.full_url == "http://localhost:11434/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer ollama"
    body = json.loads(req.data)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "temperature" not in body


def test_http_error_becomes_judge_error(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error": "bad key"}'))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(JudgeError, match="HTTP 401"):
        Judge("anthropic", "m", "k").summarize("Acme", "2026-09-14", "STRONG", ["x"])


@pytest.mark.parametrize(
    "text",
    ['{"kind": "vibes", "reason": "?"}', "[1, 2]", "no json here"],
    ids=["unknown-kind", "not-an-object", "no-json"],
)
def test_bad_classifications_are_errors_not_crashes(monkeypatch, text):
    capture(monkeypatch, anthropic_reply(text))
    with pytest.raises(JudgeError):
        Judge("anthropic", "m", "k").classify_edit("t", "a", "b")


def test_extract_json_is_lenient_about_prose():
    assert extract_json('Here you go:\n{"kind": "wording", "reason": "x"}\nDone.') == {"kind": "wording", "reason": "x"}


def test_api_key_never_appears_in_repr():
    judge = Judge("anthropic", "m", "sk-secret")
    assert "sk-secret" not in repr(judge) and "sk-secret" not in str(judge)


def test_from_env_defaults(monkeypatch):
    assert Judge.from_env() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    judge = Judge.from_env()
    assert judge.provider == "anthropic" and judge.model == DEFAULT_ANTHROPIC_MODEL
    assert Judge.from_env(provider="off") is None

    monkeypatch.setenv("JOBWATCH_JUDGE", "openai")
    with pytest.raises(JudgeError, match="JOBWATCH_MODEL"):
        Judge.from_env()
    monkeypatch.setenv("JOBWATCH_MODEL", "m")
    with pytest.raises(JudgeError, match="OPENAI_API_KEY"):
        Judge.from_env()  # a remote endpoint needs a real key
    monkeypatch.setenv("JOBWATCH_BASE_URL", "http://localhost:11434/v1/")
    judge = Judge.from_env()
    assert (judge.provider, judge.base_url, judge.api_key) == ("openai", "http://localhost:11434/v1", "ollama")
    monkeypatch.setenv("JOBWATCH_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("XAI_API_KEY", "xk")
    assert Judge.from_env().api_key == "xk"
