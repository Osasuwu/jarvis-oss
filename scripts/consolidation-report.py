"""Memory consolidation — cluster detection report (deterministic).

Calls the `find_consolidation_clusters` RPC and prints a human-readable
report of similarity clusters that are genuine candidates for merge/supersede.

As of Phase 5.1c the RPC filters dead memories (expired_at, valid_to,
superseded_by, deleted_at) at the SQL source, so `dead_filtered` in the
report should be 0 against any current DB. The Python-side live check is
kept as a cheap defensive guard for older DB revisions and is a no-op in
normal operation.

No LLM. Default mode is a pure read — safe against prod. The only write
path is the opt-in `--save-memory` flag, which upserts the markdown report
into the `memories` table as a dated `consolidation_report_YYYY-MM-DD` row.

Follow-ups (separate PRs):
    5.1b — Haiku merger emits merge/supersede plan per cluster (dry-run + --apply)
    5.1c — RPC migration: filter dead memories at source; replace archive_memories
           type-suffix hack with expired_at + superseded_by writes
    5.1d — Weekly scheduled task + owner-review queue

Usage:
    python scripts/consolidation-report.py                 # markdown report to stdout
    python scripts/consolidation-report.py --json          # machine-readable
    python scripts/consolidation-report.py --min-size 4    # require 4+ memories per cluster
    python scripts/consolidation-report.py --threshold 0.85
    python scripts/consolidation-report.py --save-memory   # also upsert report as a memory

Requires SUPABASE_URL and SUPABASE_KEY. .env in repo root auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Reports contain non-ASCII characters (em-dashes, arrows, Cyrillic in
# descriptions). On Windows the default console codec is cp1251 which
# can't encode them; force UTF-8 so the script works on all 3 devices.
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
            load_dotenv(c)
            break
except ImportError:
    pass

from supabase import create_client


DEFAULT_MIN_SIZE = 3
DEFAULT_THRESHOLD = 0.80


def fetch_clusters(client, min_size: int, threshold: float) -> list[dict]:
    """Call find_consolidation_clusters RPC. Returns rows as-is from Postgres."""
    resp = client.rpc(
        "find_consolidation_clusters",
        {"min_cluster_size": min_size, "sim_threshold": threshold},
    ).execute()
    return resp.data or []


def fetch_lifecycle_columns(client, memory_ids: list[str]) -> dict[str, dict]:
    """Fetch lifecycle columns + description/tags for the given memory ids."""
    if not memory_ids:
        return {}
    rows = (
        client.table("memories")
        .select(
            "id, name, type, description, tags, "
            "expired_at, valid_to, superseded_by, deleted_at, "
            "updated_at, content_updated_at"
        )
        .in_("id", memory_ids)
        .execute()
        .data
    )
    return {r["id"]: r for r in rows}


def _parse_ts(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        # Postgres returns ISO-8601 with offset; handle the `Z` suffix too.
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def is_live(row: dict) -> bool:
    """Phase 1 default-recall semantics: memory is live iff not expired, not
    past its valid_to, not superseded, not soft-deleted.
    Kept in sync with server.py `_hybrid_recall` filter:
        expired_at IS NULL
        AND (valid_to IS NULL OR valid_to > now())
        AND superseded_by IS NULL
    Plus deleted_at (soft-delete).
    """
    if row.get("expired_at") is not None:
        return False
    if row.get("superseded_by") is not None:
        return False
    if row.get("deleted_at") is not None:
        return False
    valid_to = _parse_ts(row.get("valid_to"))
    if valid_to is not None and valid_to <= datetime.now(timezone.utc):
        return False
    return True


def group_clusters(rpc_rows: list[dict], live_by_id: dict[str, dict]) -> list[dict]:
    """Re-group RPC rows by cluster_id, enrich with lifecycle+description,
    and drop clusters where <min stayed live.

    A memory appears in the RPC output once per pair-membership (anchor +
    each neighbor) so the raw rows contain duplicates. Dedup by memory_id
    within a cluster, keeping the highest similarity seen for that row.
    """
    by_cluster: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in rpc_rows:
        mid = r["memory_id"]
        live = live_by_id.get(mid)
        if live is None:
            continue  # memory vanished between RPC call and lookup
        if not is_live(live):
            continue
        sim = float(r["similarity"])
        existing = by_cluster[r["cluster_id"]].get(mid)
        if existing and existing["similarity"] >= sim:
            continue
        by_cluster[r["cluster_id"]][mid] = {
            "id": mid,
            "name": r["memory_name"],
            "type": r["memory_type"],
            "similarity": sim,
            "updated_at": r["updated_at"],
            "description": live.get("description") or "",
            "tags": live.get("tags") or [],
            "content_updated_at": live.get("content_updated_at"),
        }

    clusters: list[dict] = []
    for cid, by_id in by_cluster.items():
        members = sorted(by_id.values(), key=lambda m: m["updated_at"], reverse=True)
        max_sim = max(m["similarity"] for m in members)
        avg_sim = sum(m["similarity"] for m in members) / len(members)
        clusters.append({
            "cluster_id": cid,
            "size": len(members),
            "max_similarity": round(max_sim, 4),
            "avg_similarity": round(avg_sim, 4),
            "types": sorted({m["type"] for m in members}),
            "members": members,
        })
    clusters.sort(key=lambda c: (c["size"], c["max_similarity"]), reverse=True)
    return clusters


def render_markdown(
    clusters: list[dict],
    min_size: int,
    threshold: float,
    raw_rows: int,
    dead_filtered: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_members = sum(c["size"] for c in clusters)
    lines = [
        f"# Memory consolidation report — {now}",
        "",
        f"- RPC: `find_consolidation_clusters(min_cluster_size={min_size}, sim_threshold={threshold})`",
        f"- Raw RPC rows: {raw_rows}",
        f"- Dead memories filtered post-hoc (defensive, expected 0): {dead_filtered}",
        f"- Live clusters (size >= {min_size}): **{len(clusters)}**",
        f"- Total live memories in clusters: {total_members}",
        "",
    ]
    if not clusters:
        lines.append("_No live clusters found at current thresholds. Nothing to consolidate._")
        return "\n".join(lines)

    for c in clusters:
        lines.append(
            f"## Cluster {c['cluster_id']} — {c['size']} members, "
            f"max sim {c['max_similarity']:.3f}, avg {c['avg_similarity']:.3f}, "
            f"types: {', '.join(c['types'])}"
        )
        lines.append("")
        lines.append("| name | type | updated_at | sim | description |")
        lines.append("|------|------|------------|-----|-------------|")
        for m in c["members"]:
            desc = (m["description"] or "").replace("\n", " ").replace("|", "\\|")
            if len(desc) > 120:
                desc = desc[:117] + "..."
            # RPC sim for each row is the sim to *some* pair; keep as signal.
            lines.append(
                f"| `{m['name']}` | {m['type']} | {m['updated_at'][:10]} | "
                f"{m['similarity']:.3f} | {desc} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**Next**: Phase 5.1b will feed these clusters to Haiku for a merge/supersede plan.")
    return "\n".join(lines)


def save_report_memory(client, report_md: str, cluster_count: int, member_count: int) -> None:
    """Upsert as a project memory so owner can recall recent consolidation state.

    Name is date-stamped so successive daily/weekly runs don't clobber; kept
    type=project because this is operational state, not a decision.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = f"consolidation_report_{today}"
    description = (
        f"Consolidation clusters {today}: {cluster_count} live clusters, "
        f"{member_count} memories. Deterministic RPC snapshot (Phase 5.1a)."
    )
    # Only match a *live* prior row for update; if owner soft-deleted the old
    # report, treat that as a deliberate tombstone and insert a fresh row
    # instead of silently reviving it.
    existing = (
        client.table("memories")
        .select("id")
        .eq("project", "jarvis")
        .eq("name", name)
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    payload = {
        "project": "jarvis",
        "name": name,
        "type": "project",
        "description": description,
        "content": report_md,
        "tags": ["memory", "consolidation", "phase-5"],
        "source_provenance": "skill:consolidation",
    }
    if existing:
        client.table("memories").update(payload).eq("id", existing[0]["id"]).execute()
        print(f"Updated memory `{name}` (id={existing[0]['id']})", file=sys.stderr)
    else:
        client.table("memories").insert(payload).execute()
        print(f"Inserted memory `{name}`", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE,
                   help=f"Minimum cluster size (default {DEFAULT_MIN_SIZE})")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"Cosine similarity threshold (default {DEFAULT_THRESHOLD})")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of markdown")
    p.add_argument("--save-memory", action="store_true",
                   help="Upsert the markdown report as a Jarvis memory "
                        "(`consolidation_report_YYYY-MM-DD`, type=project)")
    args = p.parse_args()

    url = os.environ.get("SUPABASE_URL")
    # Prefer service-role: writes memories with skill:consolidation provenance,
    # which the #542 RLS gate rejects for anon. See mcp-memory/client.py.
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_KEY (or SUPABASE_SERVICE_KEY) missing from env", file=sys.stderr)
        return 2

    client = create_client(url, key)

    rpc_rows = fetch_clusters(client, args.min_size, args.threshold)
    raw_count = len(rpc_rows)

    ids = sorted({r["memory_id"] for r in rpc_rows})
    live_by_id = fetch_lifecycle_columns(client, ids)
    dead_filtered = sum(1 for r in live_by_id.values() if not is_live(r))

    clusters = group_clusters(rpc_rows, live_by_id)
    # Dead filtering + dedup can shrink clusters below the requested size.
    clusters = [c for c in clusters if c["size"] >= args.min_size]

    if args.json:
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": {"min_size": args.min_size, "threshold": args.threshold},
            "raw_rpc_rows": raw_count,
            "dead_filtered": dead_filtered,
            "cluster_count": len(clusters),
            "clusters": clusters,
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        md = render_markdown(clusters, args.min_size, args.threshold, raw_count, dead_filtered)
        print(md)
        if args.save_memory:
            total_members = sum(c["size"] for c in clusters)
            save_report_memory(client, md, len(clusters), total_members)

    return 0


if __name__ == "__main__":
    sys.exit(main())
