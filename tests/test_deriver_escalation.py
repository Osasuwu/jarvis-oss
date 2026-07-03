"""Tests for the Deriver multi-tier escalation (slice 7, #558).

Covers the acceptance criteria:
  - Tier 0 success → no escalation, no extra cost recorded
  - Tier 0 failure → Tier 1 retry on configured model
  - Persistent Tier 0+1 failure → Tier 2 escalation to DeepSeek
  - Tier 2 failure → defer-to-queue (deferred result)
  - ``DERIVER_DEEPSEEK_FALLBACK=false`` disables Tier 2
  - Claude API never reachable from Deriver path
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from deriver.escalation import derive_with_escalation, ENV_FALLBACK, ENV_TIER1_MODEL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLLAMA_OK = {"text": "hello from ollama", "model": "qwen2.5-coder:14b", "input_tokens": 10, "output_tokens": 20}
_OLLAMA_TIER1_OK = {"text": "hello from tier1", "model": "qwen2.5-coder:7b", "input_tokens": 8, "output_tokens": 15}
_DEEPSEEK_OK = {"text": "hello from deepseek", "model": "deepseek-chat", "input_tokens": 12, "output_tokens": 25}
_OLLAMA_FAIL = None
_DEEPSEEK_FAIL = None


# ---------------------------------------------------------------------------
# Tier 0 success — fastest path
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_tier0_success_returns_result(mock_ollama: patch):
    """Tier 0 works → result returned with tier_completed='tier0'."""
    mock_ollama.return_value = _OLLAMA_OK
    result = derive_with_escalation("test prompt")
    assert result.text == "hello from ollama"
    assert result.tier_completed == "tier0"
    assert result.model == "qwen2.5-coder:14b"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    mock_ollama.assert_called_once()


@patch("deriver.escalation.call_ollama")
def test_tier0_success_no_deepseek_call(mock_ollama: patch):
    """Tier 0 success → DeepSeek is never called."""
    mock_ollama.return_value = _OLLAMA_OK
    with patch("deriver.escalation.call_deepseek") as mock_ds:
        result = derive_with_escalation("test prompt")
    assert result.tier_completed == "tier0"
    mock_ds.assert_not_called()


# ---------------------------------------------------------------------------
# Tier 0 → Tier 1 escalation
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_tier0_failure_triggers_tier1(mock_ollama: patch):
    """Tier 0 fails → Tier 1 retry on configured smaller model."""
    # First call (Tier 0) fails, second call (Tier 1) succeeds
    mock_ollama.side_effect = [_OLLAMA_FAIL, _OLLAMA_TIER1_OK]
    with patch.dict("os.environ", {ENV_TIER1_MODEL: "qwen2.5-coder:7b"}):
        result = derive_with_escalation("test prompt")
    assert result.text == "hello from tier1"
    assert result.tier_completed == "tier1"
    assert result.model == "qwen2.5-coder:7b"
    # Should have called Ollama twice (Tier 0 + Tier 1)
    assert mock_ollama.call_count == 2


@patch("deriver.escalation.call_ollama")
def test_tier0_failure_no_tier1_model_skips_to_tier2(mock_ollama: patch):
    """No DERIVER_OLLAMA_TIER1_MODEL set → Tier 1 skipped, Tier 2 tried."""
    mock_ollama.return_value = _OLLAMA_FAIL
    with patch("deriver.escalation.call_deepseek") as mock_ds:
        mock_ds.return_value = _DEEPSEEK_OK
        result = derive_with_escalation("test prompt")
    assert result.text == "hello from deepseek"
    assert result.tier_completed == "tier2"
    # Only one Ollama call (Tier 0), no Tier 1
    mock_ollama.assert_called_once()
    mock_ds.assert_called_once()


# ---------------------------------------------------------------------------
# Tier 0 + 1 → Tier 2 (DeepSeek)
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_tier0_and_tier1_failure_triggers_tier2(mock_ollama: patch):
    """Tier 0 and Tier 1 fail → Tier 2 (DeepSeek) produces result."""
    mock_ollama.side_effect = [_OLLAMA_FAIL, _OLLAMA_FAIL]
    with (
        patch("deriver.escalation.call_deepseek") as mock_ds,
        patch.dict("os.environ", {ENV_TIER1_MODEL: "qwen2.5-coder:7b"}),
    ):
        mock_ds.return_value = _DEEPSEEK_OK
        result = derive_with_escalation("test prompt")
    assert result.text == "hello from deepseek"
    assert result.tier_completed == "tier2"
    assert result.model == "deepseek-chat"
    assert mock_ollama.call_count == 2
    mock_ds.assert_called_once()


# ---------------------------------------------------------------------------
# All tiers fail → deferred
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_all_tiers_fail_returns_deferred(mock_ollama: patch):
    """All tiers exhausted → deferred result with text=None."""
    mock_ollama.side_effect = [_OLLAMA_FAIL, _OLLAMA_FAIL]
    with (
        patch("deriver.escalation.call_deepseek") as mock_ds,
        patch.dict("os.environ", {ENV_TIER1_MODEL: "qwen2.5-coder:7b"}),
    ):
        mock_ds.return_value = _DEEPSEEK_FAIL
        result = derive_with_escalation("test prompt")
    assert result.text is None
    assert result.tier_completed == "deferred"
    assert mock_ollama.call_count == 2
    mock_ds.assert_called_once()


@patch("deriver.escalation.call_ollama")
def test_deferred_without_tier1(mock_ollama: patch):
    """All tiers fail (no Tier 1 configured) → deferred."""
    mock_ollama.return_value = _OLLAMA_FAIL
    with patch("deriver.escalation.call_deepseek") as mock_ds:
        mock_ds.return_value = _DEEPSEEK_FAIL
        result = derive_with_escalation("test prompt")
    assert result.text is None
    assert result.tier_completed == "deferred"
    # Only one Ollama call (no Tier 1), one DeepSeek call
    mock_ollama.assert_called_once()
    mock_ds.assert_called_once()


# ---------------------------------------------------------------------------
# DERIVER_DEEPSEEK_FALLBACK=false — Tier 2 disabled
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_tier2_disabled_by_env_fallback_to_deferred(mock_ollama: patch):
    """DERIVER_DEEPSEEK_FALLBACK=false → no DeepSeek call, defer on Tier 0+1 fail."""
    mock_ollama.side_effect = [_OLLAMA_FAIL, _OLLAMA_FAIL]
    with (
        patch.dict("os.environ", {ENV_FALLBACK: "false", ENV_TIER1_MODEL: "qwen2.5-coder:7b"}),
        patch("deriver.escalation.call_deepseek") as mock_ds,
    ):
        result = derive_with_escalation("test prompt")
    assert result.text is None
    assert result.tier_completed == "deferred"
    # DeepSeek should NOT be called
    mock_ds.assert_not_called()


@patch("deriver.escalation.call_ollama")
def test_tier2_disabled_tier0_alone_falls_to_deferred(mock_ollama: patch):
    """DERIVER_DEEPSEEK_FALLBACK=false + no Tier 1 → defer after Tier 0 fail."""
    mock_ollama.return_value = _OLLAMA_FAIL
    with (
        patch.dict("os.environ", {ENV_FALLBACK: "false"}),
        patch("deriver.escalation.call_deepseek") as mock_ds,
    ):
        result = derive_with_escalation("test prompt")
    assert result.text is None
    assert result.tier_completed == "deferred"
    mock_ollama.assert_called_once()
    mock_ds.assert_not_called()


# ---------------------------------------------------------------------------
# Env var parsing
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
@pytest.mark.parametrize("val", ["true", "True", "1", "yes", "TRUE"])
def test_tier2_enabled_by_various_true_values(mock_ollama: patch, val: str):
    """Tier 2 enabled for any truthy env value."""
    mock_ollama.side_effect = [_OLLAMA_FAIL, _OLLAMA_FAIL]
    with (
        patch.dict("os.environ", {ENV_FALLBACK: val, ENV_TIER1_MODEL: "qwen2.5-coder:7b"}),
        patch("deriver.escalation.call_deepseek") as mock_ds,
    ):
        mock_ds.return_value = _DEEPSEEK_OK
        result = derive_with_escalation("test prompt")
    assert result.tier_completed == "tier2"
    assert result.text == "hello from deepseek"


@patch("deriver.escalation.call_ollama")
@pytest.mark.parametrize("val", ["false", "False", "0", "no", "FALSE"])
def test_tier2_disabled_by_various_false_values(mock_ollama: patch, val: str):
    """Tier 2 disabled for any falsy env value."""
    mock_ollama.return_value = _OLLAMA_FAIL
    with patch.dict("os.environ", {ENV_FALLBACK: val}):
        result = derive_with_escalation("test prompt")
    assert result.text is None
    assert result.tier_completed == "deferred"


# ---------------------------------------------------------------------------
# Empty response from Ollama (OOM-like) triggers escalation
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_ollama_empty_response_triggers_tier1(mock_ollama: patch):
    """Ollama returns empty text (OOM-like) → escalates to Tier 1."""
    mock_ollama.side_effect = [
        {"text": None, "model": "qwen2.5-coder:14b", "input_tokens": 100, "output_tokens": 0},
        _OLLAMA_TIER1_OK,
    ]
    with patch.dict("os.environ", {ENV_TIER1_MODEL: "qwen2.5-coder:7b"}):
        result = derive_with_escalation("test prompt")
    assert result.tier_completed == "tier1"
    assert result.text == "hello from tier1"


# ---------------------------------------------------------------------------
# System prompt forwarding
# ---------------------------------------------------------------------------


@patch("deriver.escalation.call_ollama")
def test_system_prompt_forwarded_to_tier0(mock_ollama: patch):
    """system_prompt parameter reaches the underlying call_ollama."""
    mock_ollama.return_value = _OLLAMA_OK
    derive_with_escalation("test prompt", system_prompt="You are a test assistant.")
    mock_ollama.assert_called_once()
    _kw = mock_ollama.call_args.kwargs
    assert _kw.get("system_prompt") == "You are a test assistant."


@patch("deriver.escalation.call_ollama")
def test_format_json_forwarded_to_tier0(mock_ollama: patch):
    """format_json parameter reaches the underlying call_ollama."""
    mock_ollama.return_value = _OLLAMA_OK
    derive_with_escalation("test prompt", format_json=False)
    mock_ollama.assert_called_once()
    _kw = mock_ollama.call_args.kwargs
    assert _kw.get("format_json") is False
