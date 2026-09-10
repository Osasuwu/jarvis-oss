"""Classifier — call local Ollama, parse JSON, validate against ADR 0004 enum.

Default model is ``qwen3:4b`` — only model that fits Main PC's RTX 3050 6GB
fully in VRAM at ~27 tok/s. ``think:false`` is required for qwen3 or the
response field comes back empty (memory ``qwen3_think_false_required``).

The function is a *pure I/O wrapper* — no business logic beyond JSON
parsing and enum-value validation. Tests inject a fake classifier via
``classify_fn`` parameter on the extractor; this function is exercised
only in the live smoke run.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request


class OllamaUnavailable(RuntimeError):
    """Ollama host unreachable / timed out. Distinct from a malformed reply."""

    pass


VALID_LABELS = {
    "correction_wrong_direction",
    "correction_incomplete",
    "affirmation",
    "affirmation_with_redirect",
    "preference_directive",
    "meta_protocol",
}

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_S = 60

_PROMPT_PATH = Path(__file__).parent / "classifier.md"
_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.S)
_PROMPT_CACHE: str | None = None


def _load_prompt_template() -> str:
    """Read classifier.md once per process. The Stop hook is a fresh process
    each time so the cache only matters for backfill / smoke runs."""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _render_prompt(user_text: str, prev_assistant_text: str) -> str:
    template = _load_prompt_template()
    return template.replace("{prev_assistant_text}", prev_assistant_text or "(none)").replace(
        "{user_text}", user_text
    )


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Try strict parse first; fall back to regex-extracted first object."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Strip code fences.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def normalize_result(obj: dict[str, Any] | None, anchor_fallback: str) -> dict[str, Any] | None:
    """Coerce classifier output into the schema. Return None if unusable."""
    if not isinstance(obj, dict):
        return None
    label = obj.get("primary_label")
    if label is not None and label not in VALID_LABELS:
        return None
    confidence = obj.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    subtype = obj.get("subtype")
    if subtype is not None and not isinstance(subtype, str):
        subtype = None
    if isinstance(subtype, str):
        subtype = subtype.strip()[:64] or None
    anchor = obj.get("anchor_quote") or anchor_fallback
    if not isinstance(anchor, str):
        anchor = anchor_fallback
    anchor = anchor.strip()[:600]  # row stores text not null; cap defensively
    return {
        "primary_label": label,
        "subtype": subtype,
        "confidence": round(confidence, 2),
        "anchor_quote": anchor,
    }


def call_ollama(
    user_text: str,
    prev_assistant_text: str,
    *,
    host: str | None = None,
    model: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Call local Ollama and return the parsed classifier object.

    Raises OllamaUnavailable on network/timeout failure (host unreachable).
    Returns None on JSON-parse failures (successful HTTP but malformed body).
    Caller decides what to do with None — typically: skip this turn, don't bump
    watermark for it.

    Note: ``scripts/lib/llm_client.py`` has a DIFFERENT ``call_ollama`` that
    returns None on network errors (no exception). That one serves the Deriver
    escalation chain. This one serves the comm-patterns classifier. Do not
    confuse them — they have opposite error contracts.
    """
    host = host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
    model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
    prompt = _render_prompt(user_text, prev_assistant_text)
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,  # required for qwen3 (memory qwen3_think_false_required)
            "format": "json",
            "options": {"temperature": 0, "num_predict": 400},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise OllamaUnavailable(str(e)) from e
    try:
        envelope = json.loads(raw)
    except Exception:
        return None
    response_text = envelope.get("response", "")
    parsed = _extract_json(response_text)
    return normalize_result(parsed, anchor_fallback=user_text[:600])
