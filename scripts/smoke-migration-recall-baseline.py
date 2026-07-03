#!/usr/bin/env python3
"""Post-migration recall-baseline smoke test for memory-deriver Slice 1 (#552).

The recall query references the new deriver columns (`requires_review`,
`merge_targets`) which only exist post-migration — so this script captures a
post-migration baseline for ongoing drift detection, not a pre/post-DDL
comparison.  Use the precheck script (`check-memory-deriver-schema.py`) for
the pre-DDL collision check.

Workflow: run `baseline` once to anchor a snapshot timestamp + ID set; run
`check` later to confirm the same recall window still returns the same rows.
The baseline anchors a `created_at <= baseline_ts` cutoff so concurrent
writes (both jarvis and redrobot share the `memories` table) can't displace
older rows past a LIMIT window and falsely trigger a regression alarm.

Usage:
    # After migration, anchor a baseline snapshot:
    SUPABASE_URL=<url> SUPABASE_KEY=<anon-key> \
        python scripts/smoke-migration-recall-baseline.py baseline

    # Later (e.g. after Deriver writes), verify drift:
    SUPABASE_URL=<url> SUPABASE_KEY=<anon-key> \
        python scripts/smoke-migration-recall-baseline.py check
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASELINE_PATH = ".smoke-recall-baseline.json"

# Recall query that redrobot might run — a simple semantic-style select over
# live (non-superseded, non-expired, non-merge-proposal) memories.  The
# `:baseline_ts` placeholder is filled at call time and anchors the snapshot
# window so concurrent writes do not displace rows past the LIMIT.
RECALL_QUERY = """
SELECT id, type, project, content, created_at, source_provenance,
       confidence, requires_review, derivation_run_id, merge_targets
FROM memories
WHERE expired_at IS NULL
  AND superseded_by IS NULL
  AND deleted_at IS NULL
  AND (valid_to IS NULL OR valid_to > now())
  AND merge_targets IS NULL
  AND requires_review = false
  AND created_at <= '{baseline_ts}'::timestamptz
ORDER BY created_at DESC
LIMIT 50;
"""

QUERY_COUNT = """
SELECT COUNT(*) AS total_live
FROM memories
WHERE expired_at IS NULL
  AND superseded_by IS NULL
  AND deleted_at IS NULL
  AND (valid_to IS NULL OR valid_to > now())
  AND merge_targets IS NULL
  AND requires_review = false
  AND created_at <= '{baseline_ts}'::timestamptz;
"""

QUERY_NOW = "SELECT now() AS ts;"


def _query_supabase(sql: str) -> list[dict]:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/execute_sql"
    key = os.environ["SUPABASE_KEY"]
    headers = {
        "Content-Type": "application/json",
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": sql}).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _check_required_cols(rows: list[dict]) -> None:
    """Ensure the new deriver columns exist and have expected default values.

    Uses explicit raise instead of `assert` so that running under
    PYTHONOPTIMIZE=1 (common in hardened images) still enforces the
    invariants. `assert` is stripped by -O / -OO and would silently
    no-op these guards.
    """
    if not rows:
        return
    sample = rows[0]
    for col in ("requires_review", "derivation_run_id", "merge_targets"):
        if col not in sample:
            raise RuntimeError(
                f"Column `{col}` missing from recall results — "
                f"migration may not have been applied."
            )

    # Check requires_review default — all existing rows should be false
    for row in rows:
        rr = row.get("requires_review")
        if rr is not False and rr is not None:
            raise RuntimeError(
                f"Row {row['id']} has requires_review={rr}, "
                f"expected false after backfill."
            )


def _fetch_baseline_ts() -> str:
    """Anchor a snapshot timestamp via the DB clock (not local)."""
    rows = _query_supabase(QUERY_NOW)
    if not rows or "ts" not in rows[0]:
        raise RuntimeError("Could not anchor baseline timestamp (SELECT now() returned no row).")
    return str(rows[0]["ts"])


def baseline() -> None:
    """Capture recall baseline anchored to a DB-side timestamp."""
    print("Capturing recall baseline...", flush=True)
    baseline_ts = _fetch_baseline_ts()
    print(f"  Anchor timestamp: {baseline_ts}", flush=True)

    rows = _query_supabase(RECALL_QUERY.format(baseline_ts=baseline_ts))
    ids = sorted(r["id"] for r in rows)
    print(f"  {len(ids)} rows returned, saving...", flush=True)

    baseline = {
        "baseline_ts": baseline_ts,
        "count": len(ids),
        "ids": ids,
        "sample": rows[:3] if rows else [],
    }

    with open(BASELINE_PATH, "w") as f:
        json.dump(baseline, f, indent=2, default=str)
    print(f"  Baseline saved to {BASELINE_PATH}", flush=True)


def check() -> None:
    """Compare recall against the anchored baseline snapshot."""
    if not os.path.exists(BASELINE_PATH):
        print(
            f"No baseline at {BASELINE_PATH}. Run `baseline` command first.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)

    baseline_ts = baseline.get("baseline_ts")
    if not baseline_ts:
        print(
            f"Baseline at {BASELINE_PATH} is from an older script version "
            f"(missing baseline_ts anchor). Recapture with `baseline`.",
            file=sys.stderr,
        )
        sys.exit(1)

    rows = _query_supabase(RECALL_QUERY.format(baseline_ts=baseline_ts))
    current_ids = sorted(r["id"] for r in rows)

    # Verify new columns exist and are populated correctly
    _check_required_cols(rows)

    count_rows = _query_supabase(QUERY_COUNT.format(baseline_ts=baseline_ts))
    total_live = count_rows[0]["total_live"] if count_rows else 0
    print(f"  Baseline anchor: {baseline_ts}", flush=True)
    print(f"  Baseline: {baseline['count']} rows", flush=True)
    print(f"  Current:  {len(current_ids)} rows (total live at anchor: {total_live})", flush=True)

    # With the created_at <= baseline_ts anchor, new rows can no longer
    # enter the window — only deletions/supersessions/expiries can remove
    # baseline rows.  Report both directions for visibility but only
    # `removed` is a failure signal.
    removed = set(baseline["ids"]) - set(current_ids)
    added = set(current_ids) - set(baseline["ids"])
    if added:
        print(
            f"  WARN: {len(added)} new rows inside the baseline window — "
            f"unexpected with the timestamp anchor: {list(added)[:3]}",
            file=sys.stderr,
        )

    if removed:
        print(
            f"\nFAIL: {len(removed)} baseline rows missing. "
            f"Recall behavior drifted (supersede/expire/delete or filter regression).",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nOK: recall baseline preserved.", flush=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "baseline":
        baseline()
    elif cmd == "check":
        check()
    else:
        print(
            "Usage:\n"
            "  smoke-migration-recall-baseline.py baseline   # capture pre-migration\n"
            "  smoke-migration-recall-baseline.py check      # verify post-migration\n"
            "\n"
            "Both commands need SUPABASE_URL and SUPABASE_KEY set.\n"
        )
        sys.exit(1 if cmd else 0)


if __name__ == "__main__":
    main()
