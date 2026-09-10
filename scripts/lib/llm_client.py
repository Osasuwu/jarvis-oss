"""Shared LLM client — Ollama primary with DeepSeek fallback.

Patterns (not a library — too small to extract):
  - ``call_ollama(prompt, ...)`` — POST to ``/api/generate``.
  - ``call_deepseek(prompt, ...)`` — POST to OpenAI-compatible ``/v1/chat/completions``.
  - ``call_llm(prompt)`` — tries Ollama first, falls back to DeepSeek.

All return the response text on success, or None on any error (timeout,
network, parse).  Caller decides what to do with None.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = (
    "qwen2.5-coder:14b"  # Workshop primary (docs/agents/ollama-workshop-bench-538.md)
)
DEFAULT_OLLAMA_TIMEOUT_S = 120

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_TIMEOUT_S = 180

# Max output tokens for both backends. 1500 was a Deriver-era heuristic but
# triggered silent truncation on verbose sessions (5 candidates × 2-5 sentences
# each easily exceeds 1500). 4096 is comfortably within both Ollama qwen2.5
# and DeepSeek limits.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def call_ollama(
    prompt: str,
    *,
    host: str | None = None,
    model: str | None = None,
    timeout_s: int = DEFAULT_OLLAMA_TIMEOUT_S,
    system_prompt: str | None = None,
    format_json: bool = True,
    return_usage: bool = False,
) -> str | dict | None:
    """Call Ollama ``/api/generate`` with *prompt* and return the response text.

    When *return_usage* is True, returns a dict with keys ``text``,
    ``input_tokens`` (prompt_eval_count), ``output_tokens`` (eval_count),
    and ``model`` — or None on error.

    Returns None (bare) on any error or empty response.  *system_prompt* is
    passed as the ``system`` field (Ollama 0.3+).  When *format_json* is true
    (default) the request sets ``"format": "json"``.

    Note: ``scripts/comm_patterns/classifier.py`` has a DIFFERENT ``call_ollama``
    that raises ``OllamaUnavailable`` on network errors (not None). That one
    serves the comm-patterns classifier pipeline. This one serves the Deriver
    escalation chain. Do not confuse them — they have opposite error contracts.
    """
    host = host or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": DEFAULT_MAX_OUTPUT_TOKENS},
    }
    if system_prompt:
        body["system"] = system_prompt
    if format_json:
        body["format"] = "json"

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        envelope = json.loads(raw)
    except Exception:
        return None
    response_text = envelope.get("response")
    # Distinguish "Ollama returned successfully but with empty/null content"
    # from "connection failed". The former returns None too, but logging the
    # difference helps debug silent quota burn on DeepSeek fallback when
    # Ollama is the actual root cause (aborted generation, OOM, model
    # producing an empty completion).
    if not response_text and envelope.get("done"):
        done_reason = envelope.get("done_reason", "unknown")
        print(
            f"[llm_client] Ollama returned done={envelope.get('done')!r} "
            f"reason={done_reason!r} with empty response — falling back to DeepSeek",
            file=sys.stderr,
        )

    if return_usage:
        return {
            "text": response_text,
            "input_tokens": envelope.get("prompt_eval_count", 0) or 0,
            "output_tokens": envelope.get("eval_count", 0) or 0,
            "model": model,
        }
    return response_text


# ---------------------------------------------------------------------------
# DeepSeek (OpenAI-compatible API)
# ---------------------------------------------------------------------------


def call_deepseek(
    prompt: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    timeout_s: int = DEFAULT_DEEPSEEK_TIMEOUT_S,
    system_prompt: str | None = None,
    format_json: bool = True,
    return_usage: bool = False,
) -> str | dict | None:
    """Call DeepSeek chat-completions API and return the response text.

    When *return_usage* is True, returns a dict with keys ``text``,
    ``input_tokens``, ``output_tokens``, and ``model`` — or None on error.

    Returns None (bare) on any error.  Uses ``requests``-style JSON POST via
    stdlib ``urllib`` — no extra dependency.

    When *format_json* is true (default), sets
    ``"response_format": {"type": "json_object"}`` per the OpenAI-compatible
    contract DeepSeek implements. Without it, DeepSeek wraps JSON output in
    explanatory prose, which downstream parsers must strip.
    """
    base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
    model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        return None

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    }
    if format_json:
        body["response_format"] = {"type": "json_object"}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        envelope = json.loads(raw)
    except Exception:
        return None

    choices = envelope.get("choices", [])
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content")
    usage = envelope.get("usage", {})

    if return_usage:
        return {
            "text": content,
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
            "model": model,
        }
    return content


# ---------------------------------------------------------------------------
# Orchestrator: Ollama primary, DeepSeek fallback
# ---------------------------------------------------------------------------


def call_llm(
    prompt: str,
    *,
    system_prompt: str | None = None,
    format_json: bool = True,
    return_usage: bool = False,
) -> str | dict | None:
    """Try Ollama first; fall back to DeepSeek on any error.

    When *return_usage* is True, returns a dict with the winning tier's
    token info (see ``call_ollama`` / ``call_deepseek``). Returns None
    only if **both** backends fail.
    """
    result = call_ollama(
        prompt,
        system_prompt=system_prompt,
        format_json=format_json,
        return_usage=return_usage,
    )
    if result is not None:
        return result
    # Fallback — forward format_json so DeepSeek also gets JSON-mode enforcement.
    # Without this, DeepSeek's prose wrapping triggered the non-greedy regex
    # bug in scripts/deriver/pipeline.py (now also fixed).
    return call_deepseek(
        prompt,
        system_prompt=system_prompt,
        format_json=format_json,
        return_usage=return_usage,
    )
