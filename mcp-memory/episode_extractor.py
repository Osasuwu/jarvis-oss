"""Phase 4 — episodic-layer extractor.

Background worker that drains the `episodes` table (raw "what happened"
records written by hooks/skills/autonomous code) and distills batches
into candidate memories via a Haiku synthesis step.

Pipeline per candidate:
  1. Embed the canonical form (same as server.py's Phase 2a write path).
  2. If embedding succeeded, look up neighbors via `find_similar_memories`.
  3. If any neighbor passes `CLASSIFIER_TRIGGER_SIM` (~0.70), call the
     Phase 2b classifier for an ADD/UPDATE/DELETE/NOOP judgment.
  4. NOOP at confidence >= `CLASSIFIER_APPLY_THRESHOLD` → skip insertion
     (the candidate is judged a duplicate of an existing memory).
  5. Otherwise → insert into `memories` directly with
     `source_provenance='episode:<first-batch-id>'`. The classifier's
     decision (when it ran) is recorded in `memory_review_queue` with
     status='pending' for audit — the extractor never mutates existing
     neighbors; that path lives in server.py where rowcount checks guard
     against races.

Classifier invocation is CONDITIONAL (only when neighbors exist). No
neighbors → straight insert without a queue entry. Embedding failure is
treated as "no neighbors" and also short-circuits the classifier.

Theory (CLS, Tulving) and production (Letta, LangMem, A-MEM) agree on
the same shape: a non-lossy episodic buffer, with semantic extraction
running offline. Direct-to-semantic writes cause catastrophic
interference. Hooks push cheap raw records; this module is where
actual memory synthesis happens, detached from the write latency path.

Run modes:
  python mcp-memory/episode_extractor.py                  # one batch
  python mcp-memory/episode_extractor.py --batch-size 10  # custom size
  python mcp-memory/episode_extractor.py --dry-run        # no DB writes

Environment:
  SUPABASE_URL / SUPABASE_KEY   — required
  ANTHROPIC_API_KEY             — required for synthesis (and classifier
                                  when it runs). Missing key leaves
                                  episodes unprocessed for retry.
  VOYAGE_API_KEY                — best-effort. Missing or erroring keeps
                                  the pipeline alive but stores candidates
                                  without embeddings and skips the
                                  classifier round-trip entirely.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root .env (mirrors server.py's lookup) and sibling module imports.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

try:
    from dotenv import load_dotenv

    for _env_path in [_HERE.parent / ".env", _HERE.parent.parent / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path, override=True)
            break
except ImportError:  # pragma: no cover — .env loading is optional
    pass

import httpx  # noqa: E402

# classifier.py is a pure module (no top-level side effects) — safe to import.
from classifier import classify_write  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
SYNTHESIZER_MODEL = "claude-haiku-4-5"
SYNTHESIZER_TIMEOUT = 30.0  # seconds — offline batch, can afford more than classifier
SYNTHESIZER_MAX_TOKENS = 2000

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-3-lite"
EMBED_TIMEOUT = 30.0

VALID_MEM_TYPES = ("user", "project", "decision", "feedback", "reference")

DEFAULT_BATCH_SIZE = 20
MAX_PAYLOAD_CHARS_PER_EPISODE = 800  # truncate giant tool-call payloads

# Classifier integration thresholds (mirror server.py's Phase 2b values).
NEIGHBOR_SIM_THRESHOLD = 0.60
NEIGHBOR_LIMIT = 5
CLASSIFIER_TRIGGER_SIM = 0.70
CLASSIFIER_APPLY_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    name: str
    type: str
    description: str
    content: str
    tags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "content": self.content,
            "tags": self.tags,
        }


@dataclass
class BatchResult:
    episode_ids: list[str]
    candidates_synthesized: int
    candidates_inserted: int
    candidates_skipped_noop: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Supabase client — self-contained, mirrors server.py's lazy init
# ---------------------------------------------------------------------------


_supabase = None


def get_client():
    global _supabase
    if _supabase is not None:
        return _supabase

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set to run the episode extractor."
        )

    from supabase import create_client

    _supabase = create_client(url, key)
    return _supabase


# ---------------------------------------------------------------------------
# Embedding — minimal re-implementation (server.py is protected; we can't
# share helpers, but Voyage's REST surface is tiny).
# ---------------------------------------------------------------------------


async def _embed(text: str) -> list[float] | None:
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key or not text:
        return None
    try:
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
            resp = await client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VOYAGE_MODEL, "input": [text], "input_type": "document"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None


def _canonical_embed_text(name: str, description: str, tags: list[str], content: str) -> str:
    """Same canonical form server.py uses (Phase 2a) — keeps neighbor similarity
    comparable across episode-derived and regular writes."""
    parts: list[str] = []
    if name:
        parts.append(name.replace("_", " "))
    if tags:
        parts.append("tags: " + ", ".join(tags))
    if description:
        parts.append(description)
    if content:
        parts.append(content)
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Synthesis prompt
# ---------------------------------------------------------------------------


SYNTHESIZER_SYSTEM_PROMPT = """You are a memory-synthesis worker for a personal AI agent.

You receive a BATCH of raw EPISODES — low-level records of what the agent did or saw recently (tool calls, user messages, assistant decisions, observations). Your job is to distill them into candidate MEMORIES that are worth saving to long-term storage.

Output one memory per durable, non-obvious fact/decision/preference that a future session would benefit from knowing. Skip ephemeral operational detail (which tool ran, which file was read) — those belong in the episode log, not long-term memory.

Each candidate memory must have:
  - name: slug-style identifier, lowercase_with_underscores, under 60 chars
  - type: one of user / project / decision / feedback / reference
    * user: about the person (role, goals, preferences, knowledge)
    * project: ongoing work context, initiatives, bugs, goals, state
    * decision: a choice made with reasoning ("chose X because Y")
    * feedback: guidance about how to collaborate (corrections, validations)
    * reference: pointers to external systems (URLs, tool names, doc locations)
  - description: one sentence, used for relevance ranking
  - content: the full memory body. Lead with the fact/rule; for feedback and project memories include **Why:** and **How to apply:** lines.
  - tags: 2-5 short lowercase tags for filtering

Rules:
  - Emit 0 candidates if the batch contains no durable content worth saving.
  - Prefer fewer high-quality candidates over many low-signal ones.
  - Never emit state that will be stale in 2 weeks (percentages, status markers, dates tied to in-flight work).
  - De-duplicate within the batch — if two episodes say the same thing, one memory.
  - Content must be self-contained; don't reference "this session" or "the episode above".

Output strict JSON, nothing else:
{
  "candidates": [
    {
      "name": "...",
      "type": "user|project|decision|feedback|reference",
      "description": "...",
      "content": "...",
      "tags": ["...", "..."]
    }
  ]
}

If nothing in the batch is worth saving, return: {"candidates": []}"""


def _truncate(text: str, limit: int = MAX_PAYLOAD_CHARS_PER_EPISODE) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _render_episode(ep: dict) -> str:
    payload = ep.get("payload") or {}
    if isinstance(payload, (dict, list)):
        payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    else:
        payload_str = str(payload)
    payload_str = _truncate(payload_str)
    created = ep.get("created_at", "")
    return (
        f"- id: {ep.get('id', '')}\n"
        f"  actor: {ep.get('actor', '')}\n"
        f"  kind: {ep.get('kind', '')}\n"
        f"  at: {created}\n"
        f"  payload: {payload_str}"
    )


def build_synthesis_user_message(episodes: list[dict]) -> str:
    """Render the batch as a single prompt body. Pure function — tested directly."""
    if not episodes:
        return "EPISODES\n(empty batch)"
    blocks = [_render_episode(ep) for ep in episodes]
    return "EPISODES\n" + "\n\n".join(blocks)


def parse_synthesis_response(text: str) -> list[Candidate]:
    """Parse the model's JSON reply into candidates. Tolerant of stray prose,
    silently drops malformed entries rather than failing the whole batch."""
    if not text:
        return []
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return []
    try:
        data = json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return []

    raw = data.get("candidates")
    if not isinstance(raw, list):
        return []

    out: list[Candidate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        mem_type = str(entry.get("type", "")).strip().lower()
        description = str(entry.get("description", "")).strip()
        content = str(entry.get("content", "")).strip()
        tags_raw = entry.get("tags", []) or []

        if not name or not content:
            continue
        if mem_type not in VALID_MEM_TYPES:
            continue
        if not isinstance(tags_raw, list):
            tags_raw = []
        tags = [str(t).strip().lower() for t in tags_raw if str(t).strip()]

        out.append(
            Candidate(
                name=name,
                type=mem_type,
                description=description,
                content=content,
                tags=tags[:10],  # hard cap
            )
        )
    return out


# ---------------------------------------------------------------------------
# Synthesis HTTP call
# ---------------------------------------------------------------------------


async def synthesize_candidates(
    episodes: list[dict],
    *,
    model: str = SYNTHESIZER_MODEL,
    timeout: float = SYNTHESIZER_TIMEOUT,
) -> list[Candidate] | None:
    """Call Haiku to turn a batch of episodes into candidate memories.

    Return contract:
      - list[Candidate] (possibly empty) → synthesis ran. An empty list means
        the model explicitly judged the batch as having no durable content;
        the caller SHOULD mark the episodes processed.
      - None → synthesis could not run (missing API key, network/API error).
        The caller MUST NOT mark the episodes processed so a later run can
        retry.

    Parse-level noise (stray prose, malformed JSON inside an otherwise-OK
    response) is tolerated and collapses to []: we trust the model's
    "nothing here" signal rather than looping forever on a bad shape."""
    if not episodes:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    body = {
        "model": model,
        "max_tokens": SYNTHESIZER_MAX_TOKENS,
        "system": SYNTHESIZER_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_synthesis_user_message(episodes)}],
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            payload = resp.json()
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, ValueError):
        return None

    blocks = payload.get("content", [])
    text = ""
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            break
    return parse_synthesis_response(text)


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def fetch_unprocessed(client, limit: int) -> list[dict]:
    """Fetch the next batch of unprocessed episodes (oldest first — preserves
    causal ordering within a session)."""
    resp = (
        client.table("episodes")
        .select("id, actor, kind, payload, created_at")
        .is_("processed_at", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def mark_processed(client, episode_ids: list[str]) -> None:
    if not episode_ids:
        return
    client.table("episodes").update(
        {
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
    ).in_("id", episode_ids).execute()


def _find_neighbors(client, embedding: list[float]) -> list[dict]:
    """Look up semantically-similar existing memories via the Phase 2b RPC.
    Returns [] on any failure — classifier call is optional, not critical."""
    try:
        resp = client.rpc(
            "find_similar_memories",
            {
                "query_embedding": embedding,
                "exclude_id": "00000000-0000-0000-0000-000000000000",
                "match_limit": NEIGHBOR_LIMIT,
                "similarity_threshold": NEIGHBOR_SIM_THRESHOLD,
                "filter_type": None,
            },
        ).execute()
        return resp.data or []
    except Exception:
        return []


def _hydrate_neighbors(client, rows: list[dict]) -> list[dict]:
    """find_similar_memories returns id/name/type/similarity only; classifier
    needs description+content for a real comparison. One round-trip."""
    ids = [r["id"] for r in rows if r.get("id")]
    if not ids:
        return rows
    try:
        full = (
            client.table("memories")
            .select("id, name, type, description, content, tags")
            .in_("id", ids)
            .execute()
        )
        by_id = {row["id"]: row for row in (full.data or [])}
    except Exception:
        return rows

    hydrated = []
    for r in rows:
        extra = by_id.get(r.get("id"), {})
        hydrated.append(
            {
                "id": r.get("id"),
                "name": r.get("name") or extra.get("name", ""),
                "type": r.get("type") or extra.get("type", ""),
                "similarity": r.get("similarity", 0),
                "description": extra.get("description", ""),
                "content": extra.get("content", ""),
                "tags": extra.get("tags", []) or [],
            }
        )
    return hydrated


def _insert_candidate(
    client,
    candidate: Candidate,
    embedding: list[float] | None,
    source_provenance: str,
) -> str | None:
    """Upsert-by-(project, name) for project-scoped, manual upsert for global.
    Mirrors server.py's _handle_store logic just enough to get the row in.

    We deliberately set project=None (global) for episode-derived memories
    until we have signals to scope them — the synthesizer doesn't currently
    distinguish projects."""
    data = {
        "type": candidate.type,
        "name": candidate.name,
        "content": candidate.content,
        "description": candidate.description,
        "project": None,
        "tags": candidate.tags,
        "source_provenance": source_provenance,
    }
    if embedding is not None:
        data["embedding"] = embedding
        data["embedding_model"] = VOYAGE_MODEL
        data["embedding_version"] = "v2"

    try:
        existing = (
            client.table("memories")
            .select("id")
            .eq("name", candidate.name)
            .is_("project", "null")
            .limit(1)
            .execute()
        )
        if existing.data:
            stored_id = existing.data[0]["id"]
            client.table("memories").update(data).eq("id", stored_id).execute()
            return stored_id
        result = client.table("memories").insert(data).execute()
        return result.data[0]["id"] if result.data else None
    except Exception:
        return None


def _queue_classifier_decision(
    client,
    candidate_id: str,
    decision,
    neighbors: list[dict],
) -> None:
    """Record the classifier's call on an episode-derived candidate.

    We do NOT mutate neighbors from the extractor path — neighbor mutation
    belongs to the synchronous write path in server.py, where rowcount
    checks guard against races. Because no mutation is ever applied here,
    status is always 'pending' (per schema semantics: 'auto_applied' means
    the decision was actually applied) and `reviewed_by` is left unset —
    the owner (or a consolidation job) is the real reviewer."""
    try:
        client.table("memory_review_queue").insert(
            {
                "candidate_id": candidate_id,
                "decision": decision.decision,
                "target_id": decision.target_id,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "classifier_model": "claude-haiku-4-5",
                "neighbors_seen": [
                    {"id": n.get("id"), "name": n.get("name"), "similarity": n.get("similarity")}
                    for n in neighbors
                ],
                "status": "pending",
            }
        ).execute()
    except Exception:
        pass  # queue is audit-only; never block the extractor


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def process_candidate(
    client,
    candidate: Candidate,
    source_provenance: str,
    *,
    dry_run: bool = False,
) -> tuple[str, str | None]:
    """Embed → (optional) classify → insert. Returns (action, memory_id).
    action is one of: 'inserted', 'skipped_noop', 'failed'."""
    embed_text = _canonical_embed_text(
        candidate.name, candidate.description, candidate.tags, candidate.content
    )
    embedding = await _embed(embed_text)

    # Neighbor lookup + classifier (optional, best-effort).
    classifier_decision = None
    neighbors: list[dict] = []
    if embedding is not None:
        raw_neighbors = _find_neighbors(client, embedding)
        trigger_neighbors = [
            r for r in raw_neighbors if r.get("similarity", 0) >= CLASSIFIER_TRIGGER_SIM
        ]
        if trigger_neighbors:
            neighbors = _hydrate_neighbors(client, trigger_neighbors)
            try:
                classifier_decision = await classify_write(candidate.to_dict(), neighbors)
            except Exception:
                classifier_decision = None

    # NOOP at high confidence → skip insertion (candidate is redundant).
    if (
        classifier_decision is not None
        and classifier_decision.decision == "NOOP"
        and classifier_decision.confidence >= CLASSIFIER_APPLY_THRESHOLD
    ):
        return ("skipped_noop", None)

    if dry_run:
        return ("inserted", None)

    stored_id = _insert_candidate(client, candidate, embedding, source_provenance)
    if not stored_id:
        return ("failed", None)

    # Record classifier's view for audit (even on ADD) if we called it.
    if classifier_decision is not None:
        _queue_classifier_decision(client, stored_id, classifier_decision, neighbors)

    return ("inserted", stored_id)


async def process_batch(
    client,
    batch_size: int = DEFAULT_BATCH_SIZE,
    *,
    dry_run: bool = False,
) -> BatchResult:
    """Drain one batch of unprocessed episodes.

    Returns a BatchResult that callers (tests, schedulers, manual runs) can
    use to report progress. An empty backlog returns a BatchResult with
    episode_ids=[] — still counts as a successful run."""
    episodes = fetch_unprocessed(client, batch_size)
    episode_ids = [ep["id"] for ep in episodes]
    errors: list[str] = []

    if not episodes:
        return BatchResult(
            episode_ids=[],
            candidates_synthesized=0,
            candidates_inserted=0,
            candidates_skipped_noop=0,
            errors=[],
        )

    candidates = await synthesize_candidates(episodes)
    if candidates is None:
        # Synthesis failed (no API key or transport error). Leave episodes
        # unprocessed so a later run can retry.
        return BatchResult(
            episode_ids=episode_ids,
            candidates_synthesized=0,
            candidates_inserted=0,
            candidates_skipped_noop=0,
            errors=["synthesis failed — episodes left unprocessed for retry"],
        )

    if not candidates:
        # Legitimate empty result — model saw nothing worth saving. Mark
        # processed so we don't loop on the same episodes.
        if not dry_run:
            mark_processed(client, episode_ids)
        return BatchResult(
            episode_ids=episode_ids,
            candidates_synthesized=0,
            candidates_inserted=0,
            candidates_skipped_noop=0,
            errors=[],
        )

    # Use the first episode's id as the provenance anchor. The full batch of
    # episode ids is recoverable via the audit_log / review queue if needed.
    source_provenance = f"episode:{episode_ids[0]}"

    inserted = 0
    skipped_noop = 0
    for candidate in candidates:
        try:
            action, _ = await process_candidate(
                client, candidate, source_provenance, dry_run=dry_run
            )
            if action == "inserted":
                inserted += 1
            elif action == "skipped_noop":
                skipped_noop += 1
            else:
                errors.append(f"failed to insert {candidate.name}")
        except Exception as exc:  # pragma: no cover — defensive
            errors.append(f"{candidate.name}: {exc}")

    # Only mark processed when everything succeeded — partial failures leave
    # the batch for retry. Operators can manually intervene if a candidate
    # keeps failing (e.g. malformed name blocks the insert on every retry).
    if not dry_run and not errors:
        mark_processed(client, episode_ids)

    return BatchResult(
        episode_ids=episode_ids,
        candidates_synthesized=len(candidates),
        candidates_inserted=inserted,
        candidates_skipped_noop=skipped_noop,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drain the episodes table into candidate memories."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Episodes per batch (default {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run synthesis + classifier, print result, don't write to DB.",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    client = get_client()
    result = await process_batch(client, batch_size=args.batch_size, dry_run=args.dry_run)
    print(
        json.dumps(
            {
                "episode_ids": result.episode_ids,
                "candidates_synthesized": result.candidates_synthesized,
                "candidates_inserted": result.candidates_inserted,
                "candidates_skipped_noop": result.candidates_skipped_noop,
                "errors": result.errors,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 1 if result.errors else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
