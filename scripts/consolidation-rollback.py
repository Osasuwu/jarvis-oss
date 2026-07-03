"""Consolidation rollback — inverse of --apply (Phase 5.1b-β, #221).

Reverts a single auto-applied consolidation by its memory_review_queue id.

Reads the stored consolidation_payload for the authoritative member list
(not re-derived from memory_links, which may collide with Phase 2
classifier supersedes), then calls the `rollback_consolidation` RPC.

  * MERGE rollback:  synthesized canonical is soft-deleted, members'
                     lifecycle columns cleared, `consolidates` links dropped.
  * SUPERSEDE_CONSOLIDATION rollback:
                     canonical stays live, losers are restored,
                     `supersedes` links dropped (scoped to this canonical).

Queue row transitions: auto_applied/approved → rolled_back.
A rolled_back cluster is eligible for re-planning next week (unlike
`rejected`, which blocks forever).

Usage:
    python scripts/consolidation-rollback.py <queue_id>
    python scripts/consolidation-rollback.py <queue_id> --json
    python scripts/consolidation-rollback.py --list            # show auto_applied entries

Requires SUPABASE_URL, SUPABASE_KEY. .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

from supabase import create_client


def list_applied(client, limit: int = 20) -> list[dict]:
    """Return recent auto_applied/approved consolidation entries."""
    rows = (
        client.table("memory_review_queue")
        .select(
            "id, decision, confidence, status, target_id, applied_at, "
            "consolidation_payload, reasoning"
        )
        .in_("decision", ["MERGE", "SUPERSEDE_CONSOLIDATION"])
        .in_("status", ["auto_applied", "approved"])
        .order("applied_at", desc=True)
        .limit(limit)
        .execute()
        .data
    ) or []
    return rows


def print_listing(rows: list[dict]) -> None:
    if not rows:
        print(
            "No rollback-eligible entries (need status=auto_applied or approved, "
            "decision=MERGE or SUPERSEDE_CONSOLIDATION)."
        )
        return
    print(f"{'id':36}  {'decision':26}  {'conf':>5}  {'applied_at':20}  {'cluster':7}  members")
    print("-" * 120)
    for r in rows:
        payload = r.get("consolidation_payload") or {}
        cluster_id = payload.get("cluster_id", "?")
        names = payload.get("member_names") or []
        names_str = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        applied = (r.get("applied_at") or "")[:19].replace("T", " ")
        print(
            f"{r['id']:36}  {r['decision']:26}  {float(r['confidence']):5.2f}  "
            f"{applied:20}  {str(cluster_id):7}  {names_str}"
        )


def rollback(client, queue_id: str) -> dict:
    resp = client.rpc("rollback_consolidation", {"queue_id": queue_id}).execute()
    return resp.data or {}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("queue_id", nargs="?", help="memory_review_queue.id to roll back")
    p.add_argument("--list", action="store_true", help="List rollback-eligible entries and exit")
    p.add_argument("--limit", type=int, default=20, help="Rows shown by --list (default 20)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = p.parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key:
        print("SUPABASE_URL / SUPABASE_KEY missing from env", file=sys.stderr)
        return 2

    client = create_client(sb_url, sb_key)

    if args.list:
        rows = list_applied(client, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            print_listing(rows)
        return 0

    if not args.queue_id:
        p.print_usage(sys.stderr)
        print("error: queue_id required (or pass --list)", file=sys.stderr)
        return 2

    try:
        result = rollback(client, args.queue_id)
    except Exception as e:
        print(f"Rollback failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        out = {
            "queue_id": args.queue_id,
            "result": result,
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Rolled back queue entry {args.queue_id}")
        print(f"  decision:             {result.get('decision')}")
        print(f"  canonical_id:         {result.get('canonical_id')}")
        print(f"  canonical_soft_deleted: {result.get('canonical_soft_deleted')}")
        print(f"  restored_count:       {result.get('restored_count')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
