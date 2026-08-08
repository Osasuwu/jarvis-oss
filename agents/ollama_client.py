"""Shared Ollama client wrapper.

STAGED-DORMANT: kept intentionally, no live consumer yet. Staged for the
orchestrator's local-inference tick (cheap on-device classification/summary
without spending Claude budget). Do NOT retire this in native-first sweeps —
the dormancy is deliberate, not dead code (decision a0a4ce7b; no tracking
issue by design).

Centralises the `think=False` default needed for Qwen3 models. Without
it, Qwen3 puts output in the `thinking` field and leaves `message.content`
empty — a silent failure mode for any classification agent.

See `docs/agents/ollama-setup.md` for the full rationale.
"""

from __future__ import annotations

from typing import Any

from ollama import Client

from agents.config import AgentConfig, load_config


def get_client(config: AgentConfig | None = None) -> Client:
    """Return an Ollama HTTP client bound to the configured host."""
    cfg = config or load_config()
    return Client(host=cfg.ollama_host)


def chat(
    messages: list[dict[str, str]],
    *,
    config: AgentConfig | None = None,
    think: bool | str = False,
    format: dict[str, Any] | str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    """Send a chat request and return the assistant's text content.

    `think=False` is the default — override only for reasoning models where
    you explicitly want to capture the reasoning trace.

    Pass `format` as a JSON-schema dict (or the string "json") to force
    structured output — the pattern used by classification agents.
    """
    cfg = config or load_config()
    client = get_client(cfg)
    kwargs: dict[str, Any] = {
        "model": cfg.ollama_model,
        "messages": messages,
        "think": think,
        "options": options or {"temperature": 0, "num_predict": 200},
    }
    if format is not None:
        kwargs["format"] = format
    response = client.chat(**kwargs)
    return response["message"]["content"]
