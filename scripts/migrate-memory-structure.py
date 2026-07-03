#!/usr/bin/env python3
"""Memory structure + provenance backfill — Phase 7.4 (#271).

Two cheap, low-risk passes over the live memory set. Guided by decision memory
`old_memory_migration_policy_2026_04_20` (jarvis): cheap migrations now,
expensive ones (dual-embedding, retrospective episodes) deferred or refused.

Pass A — structure rewrite (Haiku-4.5)
  * Target: `feedback` + `project` memories whose content lacks either
    `**Why:**` or `**How to apply:**` sections.
  * Haiku reorganizes the existing text into the canonical layout
    (rule/fact → **Why:** → **How to apply:**) while preserving all facts.
  * Skips any row where Haiku returns `null` (can't extract confidently).
  * Skips anything that already has both markers.

Pass B — provenance backfill (deterministic)
  * Target: rows created before `2026-04-18` (Phase 2c cutoff) with
    `source_provenance = 'legacy:pre-2c'` (the schema-stamped sentinel).
  * New value: `session:<created_at::date>` per the policy memory.
  * Rows after the cutoff are left alone — they deserve individual attention.

Default mode: dry-run. Pass `--apply` to write.

Usage:
    python scripts/migrate-memory-structure.py                    # scope report, dry-run
    python scripts/migrate-memory-structure.py --pass-a           # only structure
    python scripts/migrate-memory-structure.py --pass-b           # only provenance
    python scripts/migrate-memory-structure.py --pass-a --limit 5 # try 5 rows
    python scripts/migrate-memory-structure.py --apply            # actually write
    python scripts/migrate-memory-structure.py --apply --verbose  # loud apply

Requires SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY (Pass A only). .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
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


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-haiku-4-5"
HAIKU_TIMEOUT = 20.0
MAX_TOKENS = 2000

PROVENANCE_CUTOFF_DATE = "2026-04-18"
LEGACY_PROVENANCE_SENTINEL = "legacy:pre-2c"

MIN_CONTENT_LEN_FOR_REWRITE = 120
LENGTH_RATIO_MIN = 0.70
LENGTH_RATIO_MAX = 1.80
MIN_CONFIDENCE = 0.6

WHY_PATTERN = re.compile(r"\*\*Why:?\*\*", re.IGNORECASE)
HOW_PATTERN = re.compile(r"\*\*How to apply:?\*\*", re.IGNORECASE)


SYSTEM_PROMPT = """You restructure personal AI memory entries into a canonical layout.

Input: the raw `content` of a feedback or project memory. Styles vary: a paragraph, bullet list, or already-partial structure.

Canonical layout:
1. Rule / fact / lesson — the first line or two, lead with the takeaway.
2. **Why:** — the rationale / context / incident / motivation that produced the rule. Draw from the ORIGINAL text only.
3. **How to apply:** — when and where to act on this rule in future sessions. Draw from the ORIGINAL text only.

Output strict JSON, nothing before or after:
{
  "rewritten_content": "<markdown that contains both `**Why:**` and `**How to apply:**` sections>" | null,
  "confidence": <float 0..1>,
  "reason": "<one short sentence>"
}

Rules:
- PRESERVE every fact, URL, code block, file path, and number from the original. Do NOT invent, generalize, or compress facts.
- If the original has NO extractable rationale OR NO clear application guidance, set `rewritten_content` to null. Do NOT hallucinate a Why/How-to-apply.
- If the original ALREADY contains both `**Why:**` and `**How to apply:**` markers, set `rewritten_content` to null and `reason` to `already structured`.
- Keep code blocks, links, file paths, lists intact.
- Length target: within ±20% of the original. Short originals stay short.
- Language: match the original (Russian stays Russian; mixed stays mixed).
- Confidence: 0.9+ when the original clearly separates rule / rationale / application. 0.6-0.8 when the structure is implicit but recoverable. < 0.6 means skip (set content null)."""


def has_required_structure(content: str) -> bool:
    """True if content already has both `**Why:**` and `**How to apply:**`."""
    if not content:
        return False
    return bool(WHY_PATTERN.search(content) and HOW_PATTERN.search(content))


def validate_rewrite(original: str, rewritten: str | None) -> tuple[bool, str]:
    """Gate Haiku output: both markers present, length in a sane band."""
    if not rewritten:
        return False, "empty_output"
    if not WHY_PATTERN.search(rewritten):
        return False, "missing_why_section"
    if not HOW_PATTERN.search(rewritten):
        return False, "missing_how_section"
    if not original:
        return True, "ok_empty_original"
    ratio = len(rewritten) / len(original)
    if ratio < LENGTH_RATIO_MIN:
        return False, f"length_collapsed_ratio={ratio:.2f}"
    if ratio > LENGTH_RATIO_MAX:
        return False, f"length_bloated_ratio={ratio:.2f}"
    return True, "ok"


def _parse_json_response(text: str) -> dict | None:
    """Tolerant JSON extraction — model sometimes wraps in ```json blocks."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None
    return None


def heuristic_provenance(created_at: str | None) -> str | None:
    """Map a created_at timestamp to `session:YYYY-MM-DD`.

    Returns None if the timestamp is unparseable.
    """
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return f"session:{dt.date().isoformat()}"


def call_haiku(content: str, api_key: str, http) -> dict | None:
    """POST to Anthropic Messages API. Returns parsed dict or None."""
    if http is None:
        return None
    try:
        resp = http.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=HAIKU_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        return {"_transport_error": str(e)[:200]}

    text = ""
    for block in body.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    parsed = _parse_json_response(text)
    if parsed is None:
        return {"_parse_error": "non-JSON response", "_raw": text[:200]}
    return parsed


def fetch_pass_a_candidates(client, limit: int | None) -> list[dict]:
    """Live feedback+project memories (Phase 5 live-filter in DB)."""
    q = (
        client.table("memories")
        .select("id, name, type, project, content, created_at")
        .in_("type", ["feedback", "project"])
        .is_("expired_at", "null")
        .is_("superseded_by", "null")
        .is_("deleted_at", "null")
        .order("created_at", desc=False)
    )
    if limit:
        q = q.limit(limit * 3)
    rows = (q.execute().data) or []
    candidates = [r for r in rows if not has_required_structure(r.get("content") or "")]
    if limit:
        candidates = candidates[:limit]
    return candidates


def fetch_pass_b_candidates(client, limit: int | None) -> list[dict]:
    """Live rows with sentinel provenance created before the cutoff."""
    q = (
        client.table("memories")
        .select("id, name, project, created_at, source_provenance")
        .eq("source_provenance", LEGACY_PROVENANCE_SENTINEL)
        .lt("created_at", PROVENANCE_CUTOFF_DATE)
        .is_("expired_at", "null")
        .is_("superseded_by", "null")
        .is_("deleted_at", "null")
        .order("created_at", desc=False)
    )
    if limit:
        q = q.limit(limit)
    return (q.execute().data) or []


def run_pass_a(client, api_key: str, candidates: list[dict], dry_run: bool, verbose: bool) -> dict:
    """Haiku-driven structure backfill. Returns a summary dict."""
    try:
        import httpx

        http = httpx.Client()
    except ImportError:
        http = None

    summary = {
        "pass": "A_structure",
        "considered": len(candidates),
        "rewritten": 0,
        "skipped_short": 0,
        "skipped_haiku_null": 0,
        "skipped_validation": 0,
        "skipped_transport": 0,
        "skipped_low_confidence": 0,
        "would_apply": [] if dry_run else None,
        "applied": [] if not dry_run else None,
    }

    if not candidates:
        return summary

    if not api_key:
        print("Pass A skipped: ANTHROPIC_API_KEY not set", file=sys.stderr)
        summary["error"] = "missing_anthropic_key"
        return summary

    for row in candidates:
        mem_id = row.get("id")
        name = row.get("name")
        content = row.get("content") or ""

        if len(content) < MIN_CONTENT_LEN_FOR_REWRITE:
            summary["skipped_short"] += 1
            if verbose:
                print(f"  skip {name}: content too short ({len(content)} chars)", file=sys.stderr)
            continue

        result = call_haiku(content, api_key, http)

        if not result or "_transport_error" in (result or {}):
            summary["skipped_transport"] += 1
            if verbose:
                print(
                    f"  skip {name}: transport error {result.get('_transport_error') if result else ''}",
                    file=sys.stderr,
                )
            continue

        if "_parse_error" in result:
            summary["skipped_transport"] += 1
            if verbose:
                print(f"  skip {name}: parse error", file=sys.stderr)
            continue

        rewritten = result.get("rewritten_content")
        confidence = result.get("confidence")
        reason = result.get("reason", "")

        if rewritten is None:
            summary["skipped_haiku_null"] += 1
            if verbose:
                print(f"  skip {name}: haiku returned null ({reason})", file=sys.stderr)
            continue

        if confidence is not None and confidence < MIN_CONFIDENCE:
            summary["skipped_low_confidence"] += 1
            if verbose:
                print(
                    f"  skip {name}: confidence {confidence} < {MIN_CONFIDENCE} ({reason})",
                    file=sys.stderr,
                )
            continue

        ok, why = validate_rewrite(content, rewritten)
        if not ok:
            summary["skipped_validation"] += 1
            if verbose:
                print(f"  skip {name}: {why}", file=sys.stderr)
            continue

        summary["rewritten"] += 1
        row_report = {
            "id": mem_id,
            "name": name,
            "project": row.get("project"),
            "orig_len": len(content),
            "new_len": len(rewritten),
            "confidence": confidence,
        }
        if verbose:
            print(
                f"  rewrite {name}: {len(content)}→{len(rewritten)} chars, conf={confidence}",
                file=sys.stderr,
            )

        if dry_run:
            summary["would_apply"].append(row_report)
        else:
            try:
                client.table("memories").update(
                    {
                        "content": rewritten,
                        "content_updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", mem_id).execute()
                summary["applied"].append(row_report)
            except Exception as e:
                summary["skipped_transport"] += 1
                if verbose:
                    print(f"  apply fail {name}: {str(e)[:100]}", file=sys.stderr)

    return summary


def run_pass_b(client, candidates: list[dict], dry_run: bool, verbose: bool) -> dict:
    """Deterministic provenance backfill."""
    summary = {
        "pass": "B_provenance",
        "considered": len(candidates),
        "rewritten": 0,
        "skipped_unparsable_date": 0,
        "would_apply": [] if dry_run else None,
        "applied": [] if not dry_run else None,
    }

    for row in candidates:
        mem_id = row.get("id")
        name = row.get("name")
        new_prov = heuristic_provenance(row.get("created_at"))
        if not new_prov:
            summary["skipped_unparsable_date"] += 1
            if verbose:
                print(f"  skip {name}: unparsable created_at", file=sys.stderr)
            continue
        row_report = {
            "id": mem_id,
            "name": name,
            "project": row.get("project"),
            "old": row.get("source_provenance"),
            "new": new_prov,
        }
        summary["rewritten"] += 1
        if verbose:
            print(f"  provenance {name}: {row_report['old']} -> {new_prov}", file=sys.stderr)
        if dry_run:
            summary["would_apply"].append(row_report)
        else:
            try:
                client.table("memories").update(
                    {"source_provenance": new_prov}
                ).eq("id", mem_id).execute()
                summary["applied"].append(row_report)
            except Exception as e:
                if verbose:
                    print(f"  apply fail {name}: {str(e)[:100]}", file=sys.stderr)

    return summary


def emit_migration_event(client, summary: dict) -> None:
    """Record a `memory_migration` event so the run is auditable."""
    try:
        client.table("events").insert(
            {
                "event_type": "memory_migration",
                "severity": "info",
                "repo": "your-username/your-repo",
                "source": "migrate-memory-structure.py",
                "title": f"Phase 7.4 backfill: {summary.get('total_rewritten', 0)} rows",
                "payload": summary,
            }
        ).execute()
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Memory structure + provenance backfill — Phase 7.4 (#271)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Default is dry-run.",
    )
    parser.add_argument(
        "--pass-a",
        action="store_true",
        help="Run only Pass A (Haiku structure rewrite).",
    )
    parser.add_argument(
        "--pass-b",
        action="store_true",
        help="Run only Pass B (provenance backfill).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap candidates per pass (None = all).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Per-row stderr log.",
    )
    args = parser.parse_args()

    run_a = args.pass_a or (not args.pass_a and not args.pass_b)
    run_b = args.pass_b or (not args.pass_a and not args.pass_b)

    try:
        from supabase import create_client
    except ImportError:
        print("Error: supabase library not available", file=sys.stderr)
        return 2

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL, SUPABASE_KEY not set", file=sys.stderr)
        return 2
    client = create_client(url, key)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    dry_run = not args.apply
    start = time.time()

    overall = {
        "phase": "7.4",
        "dry_run": dry_run,
        "limit": args.limit,
        "ran_pass_a": run_a,
        "ran_pass_b": run_b,
        "passes": [],
    }

    if run_a:
        cands = fetch_pass_a_candidates(client, args.limit)
        print(f"Pass A (structure): {len(cands)} candidates", file=sys.stderr)
        summary_a = run_pass_a(client, api_key, cands, dry_run, args.verbose)
        overall["passes"].append(summary_a)

    if run_b:
        cands = fetch_pass_b_candidates(client, args.limit)
        print(f"Pass B (provenance): {len(cands)} candidates", file=sys.stderr)
        summary_b = run_pass_b(client, cands, dry_run, args.verbose)
        overall["passes"].append(summary_b)

    overall["total_rewritten"] = sum(p.get("rewritten", 0) for p in overall["passes"])
    overall["elapsed_seconds"] = round(time.time() - start, 2)

    print(json.dumps(overall, indent=2, ensure_ascii=False))

    if not dry_run:
        emit_migration_event(client, overall)

    return 0


if __name__ == "__main__":
    sys.exit(main())
