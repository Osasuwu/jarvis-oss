"""Feeling-of-knowing batch processor — Phase 5 metacognition (#250).

For each `memory_recall` event from the last 24h lacking a fok_verdict, calls
Haiku-4.5 with {query, truncated returned memory contents} → {verdict, confidence, reason}.
Writes verdicts back to `events.payload`, emits one `fok_run` event summarizing the batch.

Handles failures gracefully: Haiku down / JSON parse fail → verdict='unknown',
confidence null, don't crash the run.

If known_unknowns table exists (Phase 5 co-dependency #249), insert low-confidence
insufficient verdicts where top_sim < 0.6.

Usage:
    python scripts/fok-batch.py                    # defaults, all events
    python scripts/fok-batch.py --limit 10         # batch of 10
    python scripts/fok-batch.py --dry-run          # judge but don't write
    python scripts/fok-batch.py --dry-run --limit 2  # smoke-test

Env: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY. .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
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

try:
    import httpx
except ImportError:
    httpx = None


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 500
MAX_RETURNED_CONTENT_CHARS = 2000  # truncate returned memories in Haiku prompt

DEFAULT_LIMIT = 50

SYSTEM_PROMPT = """You are a feeling-of-knowing judge for a personal AI agent's memory system.

A memory_recall event captures a query and the IDs/snippets of memories returned.
Judge: did the returned set sufficiently answer the query?

Output strict JSON, nothing else:
{
  "verdict": "sufficient" | "partial" | "insufficient",
  "confidence": <float 0..1>,
  "reason": "<one short sentence>"
}

Rules:
  - sufficient: returned memories directly answer the query. Agent can proceed with confidence.
  - partial: some returned memories are relevant, but key details are missing. Agent needs follow-up.
  - insufficient: returned set is irrelevant or empty. Query reveals a gap in memory.
  - confidence: 0.9+ for obvious cases; 0.5-0.7 for judgment calls; <0.5 when unsure.
"""


def build_user_message(query: str, returned_results: list[dict]) -> str:
    """Build user message for Haiku: query + top N memory snippets."""
    if not returned_results:
        return f"Query: {query}\n\nNo memories returned."

    msg = f"Query: {query}\n\nReturned memories (top {len(returned_results)}):\n\n"
    for i, r in enumerate(returned_results[:5], 1):
        mem_id = r.get("id", "unknown")
        sim = r.get("similarity")
        content = r.get("content", "")
        if len(content) > MAX_RETURNED_CONTENT_CHARS:
            content = content[: MAX_RETURNED_CONTENT_CHARS - 3] + "..."
        sim_str = f"{sim:.2f}" if isinstance(sim, (int, float)) else "unknown"
        msg += f"{i}. [{mem_id}] (similarity: {sim_str})\n{content}\n\n"

    return msg


def judge_via_haiku(query: str, returned_results: list[dict]) -> dict:
    """Call Haiku-4.5 to judge sufficiency. Return {verdict, confidence, reason}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "verdict": "unknown",
            "confidence": None,
            "reason": "ANTHROPIC_API_KEY not set",
        }

    if httpx is None:
        return {
            "verdict": "unknown",
            "confidence": None,
            "reason": "httpx not available",
        }

    user_msg = build_user_message(query, returned_results)

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": MAX_TOKENS,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            resp.raise_for_status()
            body = resp.json()

            # Extract text from response
            text_content = ""
            for block in body.get("content", []):
                if block.get("type") == "text":
                    text_content += block.get("text", "")

            # Try to extract JSON from the response (may be embedded in prose)
            json_match = re.search(r"\{[^{}]*\}", text_content)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "verdict": result.get("verdict", "unknown"),
                    "confidence": result.get("confidence"),
                    "reason": result.get("reason", ""),
                }

            return {
                "verdict": "unknown",
                "confidence": None,
                "reason": "Could not parse JSON from Haiku response",
            }

    except json.JSONDecodeError:
        return {
            "verdict": "unknown",
            "confidence": None,
            "reason": "Invalid JSON in Haiku response",
        }
    except httpx.HTTPError as e:
        return {
            "verdict": "unknown",
            "confidence": None,
            "reason": f"Haiku API error: {str(e)[:100]}",
        }
    except Exception as e:
        return {
            "verdict": "unknown",
            "confidence": None,
            "reason": f"Error calling Haiku: {str(e)[:100]}",
        }


def check_known_unknowns_exists(client) -> bool:
    """Guard: check if known_unknowns table exists via lightweight select probe.

    Previous impl called client.rpc("query", ...) which doesn't exist in our
    schema, so it always raised and silently disabled insertion.
    """
    try:
        client.table("known_unknowns").select("id").limit(1).execute()
        return True
    except Exception:
        # Missing relation / 404 / any other access error → assume absent
        return False


def try_insert_known_unknown(client, event: dict, verdict_dict: dict, project: str) -> None:
    """Optionally insert insufficient verdicts into known_unknowns (#249 co-dep).

    Phase 5.3-δ: reads verdict from canonical source (verdict_dict from Haiku),
    not from legacy event.payload.
    """
    payload = event.get("payload") or {}
    verdict = verdict_dict.get("verdict")
    confidence = verdict_dict.get("confidence")
    top_sim = payload.get("top_sim", 0.0)
    query = payload.get("query", "")

    # Only insert: verdict=insufficient, confidence < 0.7, top_sim < 0.6.
    # Use explicit None check — (confidence or 1.0) coerces a legitimate 0.0
    # into 1.0, which would skip the truly-uncertain cases we most want to log.
    effective_confidence = confidence if confidence is not None else 1.0
    if verdict != "insufficient" or effective_confidence >= 0.7 or top_sim >= 0.6:
        return
    if not query:
        return

    # Guard: table must exist
    if not check_known_unknowns_exists(client):
        return

    try:
        # Dedupe: exact-query + status='open' key. The previous semantic check
        # did `.gte("similarity", 0.7).limit(1)` which returned any row with
        # high sim to *anything*, disabling inserts once the table had any hit.
        existing = (
            client.table("known_unknowns")
            .select("id,hit_count")
            .eq("query", query)
            .eq("status", "open")
            .limit(1)
            .execute()
        )
        if existing.data:
            row = existing.data[0]
            client.table("known_unknowns").update(
                {
                    "hit_count": (row.get("hit_count") or 1) + 1,
                    "last_seen_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", row["id"]).execute()
            return

        client.table("known_unknowns").insert(
            {
                "query": query,
                "top_similarity": top_sim,
                "context": {
                    "project": project,
                    "reason": payload.get("fok_reason", ""),
                    "event_id": event.get("id"),
                    "source": "fok_batch",
                },
            }
        ).execute()
    except Exception:
        pass


def format_judgment_for_display(
    event_id: str, verdict: dict, payload: dict, judged_at: str
) -> dict:
    """Format judgment data for dry-run display."""
    return {
        "event_id": event_id,
        "query": payload.get("query", ""),
        "project": payload.get("project", "your-username/your-repo"),
        "verdict": verdict.get("verdict", "unknown"),
        "confidence": verdict.get("confidence"),
        "rationale": verdict.get("reason", ""),
        "judge_model": "claude-haiku-4-5-20251001",
        "judge_version": "5.3-γ",
        "judged_at": judged_at,
    }


def write_verdict_to_event(client, event_id: str, verdict: dict) -> None:
    """Write FOK verdict to fok_judgments (canonical only).

    Legacy mirror (events.payload.fok_verdict) dropped in 5.3-δ (#445).
    """
    db_payload: dict = {}
    judged_at = datetime.now(timezone.utc).isoformat()

    try:
        current_event = client.table("events").select("payload").eq("id", event_id).execute()
        if not current_event.data:
            return
        db_payload = current_event.data[0].get("payload") or {}

        # Only write to canonical fok_judgments table
        client.table("fok_judgments").upsert(
            {
                "recall_event_id": event_id,
                "query": db_payload.get("query", ""),
                "project": db_payload.get("project", "your-username/your-repo"),
                "verdict": verdict.get("verdict", "unknown"),
                "confidence": verdict.get("confidence"),
                "rationale": verdict.get("reason", ""),
                "judge_model": "claude-haiku-4-5-20251001",
                "judge_version": "5.3-δ",
                "judged_at": judged_at,
            },
            on_conflict="recall_event_id",
        ).execute()
    except Exception:
        pass


def write_event(client, summary: dict, project: str) -> None:
    """Emit fok_run summary event."""
    try:
        payload = dict(summary)
        payload["project"] = project
        client.table("events").insert(
            {
                "event_type": "fok_run",
                "severity": "info",
                "repo": project,
                "source": "fok_batch",
                "title": f"FOK batch run: processed {summary.get('processed', 0)}",
                "payload": payload,
            }
        ).execute()
    except Exception:
        pass


def fetch_events(client, limit: int) -> list[dict]:
    """Fetch memory_recall events without corresponding fok_judgments rows from the last 24h.

    Applies order + limit server-side. We over-fetch by 3x because the
    fok_judgments matching filter can only be applied client-side (LEFT JOIN style).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        resp = (
            client.table("events")
            .select("*")
            .eq("event_type", "memory_recall")
            .gte("created_at", cutoff.isoformat())
            .order("created_at", desc=False)
            .limit(max(limit * 3, limit))
            .execute()
        )
        events = resp.data or []
        unfudged = []

        # Check which events lack corresponding fok_judgments rows
        try:
            existing_judgments = (
                client.table("fok_judgments")
                .select("recall_event_id")
                .gte("judged_at", cutoff.isoformat())
                .execute()
            )
            judgment_event_ids = set(
                row.get("recall_event_id") for row in (existing_judgments.data or [])
            )
        except Exception:
            judgment_event_ids = set()

        for e in events:
            event_id = e.get("id")
            if event_id not in judgment_event_ids:
                unfudged.append(e)
            if len(unfudged) >= limit:
                break
        return unfudged
    except Exception:
        return []


def main():
    """Main: fetch events, judge each, write results."""
    parser = argparse.ArgumentParser(
        description="Feeling-of-knowing batch processor for memory system (#250)"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Batch size (default 50)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Judge but don't write verdicts or insert unknowns",
    )
    args = parser.parse_args()

    # Fail fast on degenerate config (#649). If the API key is missing or httpx
    # failed to import, judge_via_haiku returns a sentinel {"verdict":"unknown"}
    # for *every* event, and write_verdict_to_event would persist that garbage
    # into fok_judgments (154 missing-key rows recorded 2026-05-05 — a config
    # error masquerading as a run). Refuse before any DB work. Guard only the
    # real run; --dry-run keeps its smoke-test role (fetch/format/summary, no
    # writes). Mirrors telegram-notify-hook.py's not-dry-run + missing-config gate.
    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        # Single source of truth for both the guard and the message: whichever
        # precondition is unmet names itself; None means the config is sound.
        reason = (
            "ANTHROPIC_API_KEY not set"
            if not api_key
            else "httpx not available"
            if httpx is None
            else None
        )
        if reason:
            print(
                f"Error: {reason} — every FoK verdict would be a degenerate 'unknown'. "
                "Refusing to persist. (Use --dry-run to preview without writing.)",
                file=sys.stderr,
            )
            sys.exit(1)

    # Supabase client
    try:
        from supabase import create_client

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            print("Error: SUPABASE_URL, SUPABASE_KEY not set", file=sys.stderr)
            sys.exit(1)
        client = create_client(url, key)
    except ImportError:
        print("Error: supabase library not available", file=sys.stderr)
        sys.exit(1)

    # Fetch unfudged events
    events = fetch_events(client, args.limit)
    if not events:
        print("No unfudged memory_recall events found.", file=sys.stdout)
        return

    print(f"Processing {len(events)} events...", file=sys.stderr)

    verdicts_by_type = defaultdict(int)
    start_time = time.time()
    dry_run_writes = []

    for event in events:
        event_id = event.get("id")
        payload = event.get("payload") or {}
        query = payload.get("query", "")
        returned_ids = payload.get("returned_ids", [])
        top_sim = payload.get("top_sim", 0.0)
        # Optional per-memory similarities (Phase 5.1+). Same length/order
        # as returned_ids when present; else fall back to [top_sim, nan, ...]
        # so only the top result claims the known similarity.
        returned_sims = payload.get("returned_similarities") or []

        # Fetch memory contents for context; preserve recall ranking order.
        returned_results = []
        if returned_ids:
            top_ids = returned_ids[:5]
            try:
                result = client.table("memories").select("id,content").in_("id", top_ids).execute()
                by_id = {str(m.get("id")): m for m in (result.data or [])}
                for idx, mid in enumerate(top_ids):
                    mem = by_id.get(str(mid))
                    if not mem:
                        continue
                    if idx < len(returned_sims):
                        sim = returned_sims[idx]
                    elif idx == 0:
                        sim = top_sim
                    else:
                        sim = None  # unknown — don't mislead the judge
                    returned_results.append(
                        {
                            "id": mem.get("id"),
                            "content": mem.get("content", ""),
                            "similarity": sim,
                        }
                    )
            except Exception:
                pass

        # Judge via Haiku
        verdict = judge_via_haiku(query, returned_results)
        verdicts_by_type[verdict["verdict"]] += 1

        print(
            f"Event {event_id}: {verdict['verdict']} (conf={verdict.get('confidence', 'N/A')})",
            file=sys.stderr,
        )

        # Collect dry-run output
        if args.dry_run:
            judged_at = datetime.now(timezone.utc).isoformat()
            judgment = format_judgment_for_display(event_id, verdict, payload, judged_at)
            dry_run_writes.append({"target": "fok_judgments", "record": judgment})
        else:
            # Write verdict
            write_verdict_to_event(client, event_id, verdict)

            # Try to insert into known_unknowns if applicable
            if verdict["verdict"] == "insufficient":
                try_insert_known_unknown(client, event, verdict, "your-username/your-repo")

    elapsed = time.time() - start_time

    # Emit summary event
    summary = {
        "processed": len(events),
        "verdicts": dict(verdicts_by_type),
        "elapsed_seconds": elapsed,
        "dry_run": args.dry_run,
    }
    if args.dry_run and dry_run_writes:
        summary["would_write"] = dry_run_writes

    print(f"\nSummary: {json.dumps(summary, indent=2)}", file=sys.stdout)

    if not args.dry_run:
        write_event(client, summary, "your-username/your-repo")


if __name__ == "__main__":
    main()
