"""Multi-tier LLM escalation for the Deriver pipeline.

Imports the tier-chain pattern from sandcastle slice 5 (PR #543 / #574,
decision f8e27d53):

  Tier 0  — routine host + Ollama (primary model, ``OLLAMA_MODEL``)
  Tier 1  — Smaller Ollama model on Tier 0 failure (``DERIVER_OLLAMA_TIER1_MODEL``)
  Tier 2  — DeepSeek API on persistent failure (gated by ``DERIVER_DEEPSEEK_FALLBACK``)
  Deferred — all tiers exhausted; no candidates written, the caller writes an
             ``events_canonical`` row to record the skip.

Claude API is deliberately unreachable from this module.  Only Ollama (local)
and DeepSeek (OpenAI-compatible) endpoints are wired.  This is enforced by
both env-var scope (``DERIVER_*`` namespace) and code structure — there are
no imports or references to any Claude/Anthropic SDK in this module or its
transitive dependencies.

Config env vars:
  ``DERIVER_DEEPSEEK_FALLBACK`` (default ``"true"``) — set ``"false"`` to
  disable Tier 2 and fall through directly to defer on Tier 0+1 failure.
  ``DERIVER_OLLAMA_TIER1_MODEL`` — model name for Tier 1 (smaller Ollama
  model).  When empty, Tier 1 is skipped and the chain goes Tier 0 → Tier 2.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from lib.llm_client import call_ollama, call_deepseek

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_FALLBACK = "DERIVER_DEEPSEEK_FALLBACK"
"""Env var name: enables Tier 2 (DeepSeek) on persistent Tier 0+1 failure."""

ENV_TIER1_MODEL = "DERIVER_OLLAMA_TIER1_MODEL"
"""Env var name: smaller Ollama model name for Tier 1."""

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class TierResult:
    """Result of a multi-tier LLM invocation.

    Attributes:
      text:            Response text, or None when all tiers fail.
      tier_completed:  Which tier produced the result —
                       ``"tier0"`` | ``"tier1"`` | ``"tier2"`` | ``"deferred"``.
      model:           Model name that produced (or attempted) the response.
      input_tokens:    Prompt tokens consumed (best-effort).
      output_tokens:   Completion tokens produced (best-effort).
    """
    text: str | None
    tier_completed: str = "deferred"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def derive_with_escalation(
    prompt: str,
    *,
    system_prompt: str | None = None,
    format_json: bool = True,
) -> TierResult:
    """Run the multi-tier escalation chain.

    Each tier is attempted in order.  The first tier that returns non-None
    text wins; on failure the chain escalates to the next tier.  When all
    tiers are exhausted the result carries ``tier_completed="deferred"``
    and ``text=None``.

    Parameters:
      prompt:       The rendered LLM prompt (scrubbed transcript).
      system_prompt:  Optional system instruction.
      format_json:    Request JSON output mode (default True).

    Returns:
      ``TierResult`` with *text* = None when all tiers fail.
    """
    # ---- Resolve config ----
    tier2_enabled = os.environ.get(ENV_FALLBACK, "true").strip().lower() in ("true", "1", "yes")
    tier1_model = os.environ.get(ENV_TIER1_MODEL, "").strip()

    # ---- Tier 0: routine host + Ollama (primary) ----
    raw = call_ollama(prompt, system_prompt=system_prompt, format_json=format_json, return_usage=True)
    if raw is not None and isinstance(raw, dict) and raw.get("text"):
        return TierResult(
            text=raw["text"],
            tier_completed="tier0",
            model=raw.get("model", ""),
            input_tokens=raw.get("input_tokens", 0),
            output_tokens=raw.get("output_tokens", 0),
        )
    _log_tier_failure("tier0", raw)

    # ---- Tier 1: smaller Ollama model (only if configured) ----
    if tier1_model:
        raw = call_ollama(
            prompt,
            model=tier1_model,
            system_prompt=system_prompt,
            format_json=format_json,
            return_usage=True,
        )
        if raw is not None and isinstance(raw, dict) and raw.get("text"):
            return TierResult(
                text=raw["text"],
                tier_completed="tier1",
                model=raw.get("model", ""),
                input_tokens=raw.get("input_tokens", 0),
                output_tokens=raw.get("output_tokens", 0),
            )
        _log_tier_failure("tier1", raw)
    else:
        print(
            f"[deriver-escalation] Tier 1 skipped (no {ENV_TIER1_MODEL})",
            file=sys.stderr,
        )

    # ---- Tier 2: DeepSeek API (gated by env var) ----
    if tier2_enabled:
        raw = call_deepseek(
            prompt,
            system_prompt=system_prompt,
            format_json=format_json,
            return_usage=True,
        )
        if raw is not None and isinstance(raw, dict) and raw.get("text"):
            return TierResult(
                text=raw["text"],
                tier_completed="tier2",
                model=raw.get("model", ""),
                input_tokens=raw.get("input_tokens", 0),
                output_tokens=raw.get("output_tokens", 0),
            )
        _log_tier_failure("tier2", raw)
    else:
        print(
            f"[deriver-escalation] Tier 2 skipped ({ENV_FALLBACK}=false)",
            file=sys.stderr,
        )

    # ---- All tiers exhausted — defer ----
    print(
        "[deriver-escalation] all tiers exhausted — deferring to queue",
        file=sys.stderr,
    )
    return TierResult(text=None, tier_completed="deferred")


def _log_tier_failure(tier: str, raw: Any) -> None:
    """Log a tier failure to stderr — no secret values."""
    reason = "connection/timeout" if raw is None else "empty response"
    print(
        f"[deriver-escalation] {tier} failed: {reason}",
        file=sys.stderr,
    )
