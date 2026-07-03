"""Tests for scripts/lib/llm_client.py — fallback logic.

Covers the key acceptance criterion: Ollama failure → DeepSeek fallback,
with no half-write and correct endpoint usage.
"""

from __future__ import annotations

from unittest.mock import patch

from lib.llm_client import call_llm


def test_call_llm_uses_ollama_when_available():
    """call_llm returns Ollama response and does not call DeepSeek."""
    with (
        patch("lib.llm_client.call_ollama", return_value="ollama-result") as mock_ollama,
        patch("lib.llm_client.call_deepseek") as mock_deepseek,
    ):
        result = call_llm("test prompt", system_prompt="sys")

    assert result == "ollama-result"
    mock_ollama.assert_called_once()
    mock_deepseek.assert_not_called()


def test_call_llm_falls_back_to_deepseek_on_ollama_failure():
    """When Ollama returns None, call_llm falls back to DeepSeek."""
    deepseek_response = '[{"type":"user","name":"x","content":"y"}]'
    with (
        patch("lib.llm_client.call_ollama", return_value=None) as mock_ollama,
        patch("lib.llm_client.call_deepseek", return_value=deepseek_response) as mock_deepseek,
    ):
        result = call_llm("test prompt", system_prompt="sys")

    assert result == deepseek_response
    mock_ollama.assert_called_once()
    mock_deepseek.assert_called_once()


def test_call_llm_returns_none_when_both_backends_fail():
    """When both Ollama and DeepSeek fail, call_llm returns None (no half-write)."""
    with (
        patch("lib.llm_client.call_ollama", return_value=None),
        patch("lib.llm_client.call_deepseek", return_value=None),
    ):
        result = call_llm("test prompt")

    assert result is None


def test_call_llm_forwards_format_json_to_deepseek():
    """call_llm must forward format_json to BOTH Ollama and DeepSeek.

    Regression: round-2 forwarded format_json only to Ollama; the DeepSeek
    fallback call dropped it. DeepSeek without JSON-mode wraps output in
    prose, which then triggered the non-greedy regex bug in
    scripts/deriver/pipeline.py and silently dropped all candidates.
    """
    with (
        patch("lib.llm_client.call_ollama", return_value=None),
        patch("lib.llm_client.call_deepseek", return_value="ok") as mock_deepseek,
    ):
        call_llm("test prompt", system_prompt="sys", format_json=True)

    mock_deepseek.assert_called_once()
    kwargs = mock_deepseek.call_args.kwargs
    assert kwargs.get("format_json") is True, (
        f"call_llm must forward format_json=True to call_deepseek; got kwargs={kwargs!r}"
    )


def test_call_deepseek_sets_response_format_when_format_json_true():
    """call_deepseek must set OpenAI-compatible response_format when format_json=True."""
    from lib.llm_client import call_deepseek

    captured: dict = {}

    class _MockResp:
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _capture_urlopen(req, timeout=None):
        import json as _json

        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return _MockResp()

    with patch("lib.llm_client.urllib.request.urlopen", side_effect=_capture_urlopen):
        call_deepseek("p", api_key="fake-key", format_json=True)

    assert captured["body"].get("response_format") == {"type": "json_object"}, (
        "DeepSeek POST body must include response_format when format_json=True; "
        f"got {captured['body']!r}"
    )


def test_call_deepseek_omits_response_format_when_format_json_false():
    """call_deepseek must NOT set response_format when format_json=False."""
    from lib.llm_client import call_deepseek

    captured: dict = {}

    class _MockResp:
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _capture_urlopen(req, timeout=None):
        import json as _json

        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return _MockResp()

    with patch("lib.llm_client.urllib.request.urlopen", side_effect=_capture_urlopen):
        call_deepseek("p", api_key="fake-key", format_json=False)

    assert "response_format" not in captured["body"]
