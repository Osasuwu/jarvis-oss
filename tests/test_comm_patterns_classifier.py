"""Classifier helper tests — JSON parsing + enum validation + envelope unwrap.

Live HTTP isn't exercised here; the envelope-parsing path is covered with
a monkeypatched ``urllib.request.urlopen`` so the ``call_ollama`` wire
contract doesn't drift undetected if the Ollama API shape changes.
"""

from __future__ import annotations

import io
import json as _json
from unittest.mock import patch

import pytest

from comm_patterns import classifier as _classifier
from comm_patterns.classifier import VALID_LABELS, _extract_json, normalize_result


def test_extract_json_strict():
    obj = _extract_json('{"primary_label": "affirmation", "confidence": 0.9}')
    assert obj == {"primary_label": "affirmation", "confidence": 0.9}


def test_extract_json_strips_code_fences():
    raw = '```json\n{"primary_label": "affirmation"}\n```'
    obj = _extract_json(raw)
    assert obj == {"primary_label": "affirmation"}


def test_extract_json_finds_object_in_chatty_response():
    raw = 'Here is the answer: {"primary_label": null} — let me know.'
    obj = _extract_json(raw)
    assert obj == {"primary_label": None}


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("hello world") is None


def test_normalize_result_accepts_valid_label():
    out = normalize_result(
        {"primary_label": "affirmation", "confidence": 0.9, "anchor_quote": "ok"},
        anchor_fallback="x",
    )
    assert out is not None
    assert out["primary_label"] == "affirmation"
    assert out["confidence"] == 0.9
    assert out["anchor_quote"] == "ok"


def test_normalize_result_rejects_invalid_label():
    out = normalize_result(
        {"primary_label": "bogus_value", "confidence": 0.9, "anchor_quote": "ok"},
        anchor_fallback="x",
    )
    assert out is None


def test_normalize_result_clamps_confidence():
    out = normalize_result(
        {"primary_label": "affirmation", "confidence": 99, "anchor_quote": "ok"},
        anchor_fallback="x",
    )
    assert out["confidence"] == 1.0


def test_normalize_result_uses_anchor_fallback_when_missing():
    out = normalize_result(
        {"primary_label": "affirmation", "confidence": 0.9},
        anchor_fallback="fallback text",
    )
    assert out["anchor_quote"] == "fallback text"


def test_normalize_result_handles_null_label():
    out = normalize_result(
        {"primary_label": None, "confidence": 0.0, "anchor_quote": "x"},
        anchor_fallback="x",
    )
    assert out is not None
    assert out["primary_label"] is None


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_ollama_envelope(response_text: str) -> bytes:
    return _json.dumps(
        {"model": "qwen3:4b", "response": response_text, "done": True}
    ).encode("utf-8")


def test_call_ollama_unwraps_envelope_and_normalises():
    """Synthetic Ollama response → envelope unwrap → JSON extract →
    normalise. Pinning the wire shape so a future Ollama API change shows
    up here, not in production silently returning None."""
    inner = '{"primary_label": "affirmation", "subtype": null, "confidence": 0.9, "anchor_quote": "ok"}'
    payload = _fake_ollama_envelope(inner)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        out = _classifier.call_ollama("ok", "did X")
    assert out is not None
    assert out["primary_label"] == "affirmation"
    assert out["confidence"] == 0.9


def test_call_ollama_returns_none_on_envelope_garbage():
    payload = b"not json at all"
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        out = _classifier.call_ollama("x", "y")
    assert out is None


def test_call_ollama_returns_none_on_inner_garbage():
    payload = _fake_ollama_envelope("hello world, no JSON here")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        out = _classifier.call_ollama("x", "y")
    assert out is None


def test_call_ollama_raises_ollama_unavailable_on_url_error():
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(_classifier.OllamaUnavailable):
            _classifier.call_ollama("x", "y")


def test_call_ollama_raises_ollama_unavailable_on_timeout():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("request timed out")):
        with pytest.raises(_classifier.OllamaUnavailable):
            _classifier.call_ollama("x", "y")


def test_call_ollama_raises_ollama_unavailable_on_os_error():
    with patch("urllib.request.urlopen", side_effect=OSError("connection reset")):
        with pytest.raises(_classifier.OllamaUnavailable):
            _classifier.call_ollama("x", "y")


def test_valid_labels_match_schema_check_constraint():
    """Sentinel: the enum on the Python side has to track schema.sql.
    If this fails, ADR 0004 / schema / classifier are out of sync.
    Reads schema.sql directly so the test can't drift from the table
    definition."""
    schema = (Path(__file__).resolve().parent.parent / "mcp-memory" / "schema.sql").read_text(
        encoding="utf-8"
    )
    # Walk the comm_patterns CREATE TABLE block.
    block_start = schema.index("create table if not exists comm_patterns")
    block = schema[block_start : block_start + 2000]
    # primary_label CHECK has the six allowed values quoted.
    schema_labels = set(re.findall(r"'([a-z_]+)'", block))
    assert VALID_LABELS == schema_labels


# Imports needed by the schema sentinel — keep at the bottom so the
# top-of-file remains a simple "test the public API" view.
import re  # noqa: E402
from pathlib import Path  # noqa: E402
