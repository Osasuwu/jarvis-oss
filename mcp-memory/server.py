"""Jarvis Memory MCP Server — thin entry, dispatch, and main().

#360 split: handlers + helpers live in mcp-memory/handlers/*,
mcp-memory/client.py, mcp-memory/embeddings.py, mcp-memory/tools_schema.py.

server.py keeps:
- MCP `Server("jarvis-memory")` instance + tool/dispatch wiring
- `_compute_write_embeddings` (kept here so its monkeypatched dependencies
  — `_embed`, `EMBEDDING_MODEL_PRIMARY/SECONDARY` — resolve via this module)
- Re-exports of every handler/helper for backwards compatibility with the
  test suite (`from server import _handle_store, _rrf_merge, ...`)
- `main()` — stdio entry

Usage in .mcp.json:
{
  "memory": {
    "type": "stdio",
    "command": "python",
    "args": ["mcp-memory/server.py"],
    "env": {
      "SUPABASE_URL": "https://xxx.supabase.co",
      "SUPABASE_KEY": "eyJ...",
      "VOYAGE_API_KEY": "pa-..."
    }
  }
}
"""

from __future__ import annotations

import sys

# Alias __main__ -> 'server' so that handler modules' `import server`
# (used to reach shared utilities via this module for test monkeypatch
# propagation) doesn't re-execute this file when launched as a script
# (`python mcp-memory/server.py`) and recursively re-enter the handler
# imports below. No-op when already imported as `server` (pytest path).
sys.modules.setdefault("server", sys.modules[__name__])

import asyncio
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root (two levels up from mcp-memory/server.py).
_env_candidates = [
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
]
for _env_path in _env_candidates:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        break

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

# Phase 2b classifier — local module, optional at runtime.
try:
    from classifier import (  # type: ignore
        classify_write,
        ClassifierDecision,
        CLASSIFIER_MODEL,
    )
except Exception:  # pragma: no cover — defensive
    classify_write = None  # type: ignore
    ClassifierDecision = None  # type: ignore
    CLASSIFIER_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Re-exports — backwards compat surface for tests and external callers.
# `from server import X` must keep working for every previously-defined name.
# ---------------------------------------------------------------------------

from client import _get_client, _audit_log  # noqa: F401
from embeddings import (  # noqa: F401
    VOYAGE_API_URL,
    VOYAGE_MODEL,
    EMBED_TIMEOUT,
    EMBEDDING_MODEL_PRIMARY,
    EMBEDDING_MODEL_SECONDARY,
    EMBEDDING_MODELS,
    _model_slot,
    _embed,
    _embed_batch,
    _embed_upsert_fields,
    _embed_query,
    _canonical_embed_text,
)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("jarvis-memory")

VALID_TYPES = ("user", "project", "decision", "feedback", "reference")
VALID_GOAL_PRIORITIES = ("P0", "P1", "P2")
VALID_GOAL_STATUSES = ("active", "achieved", "paused", "abandoned")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# ---------------------------------------------------------------------------
# Compute-time write embeddings — kept in server.py because tests monkeypatch
# `server._embed` and `server.EMBEDDING_MODEL_PRIMARY` (lookups inside this
# function resolve via *this* module's namespace, where the monkeypatches land).
# ---------------------------------------------------------------------------


async def _compute_write_embeddings(text: str) -> dict:
    """Produce the full write-side embedding payload for one row.

    Always embeds with PRIMARY. If SECONDARY is set AND different from
    PRIMARY, also embeds with SECONDARY and merges columns. Returns a dict
    ready to splat into the upsert `data`.

    Failure mode: if PRIMARY embed fails → return {} (skip embedding). If
    PRIMARY succeeds but SECONDARY fails → write PRIMARY only (single-leg
    recovery; a future consolidation/backfill can fill the gap).
    """
    primary_vec = await _embed(text, model=EMBEDDING_MODEL_PRIMARY)
    if primary_vec is None:
        return {}
    fields = _embed_upsert_fields(primary_vec, EMBEDDING_MODEL_PRIMARY)
    if EMBEDDING_MODEL_SECONDARY and EMBEDDING_MODEL_SECONDARY != EMBEDDING_MODEL_PRIMARY:
        secondary_vec = await _embed(text, model=EMBEDDING_MODEL_SECONDARY)
        if secondary_vec is not None:
            fields.update(_embed_upsert_fields(secondary_vec, EMBEDDING_MODEL_SECONDARY))
    return fields


# ---------------------------------------------------------------------------
# Tool registration + dispatch
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    from tools_schema import tool_definitions

    return tool_definitions()


MAX_RESULT_CHARS = 100_000  # Claude Code default truncates at ~20k; memories can be large


def _big_result(content: list[TextContent]) -> CallToolResult:
    """Wrap content in CallToolResult with maxResultSizeChars to prevent truncation."""
    return CallToolResult(
        content=content,
        meta={"anthropic/maxResultSizeChars": MAX_RESULT_CHARS},
    )


# Handler imports come AFTER server / _compute_write_embeddings / _big_result
# are defined so the `import server` at the top of each handler module
# resolves correctly during the recursive import chain.

from handlers.goal import (  # noqa: E402, F401
    GOAL_FIELDS,
    _format_goal,
    _handle_goal_set,
    _handle_goal_list,
    _handle_goal_get,
    _handle_goal_update,
)
from handlers.memory import (  # noqa: E402, F401
    _handle_store,
    _handle_recall,
    _handle_get,
    _handle_list,
    _handle_delete,
    _handle_restore,
    _handle_memory_mark_stale,
    _handle_memory_unmark_stale,
    _handle_graph,
    _hybrid_recall,
    _keyword_recall,
    _rrf_merge,
    _format_memories,
    _create_auto_links,
    _expand_with_links,
    _apply_classifier_decision,
    _apply_legacy_supersede,
    _apply_temporal_scoring,
    _enrich_with_confidence,
    _cosine_sim,
    _parse_pgvector,
    _upsert_known_unknown,
    _resolve_known_unknowns,
    _touch_memories,
    _emit_recall_event,
    _backfill_missing_embeddings,
    _hydrate_neighbors,
    _graph_overview,
    _graph_links,
    _graph_clusters,
    TEMPORAL_HALF_LIVES,
    DEFAULT_HALF_LIFE,
    ACCESS_BOOST_MAX,
    ACCESS_HALF_LIFE,
    CONFIDENCE_FLOOR,
    LINK_SIM_THRESHOLD,
    SUPERSEDE_SIM_THRESHOLD,
    CLASSIFIER_TRIGGER_SIM,
    CLASSIFIER_APPLY_THRESHOLD,
    CONSOLIDATION_SIM_THRESHOLD,
    CONSOLIDATION_COUNT,
    MAX_AUTO_LINKS,
    MAX_CLASSIFIER_NEIGHBORS,
    GAP_DEDUP_SIM,
    GAP_THRESHOLD,
    SIMILARITY_THRESHOLD,
    EXCLUDE_TAGS_FROM_RECALL,
    _filter_excluded_tags,
    PROD_RECALL_CONFIG,
    RecallConfig,
    RecallHit,
    recall,
)
from handlers.events import (  # noqa: E402, F401
    _handle_events_list,
    _handle_events_mark_processed,
    _handle_event_claim_next,
    _handle_event_mark_processed,
    _handle_event_park,
    _handle_event_requeue,
)
from handlers.outcome import (  # noqa: E402, F401
    _handle_outcome_record,
    _handle_outcome_update,
    _handle_outcome_list,
    _handle_memory_calibration_summary,
    _handle_fok_calibration_summary,
)
from handlers.credential import (  # noqa: E402, F401
    _handle_credential_list,
    _handle_credential_add,
    _handle_credential_check_expiry,
)
from handlers.decision import (  # noqa: E402, F401
    _looks_like_uuid,
    _resolve_memory_refs,
    _handle_record_decision,
)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent] | CallToolResult:
    try:
        # Goal tools
        if name == "goal_set":
            return await _handle_goal_set(arguments)
        elif name == "goal_list":
            return _big_result(await _handle_goal_list(arguments))
        elif name == "goal_get":
            return _big_result(await _handle_goal_get(arguments))
        elif name == "goal_update":
            return await _handle_goal_update(arguments)
        # Memory tools
        elif name == "memory_store":
            return await _handle_store(arguments)
        elif name == "memory_recall":
            return _big_result(await _handle_recall(arguments))
        elif name == "memory_get":
            return _big_result(await _handle_get(arguments))
        elif name == "memory_list":
            return _big_result(await _handle_list(arguments))
        elif name == "memory_delete":
            return await _handle_delete(arguments)
        elif name == "memory_restore":
            return await _handle_restore(arguments)
        elif name == "memory_mark_stale":
            return await _handle_memory_mark_stale(arguments)
        elif name == "memory_unmark_stale":
            return await _handle_memory_unmark_stale(arguments)
        # Graph tools
        elif name == "memory_graph":
            return _big_result(await _handle_graph(arguments))
        # Outcome tracking tools (Pillar 3)
        elif name == "outcome_record":
            return await _handle_outcome_record(arguments)
        elif name == "record_decision":
            return await _handle_record_decision(arguments)
        elif name == "outcome_update":
            return await _handle_outcome_update(arguments)
        elif name == "outcome_list":
            return _big_result(await _handle_outcome_list(arguments))
        elif name == "memory_calibration_summary":
            return _big_result(await _handle_memory_calibration_summary(arguments))
        elif name == "fok_calibration_summary":
            return _big_result(await _handle_fok_calibration_summary(arguments))
        # Credential registry tools (Pillar 9)
        elif name == "credential_list":
            return _big_result(await _handle_credential_list(arguments))
        elif name == "credential_add":
            return await _handle_credential_add(arguments)
        elif name == "credential_check_expiry":
            return _big_result(await _handle_credential_check_expiry(arguments))
        # Event tools
        elif name == "events_list":
            return _big_result(await _handle_events_list(arguments))
        elif name == "events_mark_processed":
            return await _handle_events_mark_processed(arguments)
        # Event queue FSM tools (#739)
        elif name == "event_claim_next":
            return await _handle_event_claim_next(arguments)
        elif name == "event_mark_processed_fsm":
            return await _handle_event_mark_processed(arguments)
        elif name == "event_park":
            return await _handle_event_park(arguments)
        elif name == "event_requeue":
            return await _handle_event_requeue(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
