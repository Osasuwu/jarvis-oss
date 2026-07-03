#!/usr/bin/env python3
"""Generate quarterly markdown dump of record_decision episodes.

Usage:
    python scripts/dump-decisions-quarterly.py --quarter 2026-Q2

Reads decision_made episodes from the Supabase episodes table and renders
a markdown document at docs/decisions/YYYY-QN.md.

Requires SUPABASE_URL and SUPABASE_KEY env vars (same as sandcastle container).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent


def parse_quarter(quarter: str) -> tuple[int, int]:
    """Parse 'YYYY-QN' into (year, quarter_number)."""
    parts = quarter.split("-Q")
    if len(parts) != 2:
        raise ValueError(f"Invalid quarter format: {quarter!r}. Use YYYY-QN (e.g. 2026-Q2)")
    year = int(parts[0])
    q = int(parts[1])
    if q < 1 or q > 4:
        raise ValueError(f"Quarter must be 1-4, got {q}")
    return year, q


def quarter_date_range(year: int, q: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the given quarter, inclusive."""
    start_month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
    end_month = start_month + 2
    # end_date is the last day of the last month in the quarter
    if end_month == 12:
        end_year = year
    else:
        end_year = year
        end_month += 1  # go to first day of NEXT month
        # last day of quarter = first day of next month minus 1 day
    import calendar
    _, last_day = calendar.monthrange(year, start_month + 2)
    start = f"{year}-{start_month:02d}-01T00:00:00Z"
    end = f"{year}-{start_month + 2:02d}-{last_day}T23:59:59Z"
    return start, end


def fetch_decisions(client, start_iso: str, end_iso: str) -> list[dict]:
    """Fetch all decision_made episodes in the date range, ordered by created_at."""
    all_rows = []
    page = 0
    page_size = 1000

    while True:
        result = (
            client.table("episodes")
            .select("*")
            .eq("kind", "decision_made")
            .gte("created_at", start_iso)
            .lte("created_at", end_iso)
            .order("created_at", desc=False)  # chronological
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        if not result.data:
            break
        all_rows.extend(result.data)
        if len(result.data) < page_size:
            break
        page += 1

    return all_rows


def group_by_month(decisions: list[dict]) -> dict[str, list[dict]]:
    """Group decisions by month label like '2026-04'."""
    months: dict[str, list[dict]] = {}
    for d in decisions:
        ts = d.get("created_at", "")
        month_key = ts[:7]  # "2026-04"
        months.setdefault(month_key, []).append(d)
    return dict(sorted(months.items()))


def render_decision(d: dict) -> str:
    """Render a single decision as a markdown block."""
    payload = d.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}

    decision = payload.get("decision") or "(missing decision text)"
    episode_id = d.get("id", "?")
    created_at = d.get("created_at", "")
    reversibility = payload.get("reversibility") or ""
    confidence = payload.get("confidence")
    actor = d.get("actor") or ""
    project = payload.get("project") or ""
    alternatives = payload.get("alternatives_considered") or []
    rationale = payload.get("rationale") or ""
    memories_used = payload.get("memories_used") or []
    outcomes_ref = payload.get("outcomes_referenced") or []
    intentionally_empty = payload.get("intentionally_empty", False)

    lines = [f"### {decision}", ""]
    lines.append(f"- **UUID:** `{episode_id}`")
    lines.append(f"- **When:** `{created_at}`")
    lines.append(f"- **Reversibility:** `{reversibility}`")
    if confidence is not None:
        lines.append(f"- **Confidence:** `{confidence}`")
    lines.append(f"- **Actor:** `{actor}`")
    lines.append(f"- **Project:** `{project}`")

    if alternatives:
        alt_text = "; ".join(str(a) for a in alternatives)
        lines.append(f"- **Alternatives considered:** `{alt_text}`")
    else:
        lines.append("- **Alternatives considered:** `none`")

    if outcomes_ref:
        outcomes_text = ", ".join(str(o) for o in outcomes_ref)
        lines.append(f"- **Outcomes referenced:** `{outcomes_text}`")

    # Rationale — multi-line, so render as blockquote
    if rationale:
        lines.append("")
        lines.append("  > " + rationale.replace("\n", "\n  > "))

    if memories_used:
        mem_text = ", ".join(str(m) for m in memories_used)
        lines.append(f"- **Memories used:** `{mem_text}`")
    elif not intentionally_empty:
        lines.append("- **Memories used:** `none`")
    else:
        lines.append("- **Memories used:** `(intentionally empty)`")

    if payload.get("memories_used_unresolved"):
        unresolved = ", ".join(str(m) for m in payload["memories_used_unresolved"])
        lines.append(f"- **Memories used (unresolved):** `{unresolved}`")

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_document(decisions: list[dict], quarter: str, cutoff: str) -> str:
    """Render the full markdown document."""
    total = len(decisions)
    grouped = group_by_month(decisions)

    # Aggregate stats
    reversibility_counts: dict[str, int] = {}
    actor_prefixes: dict[str, int] = {}
    for d in decisions:
        payload = d.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        rev = payload.get("reversibility", "unknown")
        reversibility_counts[rev] = reversibility_counts.get(rev, 0) + 1

        actor = d.get("actor", "unknown")
        prefix = actor.split(":")[0] if ":" in actor else actor
        actor_prefixes[prefix] = actor_prefixes.get(prefix, 0) + 1

    lines = [
        f"# Decision dump — {quarter} (cutoff: {cutoff})",
        "",
        f"Total decisions: **{total}**",
        "",
        "## Aggregate",
        "",
        "### By reversibility",
    ]
    for rev, count in sorted(reversibility_counts.items()):
        lines.append(f"- `{rev}`: {count}")
    lines.append("")
    lines.append("### By actor prefix")
    for prefix, count in sorted(actor_prefixes.items()):
        lines.append(f"- `{prefix}`: {count}")

    lines.append("")
    lines.append("---")
    lines.append("")

    for month, month_decisions in grouped.items():
        lines.append(f"## {month}")
        lines.append("")
        for d in month_decisions:
            lines.append(render_decision(d))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate quarterly record_decision markdown dump."
    )
    parser.add_argument(
        "--quarter",
        required=True,
        help="Quarter to dump, e.g. 2026-Q2",
    )
    args = parser.parse_args()

    year, q = parse_quarter(args.quarter)
    start_iso, end_iso = quarter_date_range(year, q)

    # Cutoff is today (run date) — cap the range
    now = datetime.now(timezone.utc)
    cutoff = now.strftime("%Y-%m-%d")
    if end_iso > now.isoformat():
        end_iso = now.strftime("%Y-%m-%dT23:59:59Z")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_KEY env vars required.", file=sys.stderr)
        sys.exit(1)

    client = create_client(url, key)

    decisions = fetch_decisions(client, start_iso, end_iso)

    if not decisions:
        print(f"No decisions found for {args.quarter} ({start_iso} to {end_iso})")
        sys.exit(0)

    doc = render_document(decisions, args.quarter, cutoff)

    out_dir = ROOT / "docs" / "decisions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.quarter}.md"
    out_path.write_text(doc)

    # Quick summary stats
    rev_counts: dict[str, int] = {}
    for d in decisions:
        p = d.get("payload", {})
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except (json.JSONDecodeError, TypeError):
                p = {}
        rev = p.get("reversibility", "unknown")
        rev_counts[rev] = rev_counts.get(rev, 0) + 1

    print(f"Wrote {len(decisions)} decisions to {out_path}")
    print(f"  Cutoff: {cutoff}")
    print(f"  Reversibility: {rev_counts}")
    print(f"  Total: {len(decisions)} decisions")


if __name__ == "__main__":
    main()
