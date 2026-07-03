"""S4: Dreamer pipeline — scheduled cross-corpus consolidation pass.

Phase 1 — producer: emit new candidates and merge proposals from a
corpus of pending + accepted ``feedback`` memories using an LLM.

Two-phase pipeline:
  1. ``consolidate(corpus)`` — pure function returning
     ``(new_candidates, merge_proposals)``. Both lists may be empty.
  2. Write results to the ``memories`` table with ``requires_review=true``
     and ``source_provenance='dreamer:<run-id>'``.

Trigger: (pending-candidate count >= 30) OR (>= 7 days since last run).

Blocked by:
  - #681 (S1 — ``merge_targets`` column + atomic merge RPC) — **done**
  - #683 (S3 — Deriver feeds the candidate corpus Dreamer reads) —
    **in-progress**; the pure function and scheduled task are independent.
    Without S3, the corpus fetch will return zero rows and the run will
    be a no-op rather than an error.

Usage::

    python scripts/dreamer-run.py                          # triggered run
    python scripts/dreamer-run.py --force                  # skip trigger check
    python scripts/dreamer-run.py --force --dry-run        # smoke test only
    python scripts/dreamer-run.py --model deepseek-chat    # alternate LLM

Env: SUPABASE_URL, SUPABASE_KEY, plus one of:
  - ``ANTHROPIC_API_KEY`` (default — uses ``claude-sonnet-5``).
  - ``DREAMER_API_KEY`` + ``DREAMER_API_URL`` + ``DREAMER_MODEL``
    (alt provider such as Ollama / DeepSeek — ``DREAMER_API_URL`` must be a
    ``/v1/messages``-compatible endpoint that accepts the Anthropic Messages
    schema).
  - ``DREAMER_API_KEY`` alone overrides the key; ``DREAMER_MODEL`` alone
    overrides the model.

``.env`` is auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    for c in (here.parent / ".env", here.parent.parent / ".env"):
        if c.exists():
            load_dotenv(c, override=True)
            break
except ImportError:
    pass

import httpx
from supabase import create_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PENDING_THRESHOLD = 30
DAYS_SINCE_LAST_RUN = 7
MAX_CORPUS_ROWS = 200
CORPUS_LOOKBACK_DAYS = 90

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 4000
DEFAULT_TIMEOUT = 60.0

# Max outputs per category — keeps the response within token budget and review
# burden manageable per ADR-0003 (≤20 per Dreamer run).
MAX_NEW_CANDIDATES = 5
MAX_MERGE_PROPOSALS = 5


DREAMER_SYSTEM_PROMPT = """You are analyzing a corpus of feedback memories from a personal AI agent's long-term memory store.

Your task: identify cross-corpus insights that are worth capturing as new memories, AND identify groups of near-duplicate or overlapping memories that should be merged.

## New candidates

Identify patterns, connections, or important facts that emerge from looking at the corpus as a whole but aren't explicitly captured in any single memory. Examples:
- A recurring theme or preference across multiple interactions
- A project-specific pattern the agent should remember
- A general insight about how the user works

Each new candidate needs:
- name (snake_case identifier)
- type (one of: user, project, decision, feedback, reference — choose the most appropriate)
- project: "jarvis" for project-specific insights; null for universal/user insights
- description (one sentence summary)
- content (full detail)
- tags (relevant tags, include "dreamer")
- reasoning (why this insight emerges from the corpus)

## Merge proposals

Identify groups of 2+ memories that refer to the same underlying fact and would serve better as a single unified memory. Only propose a merge if the unified version would be strictly better than keeping the parts separate.

Each merge proposal needs:
- name (snake_case identifier)
- type (the most appropriate type for the merged result)
- project: same as the memories being merged (or null if global)
- description (one sentence summary)
- content (full merged content — synthesize from all source memories)
- tags (union of relevant tags, include "dreamer")
- merge_targets: array of UUIDs of memories to supersede
- reasoning (why these should be merged and how the new version improves on the parts)

## Rules
- Be conservative. Prefer fewer, high-quality outputs over many speculative ones.
- A new candidate must represent information NOT already captured in the corpus as a single memory. If the corpus already contains the fact, don't repeat it.
- Merge proposals should only reference UUIDs that appear in the corpus below.
- Max 5 new candidates + 5 merge proposals per run.
- When in doubt, emit nothing. Empty arrays are valid.

Output strict JSON matching this schema, nothing else. No prose before or after.

{
  "new_candidates": [
    {
      "name": "snake_case_name",
      "type": "user|project|decision|feedback|reference",
      "project": "jarvis | null",
      "description": "one sentence summary",
      "content": "full content",
      "tags": ["tag1", "tag2"],
      "reasoning": "why this insight emerges"
    }
  ],
  "merge_proposals": [
    {
      "name": "snake_case_name",
      "type": "user|project|decision|feedback|reference",
      "project": "jarvis | null",
      "description": "one sentence summary",
      "content": "full merged content",
      "tags": ["tag1", "tag2"],
      "merge_targets": ["uuid1", "uuid2"],
      "reasoning": "why these should be merged"
    }
  ]
}"""


# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------


def _llm_config() -> tuple[str, str, str]:
    """Return (api_key, api_url, model) for the LLM call.

    Resolution order:
      1. ``DREAMER_API_KEY`` / ``DREAMER_API_URL`` / ``DREAMER_MODEL`` — alt
         provider (Ollama, DeepSeek). The URL must be a ``/v1/messages``-
         compatible endpoint matching the Anthropic Messages schema.
      2. ``ANTHROPIC_API_KEY`` — default key; URL and model are the Anthropic
         defaults.
    """
    api_key = os.environ.get("DREAMER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    api_url = os.environ.get("DREAMER_API_URL") or DEFAULT_ANTHROPIC_URL
    model = os.environ.get("DREAMER_MODEL") or DEFAULT_ANTHROPIC_MODEL
    return api_key, api_url, model


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------


def fetch_pending_count(client) -> int:
    """Count feedback memories with ``requires_review=true`` (unreviewed candidates)."""
    rows = (
        client.table("memories")
        .select("id", count="exact")
        .eq("requires_review", True)
        .eq("type", "feedback")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return rows.count if hasattr(rows, "count") and rows.count is not None else 0


def fetch_days_since_last_run(client) -> timedelta | None:
    """Return elapsed time since the last ``dreamer_run`` event, or None if no prior run."""
    rows = (
        client.table("events")
        .select("created_at")
        .eq("event_type", "dreamer_run")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    data = rows.data or []
    if not data:
        return None
    last_ts = data[0].get("created_at")
    if not last_ts:
        return None
    try:
        last = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - last
    except (TypeError, ValueError):
        return None


def check_trigger(client) -> tuple[bool, str]:
    """Return (should_run: bool, reason: str).

    Fires when ``pending_count >= PENDING_THRESHOLD`` OR
    ``days_since >= DAYS_SINCE_LAST_RUN`` (or no prior run exists).
    """
    pending = fetch_pending_count(client)
    if pending >= PENDING_THRESHOLD:
        return True, f"pending_candidate_count={pending} >= threshold={PENDING_THRESHOLD}"

    delta = fetch_days_since_last_run(client)
    if delta is None:
        return True, "no prior dreamer_run found — first run"
    days = delta.days  # whole days for display; full timedelta used for comparison
    if delta >= timedelta(days=DAYS_SINCE_LAST_RUN):
        return True, f"days_since_last_run={days} >= threshold={DAYS_SINCE_LAST_RUN}"

    return False, (
        f"pending_candidate_count={pending} < {PENDING_THRESHOLD} AND "
        f"days_since_last_run={days} < {DAYS_SINCE_LAST_RUN}"
    )


# ---------------------------------------------------------------------------
# Corpus fetching
# ---------------------------------------------------------------------------


def fetch_corpus(client, *, max_rows: int = MAX_CORPUS_ROWS) -> list[dict]:
    """Read pending + accepted ``feedback`` memories from the last 90 days.

    Ordered by ``created_at`` descending; capped at ``max_rows`` (oldest
    rows dropped first when over the limit).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CORPUS_LOOKBACK_DAYS)).isoformat()
    rows = (
        client.table("memories")
        .select(
            "id, name, type, description, content, tags, project, created_at, updated_at, requires_review"
        )
        .eq("type", "feedback")
        .gte("created_at", cutoff)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .limit(max_rows)
        .execute()
    )
    return rows.data or []


# ---------------------------------------------------------------------------
# Pure consolidate function
# ---------------------------------------------------------------------------


def _build_corpus_prompt(corpus: list[dict]) -> str:
    """Format the corpus as a text block for the LLM."""
    lines = [
        f"CORPUS: {len(corpus)} feedback memories (last {CORPUS_LOOKBACK_DAYS} days)",
        "",
    ]
    for i, m in enumerate(corpus, 1):
        block = [
            f"MEMORY {i}:",
            f"  id: {m.get('id', '?')}",
            f"  name: {m.get('name', '?')}",
            f"  project: {m.get('project', 'null')}",
            f"  requires_review: {m.get('requires_review', False)}",
            f"  created_at: {m.get('created_at', '?')}",
        ]
        if m.get("tags"):
            block.append(f"  tags: {', '.join(str(t) for t in m['tags'])}")
        if m.get("description"):
            block.append(f"  description: {m['description']}")
        if m.get("content"):
            # Truncate long content to keep prompt within budget
            content = m["content"]
            if len(content) > 800:
                content = content[:800] + "…"
            block.append(f"  content: {content}")
        lines.append("\n".join(block))

    lines.append(
        "\n---\n"
        "Analyze the above corpus and output strict JSON with "
        "new_candidates and/or merge_proposals. "
        "Empty arrays are valid — only emit when there is a genuine insight."
    )
    return "\n".join(lines)


def _parse_response(text: str, corpus_ids: set[str]) -> tuple[list[dict], list[dict]] | None:
    """Parse LLM JSON response.

    Returns ``(new_candidates, merge_proposals)`` or ``None`` on
    unrecoverable parse failure. Invalid entries are silently dropped
    rather than failing the entire response.

    Corpus-level validation:
      - ``merge_targets`` must reference UUIDs present in ``corpus_ids``.
      - Duplicate names within each category are dropped (keep first).
    """
    if not text:
        return None
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return None
    try:
        data = json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None

    raw_candidates = data.get("new_candidates") or []
    raw_proposals = data.get("merge_proposals") or []

    if not isinstance(raw_candidates, list) or not isinstance(raw_proposals, list):
        return None

    # Validate new candidates
    seen_names: set[str] = set()
    new_candidates: list[dict] = []
    for c in raw_candidates:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name in seen_names:
            continue
        mtype = c.get("type")
        if mtype not in ("user", "project", "decision", "feedback", "reference"):
            continue
        c.setdefault("description", "")
        c.setdefault("content", "")
        c.setdefault("tags", [])
        c.setdefault("reasoning", "")
        # Strip merge_targets if LLM incorrectly set them on a new candidate
        c.pop("merge_targets", None)
        seen_names.add(name)
        new_candidates.append(c)

    # Validate merge proposals
    seen_names_p: set[str] = set()
    merge_proposals: list[dict] = []
    for p in raw_proposals:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name in seen_names_p:
            continue
        mtype = p.get("type")
        if mtype not in ("user", "project", "decision", "feedback", "reference"):
            continue
        mt = p.get("merge_targets")
        if not isinstance(mt, list) or len(mt) < 2:
            continue
        # Filter to known corpus UUIDs
        valid_mt = [u for u in mt if isinstance(u, str) and u in corpus_ids]
        if len(valid_mt) < 2:
            continue
        p.setdefault("description", "")
        p.setdefault("content", "")
        p.setdefault("tags", [])
        p.setdefault("reasoning", "")
        p["merge_targets"] = valid_mt
        seen_names_p.add(name)
        merge_proposals.append(p)

    return (new_candidates, merge_proposals)


def _fallback_empty(why: str) -> tuple[list[dict], list[dict]]:
    """Return empty results when the LLM call or parse fails."""
    print(f"  ! consolidate fallback: {why}", file=sys.stderr)
    return ([], [])


def consolidate(
    corpus: list[dict],
    *,
    api_key: str,
    api_url: str | None = None,
    model: str = DEFAULT_ANTHROPIC_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[dict], list[dict]]:
    """Pure consolidation function — the Dreamer's core.

    Takes a corpus of ``feedback`` memory dicts, calls the configured LLM,
    and returns ``(new_candidates, merge_proposals)``. Both lists may be
    empty when the corpus is sparse or the LLM finds nothing worth emitting.

    ``api_url`` defaults to the Anthropic Messages API. Compatible with
    any ``/v1/messages`` endpoint (Ollama, DeepSeek) that accepts the
    Anthropic schema.
    """
    if not api_key:
        return _fallback_empty("no API key configured")
    if not corpus:
        return ([], [])

    resolved_url = api_url or DEFAULT_ANTHROPIC_URL
    corpus_ids = {m["id"] for m in corpus if isinstance(m.get("id"), str)}
    user_message = _build_corpus_prompt(corpus)

    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": DREAMER_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message}],
    }

    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(
                resolved_url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as e:
        return _fallback_empty(f"http_error: {type(e).__name__}")
    except ValueError:
        return _fallback_empty("invalid_json_payload")

    blocks = payload.get("content", [])
    text = ""
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text", "")
            break

    parsed = _parse_response(text, corpus_ids)
    if parsed is None:
        return _fallback_empty("unparseable_response")

    candidates, proposals = parsed

    # Enforce per-category caps
    candidates = candidates[:MAX_NEW_CANDIDATES]
    proposals = proposals[:MAX_MERGE_PROPOSALS]

    return (candidates, proposals)


# ---------------------------------------------------------------------------
# DB writers
# ---------------------------------------------------------------------------

VALID_TYPES = ("user", "project", "decision", "feedback", "reference")


def _row_for_memory(
    item: dict,
    run_id: str,
    *,
    merge_targets: list[str] | None = None,
) -> dict:
    """Build a DB row dict from a candidate or proposal."""
    mtype = item.get("type", "project")
    if mtype not in VALID_TYPES:
        mtype = "project"
    row: dict = {
        "project": item.get("project"),
        "name": item["name"].strip(),
        "type": mtype,
        "description": (item.get("description") or "").strip()[:500],
        "content": (item.get("content") or "").strip(),
        "tags": [t.strip() for t in (item.get("tags") or []) if isinstance(t, str) and t.strip()],
        "requires_review": True,
        "source_provenance": f"dreamer:{run_id}",
        "derivation_run_id": run_id,
    }
    if merge_targets is not None:
        row["merge_targets"] = merge_targets
    return row


def insert_candidates(client, candidates: list[dict], run_id: str) -> list[str]:
    """Insert new candidate memories. Returns list of inserted IDs."""
    ids: list[str] = []
    for c in candidates:
        row = _row_for_memory(c, run_id)
        try:
            resp = client.table("memories").insert(row).execute()
            data = resp.data or []
            if not data:
                print(
                    f"  ! candidate insert returned no data ({c.get('name')}) "
                    "— possible RLS rejection (anon key lacks sandcastle provenance)",
                    file=sys.stderr,
                )
            elif data[0].get("id"):
                ids.append(data[0]["id"])
        except Exception as e:
            print(f"  ! candidate insert failed ({c.get('name')}): {e}", file=sys.stderr)
    return ids


def insert_merge_proposals(client, proposals: list[dict], run_id: str) -> list[str]:
    """Insert merge proposal memories. Returns list of inserted IDs."""
    ids: list[str] = []
    for p in proposals:
        row = _row_for_memory(p, run_id, merge_targets=p["merge_targets"])
        try:
            resp = client.table("memories").insert(row).execute()
            data = resp.data or []
            if not data:
                print(
                    f"  ! merge_proposal insert returned no data ({p.get('name')}) "
                    "— possible RLS rejection (anon key lacks sandcastle provenance)",
                    file=sys.stderr,
                )
            elif data[0].get("id"):
                ids.append(data[0]["id"])
        except Exception as e:
            print(f"  ! merge_proposal insert failed ({p.get('name')}): {e}", file=sys.stderr)
    return ids


# ---------------------------------------------------------------------------
# Event emitter
# ---------------------------------------------------------------------------


def write_event(
    client,
    *,
    run_id: str,
    status: str,
    title: str,
    payload: dict,
) -> str | None:
    """Insert one ``dreamer_run`` row into events. Best-effort."""
    try:
        resp = (
            client.table("events")
            .insert(
                {
                    "event_type": "dreamer_run",
                    "severity": "info" if status in ("ok", "dry-run") else "medium",
                    "repo": "your-username/your-repo",
                    "source": "scheduled_task",
                    "title": title,
                    "payload": payload,
                }
            )
            .execute()
        )
        data = resp.data or []
        return data[0]["id"] if data else None
    except Exception as e:
        print(f"! event insert failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--force",
        action="store_true",
        help="Skip the trigger check (pending-count >= 30 OR >= 7d since last run). "
        "Use for ad-hoc runs and smoke tests.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline but skip all DB writes (inserts + event). "
        "Prints candidate/proposal details to stderr.",
    )
    p.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the LLM model (overrides DREAMER_MODEL and ANTHROPIC defaults).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"LLM API timeout in seconds (default {DEFAULT_TIMEOUT})",
    )
    args = p.parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    # Mirror mcp-memory/client.py: prefer service-role key so host writes bypass
    # the RLS policy (source_provenance LIKE 'sandcastle:%' for anon INSERTs).
    # Dreamer writes 'dreamer:<run_id>' provenance — anon key gets 403 from RLS.
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not sb_key:
        sb_key = os.environ.get("SUPABASE_KEY")
        if sb_key and not os.environ.get("SANDCASTLE_RUN_ID"):
            print(
                "WARNING: dreamer using SUPABASE_KEY (anon). RLS requires "
                "source_provenance LIKE 'sandcastle:%' for anon INSERTs — "
                "dreamer writes 'dreamer:...' provenance and inserts will be rejected. "
                "Set SUPABASE_SERVICE_KEY in .env.",
                file=sys.stderr,
            )
    if not sb_url or not sb_key:
        print(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY (or SUPABASE_KEY) missing from env",
            file=sys.stderr,
        )
        return 2

    api_key, api_url, model = _llm_config()
    if args.model:
        model = args.model
    if not api_key:
        print(
            "No LLM API key found. Set DREAMER_API_KEY or ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2

    client = create_client(sb_url, sb_key)
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    # ---- Trigger check ---------------------------------------------------
    if not args.force:
        should_run, reason = check_trigger(client)
        if not should_run:
            recap = {
                "status": "skipped",
                "reason": reason,
                "run_id": run_id,
                "started_at": started_at,
            }
            print(json.dumps(recap, indent=2, default=str))
            return 0
        print(f"Trigger fired: {reason}", file=sys.stderr)
    else:
        print("--force: trigger check skipped", file=sys.stderr)

    # ---- Corpus fetch ----------------------------------------------------
    corpus = fetch_corpus(client)
    print(f"Corpus: {len(corpus)} feedback memory/ies", file=sys.stderr)

    if not corpus:
        recap = {
            "status": "ok",
            "reason": "corpus_empty",
            "run_id": run_id,
            "started_at": started_at,
            "corpus_size": 0,
            "new_candidates": 0,
            "merge_proposals": 0,
        }
        event_id = None
        if not args.dry_run:
            event_id = write_event(
                client,
                run_id=run_id,
                status="ok",
                title="Dreamer run — corpus empty, no output",
                payload={**recap, "event_id": None},
            )
        recap["event_id"] = event_id
        print(json.dumps(recap, indent=2, default=str))
        return 0

    # ---- Consolidate -----------------------------------------------------
    t0 = time.monotonic()
    new_candidates, merge_proposals = consolidate(
        corpus, api_key=api_key, api_url=api_url, model=model, timeout=args.timeout
    )
    duration = time.monotonic() - t0

    print(
        f"Consolidation: {len(new_candidates)} candidate(s), "
        f"{len(merge_proposals)} merge proposal(s) in {duration:.1f}s",
        file=sys.stderr,
    )

    # ---- Write results ---------------------------------------------------
    candidate_ids: list[str] = []
    proposal_ids: list[str] = []

    if not args.dry_run:
        if new_candidates:
            candidate_ids = insert_candidates(client, new_candidates, run_id)
            print(
                f"  Inserted {len(candidate_ids)}/{len(new_candidates)} candidates",
                file=sys.stderr,
            )
        if merge_proposals:
            proposal_ids = insert_merge_proposals(client, merge_proposals, run_id)
            print(
                f"  Inserted {len(proposal_ids)}/{len(merge_proposals)} merge proposals",
                file=sys.stderr,
            )
    else:
        print(
            f"  --dry-run: would insert {len(new_candidates)} candidate(s), "
            f"{len(merge_proposals)} merge proposal(s)",
            file=sys.stderr,
        )
        if new_candidates:
            print("  New candidates:", file=sys.stderr)
            for c in new_candidates:
                print(f"    - {c.get('name')}: {c.get('description', '')}", file=sys.stderr)
        if merge_proposals:
            print("  Merge proposals:", file=sys.stderr)
            for mp in merge_proposals:
                targets = mp.get("merge_targets", [])
                print(
                    f"    - {mp.get('name')} (merge {targets}): {mp.get('description', '')}",
                    file=sys.stderr,
                )
        candidate_ids = [f"(dry-run) {c.get('name', '?')}" for c in new_candidates]
        proposal_ids = [f"(dry-run) {p.get('name', '?')}" for p in merge_proposals]

    # ---- Event -----------------------------------------------------------
    payload = {
        "run_id": run_id,
        "started_at": started_at,
        "duration_s": round(duration, 2),
        "corpus_size": len(corpus),
        "new_candidates": len(new_candidates),
        "candidate_inserted": len(candidate_ids),
        "merge_proposals": len(merge_proposals),
        "proposal_inserted": len(proposal_ids),
        "model": model,
        "dry_run": args.dry_run,
        "force": args.force,
    }
    total_inserted = len(candidate_ids) + len(proposal_ids)
    title_status = "dry-run" if args.dry_run else "ok"
    title = (
        f"Dreamer run ({title_status}) — {len(new_candidates)} candidates, "
        f"{len(merge_proposals)} merge proposals "
        f"(corpus {len(corpus)}, {duration:.1f}s)"
    )
    event_id = write_event(
        client,
        run_id=run_id,
        status=title_status,
        title=title,
        payload=payload,
    )

    recap = {
        "status": title_status,
        "event_id": event_id,
        "run_id": run_id,
        "title": title,
        "started_at": started_at,
        "duration_s": round(duration, 2),
        "corpus_size": len(corpus),
        "new_candidates": len(new_candidates),
        "merge_proposals": len(merge_proposals),
        "candidate_ids": candidate_ids,
        "proposal_ids": proposal_ids,
        "dry_run": args.dry_run,
        "needs_review": (not args.dry_run) and total_inserted > 0,
    }
    print(json.dumps(recap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
