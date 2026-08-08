"""PreToolUse hook: semantic dedup check before memory_store.

Embeds the new memory's description+content, searches existing memories of the
same type via match_memories RPC, and blocks if a similar memory exists under
a DIFFERENT name (same name = intended update, pass through).

Graceful degradation: any failure (no API key, network, RPC error) → allow.
We don't want to block legitimate writes due to infra issues.

Exit codes:
  0 + no JSON  → allow silently
  2 + deny JSON → block with reason shown to assistant
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# Guard: only re-exec when run as script. When imported (e.g. by tests via
# importlib with a non-"__main__" module name), skip the re-exec so the
# module's top-level sys.exit doesn't kill pytest collection.
if __name__ == "__main__" and _venv_py.exists() and Path(sys.executable).resolve() != _venv_py.resolve():
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]))

# ---------------------------------------------------------------------------
# Under venv — safe to import deps
# ---------------------------------------------------------------------------
import httpx
from dotenv import load_dotenv

for _env in [_root / ".env", _root.parent / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break

from supabase import create_client

# recall.py is the deep module (#496-#499) that owns EXCLUDE_TAGS_FROM_RECALL —
# same list used to hide operational artifacts (session snapshots) from
# memory_recall. Reused here so the dedup guard can't drift out of sync with
# the recall path (#1184: snapshot rows were excluded from recall but not
# from dedup, causing false-positive blocks on unrelated working_state writes).
sys.path.insert(0, str(_root / "mcp-memory"))
from recall import (  # noqa: E402
    EXCLUDE_TAGS_FROM_RECALL,  # noqa: F401
    filter_excluded_tags as _filter_excluded_tags,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BLOCK_THRESHOLD = 0.75  # different-name match at this similarity → block
# Calibration note (2026-04-17): voyage-3-lite/512 returns ~0.79 for near-verbatim
# concept paraphrases and ~0.54 for related-but-distinct. 0.75 catches the dup
# case without false-firing on sibling concepts.

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
EMBED_TIMEOUT = 10.0  # keep fast — blocks the tool call

# Auto-generated serial memories (e.g. the status-record skill's one-row-per-
# UTC-date snapshots) are intentionally near-identical: a unique date-keyed name
# + upsert, but ~0.98 cosine to the prior day's row. The cross-name dup gate
# catches *accidental* concept duplication, not a deliberate daily series — so
# exempt anything carrying these series tags. Without this, the status-record
# cron is blocked every day after the first, producing false-zero gaps in the
# trend queries those snapshots exist to feed.
SERIES_EXEMPT_TAGS = {"status-snapshot", "auto-generated"}


def is_exempt_series(tags) -> bool:
    """True if the memory carries a tag marking it as an auto-generated serial
    snapshot, which is intentionally near-identical to its siblings."""
    return isinstance(tags, list) and bool(SERIES_EXEMPT_TAGS.intersection(tags))


def row_exists(client, name: str, project) -> bool:
    """True if a live (project, name) row already exists.

    A memory_store against an existing (project, name) is an upsert of a
    known row, not a candidate for cross-name duplicate detection — the
    unique constraint already owns that identity (#1184). Fail-open: any
    error here just means the dedup guard runs as before, never that a
    real duplicate silently skips the check.
    """
    norm_project = None if project == "global" else project
    try:
        q = client.table("memories").select("id").eq("name", name).is_("deleted_at", "null")
        q = q.eq("project", norm_project) if norm_project is not None else q.is_("project", "null")
        result = q.limit(1).execute()
        return bool(result.data)
    except Exception:
        return False


def allow():
    sys.exit(0)


def block(message: str):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }
    json.dump(out, sys.stdout)
    sys.exit(2)


def embed(text: str) -> list[float] | None:
    """Synchronous Voyage embedding. Returns None on any failure."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            resp = client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VOYAGE_MODEL, "input": [text], "input_type": "query"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception:
        return None


def main():
    # Force UTF-8 decode: on Windows, sys.stdin default codec is cp1251 which
    # mangles Cyrillic memory content from Claude Code (always UTF-8).
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        allow()
    if not raw.strip():
        allow()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        allow()

    tool_name = data.get("tool_name", "")
    if "memory_store" not in tool_name:
        allow()

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        allow()

    new_name = tool_input.get("name") or ""
    new_type = tool_input.get("type") or ""
    new_project = tool_input.get("project")  # may be None
    new_desc = tool_input.get("description") or ""
    new_content = tool_input.get("content") or ""

    if not new_name or not new_type:
        allow()

    # Deliberately-serialized snapshots (status-record etc.) bypass the dup gate.
    if is_exempt_series(tool_input.get("tags")):
        allow()

    embed_text = f"{new_desc}\n{new_content}".strip()
    if not embed_text:
        allow()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        allow()

    try:
        client = create_client(url, key)
    except Exception:
        allow()

    # (project, name) already exists → this is an upsert of a known row, not
    # a new write to check for duplicates. Skip before spending an embedding
    # call. (#1184)
    if row_exists(client, new_name, new_project):
        allow()

    query_embedding = embed(embed_text)
    if query_embedding is None:
        allow()

    try:
        result = client.rpc(
            "match_memories",
            {
                "query_embedding": query_embedding,
                "match_limit": 5,
                "similarity_threshold": BLOCK_THRESHOLD,
                "filter_project": new_project,
                "filter_type": new_type,
            },
        ).execute()
        rows = result.data or []
    except Exception:
        allow()

    # Operational artifacts (session snapshots etc.) are excluded from recall
    # (#417) — exclude them from dedup comparison too, or a snapshot's mixed
    # transcript content false-positives against unrelated writes. (#1184)
    rows = _filter_excluded_tags(rows)

    # Same name = intended update — pass through
    candidates = [r for r in rows if r.get("name") != new_name]
    if not candidates:
        allow()

    top = candidates[0]
    sim = top.get("similarity", 0.0)
    existing_name = top.get("name", "?")
    existing_project = top.get("project") or "global"
    existing_desc = top.get("description") or ""

    block(
        f"Possible duplicate memory (similarity {sim:.2f} ≥ {BLOCK_THRESHOLD}).\n"
        f"Existing: '{existing_name}' ({new_type}, {existing_project})\n"
        f"  — {existing_desc}\n\n"
        f"Options:\n"
        f"1. If updating the same concept → retry with name='{existing_name}' (that triggers upsert).\n"
        f"2. If this is genuinely distinct → make description/content more specific to differentiate, "
        f"then retry.\n"
        f"3. If old memory is obsolete → memory_delete('{existing_name}') first, then retry."
    )


if __name__ == "__main__":
    main()
