"""Shared configuration for reactive-core agents.

Loaded from environment variables (with sensible defaults for local dev).
See `.env.example` for the canonical list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# NOTE: `.env` loading happens in the application entry point (see `main.py`),
# not at module import. This keeps `load_config()` deterministic in tests —
# callers that clear env vars get the documented defaults regardless of
# whether a local `.env` is present.


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for Pillar 7 agents."""

    ollama_host: str
    ollama_model: str
    # Direct-Postgres DSN for wake_driver's LISTEN/NOTIFY socket (the PostgREST
    # client can't LISTEN). Empty string is intentionally allowed at load time —
    # same idiom as the Supabase fields below — so imports/tests don't require a
    # live DSN; `wake_driver._build_psycopg_queue` raises a clear error naming
    # AGENTS_POSTGRES_URL if someone tries to open the socket without it.
    postgres_url: str
    # Supabase bridge — shared with Claude Code's MCP memory server. Empty
    # strings are intentionally allowed at load time so imports/tests don't
    # require live credentials; `agents.supabase_client.get_client` raises
    # with a clear error if someone tries to use the bridge without them.
    supabase_url: str
    supabase_key: str


def load_config() -> AgentConfig:
    """Read environment variables and return an immutable config object."""
    return AgentConfig(
        ollama_host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        ollama_model=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        postgres_url=os.environ.get("AGENTS_POSTGRES_URL", ""),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_KEY", ""),
    )
