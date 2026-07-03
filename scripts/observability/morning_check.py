"""Morning check — read audit_log, surface what happened in the last 24h.

Run on any host with Supabase credentials (SUPABASE_URL / SUPABASE_KEY in env
or via agents.config.AgentConfig). Prints a text report; exits 0 on healthy,
1 on alarms, 2 on connection failure.

Usage:
    python -m scripts.observability.morning_check
    python -m scripts.observability.morning_check --hours 6
    python -m scripts.observability.morning_check --agent task-dispatcher
    python -m scripts.observability.morning_check --enqueue-on-alarm
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.executor import _now_iso
from agents.scope_hash import _hash_scope_files
from agents.supabase_client import get_client

# Alarm categories for stable idempotency key generation
_ALARM_CATEGORIES = frozenset(
    {
        "high_failure_rate",
        "dispatcher_gap",
        "no_audit_rows",
    }
)


def _fmt_ts(ts: str) -> str:
    return ts.replace("T", " ").split("+")[0].split(".")[0]


def _idempotency_key(category: str, details_summary: str) -> str:
    """Build idempotency key: sha256(YYYY-MM-DD | category | sha256(details_summary)).

    Date in UTC ensures the same alarm on the same day produces the same key,
    but a new day produces a fresh key even if the alarm repeats.
    """
    utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    details_hash = hashlib.sha256(details_summary.encode("utf-8")).hexdigest()
    formula = f"{utc_date}|{category}|{details_hash}"
    return hashlib.sha256(formula.encode("utf-8")).hexdigest()


def _enqueue_alarm(
    client: Any,
    category: str,
    details_summary: str,
    goal: str,
) -> None:
    """Enqueue a self-perception alarm as a tier:3-human task_queue row.

    Uses upsert with ignore_duplicates to ensure idempotency: the same alarm
    category + details on the same day produces the same key and never duplicates.
    If the upsert fails (network, auth), log and continue — enqueue is best-effort.
    """
    key = _idempotency_key(category, details_summary)
    now = _now_iso()

    row = {
        "goal": goal,
        "scope_files": [],
        "approved_by": "cron:morning_check",
        "approved_at": now,
        "approved_scope_hash": _hash_scope_files([]),  # sha256 of empty list
        "auto_dispatch": False,
        "idempotency_key": key,
        "status": "pending",
    }

    try:
        client.table("task_queue").upsert(
            row,
            on_conflict="idempotency_key",
            ignore_duplicates=True,
        ).execute()
        # Log first 12 chars of key for re-enqueue pattern detection
        print(f"  [enqueued alarm {key[:12]}... ({category})]", file=sys.stderr)
    except Exception as e:
        # Failure to enqueue doesn't mask the alarm — we already printed it.
        print(f"  [enqueue failed: {e}]", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hours", type=int, default=24, help="Lookback window (default 24)")
    parser.add_argument("--agent", default=None, help="Filter to specific agent_id")
    parser.add_argument(
        "--gap-minutes",
        type=int,
        default=10,
        help="Flag gaps in dispatcher heartbeat exceeding N minutes (default 10)",
    )
    parser.add_argument(
        "--enqueue-on-alarm",
        action="store_true",
        default=False,
        help="Enqueue alarms as task_queue rows (default: off)",
    )
    args = parser.parse_args(argv)

    try:
        client = get_client()
    except Exception as e:
        print(f"FAILED to connect to Supabase: {e}", file=sys.stderr)
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    cutoff_iso = cutoff.isoformat()

    q = (
        client.table("audit_log")
        .select("agent_id, tool_name, action, target, outcome, timestamp, details")
        .gte("timestamp", cutoff_iso)
        .order("timestamp", desc=False)
    )
    if args.agent:
        q = q.eq("agent_id", args.agent)

    rows = q.execute().data or []

    if not rows:
        msg = (
            f"No audit_log rows in the last {args.hours}h"
            + (f" for agent {args.agent}" if args.agent else "")
            + "."
        )
        print(msg)
        print("If the dispatcher should be running, this is a RED FLAG — service may be down.")
        if args.enqueue_on_alarm:
            details = "no_audit_rows"
            _enqueue_alarm(client, "no_audit_rows", details, msg)
        return 1

    # Per-agent rollup
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_agent[r["agent_id"] or "<null>"].append(r)

    print(f"=== Morning check - last {args.hours}h ===")
    print(f"Total rows: {len(rows)}")
    print(f"Window: {_fmt_ts(cutoff_iso)} -> now")
    print()
    print(f"{'agent_id':<30} {'rows':>6} {'failures':>10} {'first':<20} {'last':<20}")
    print("-" * 96)

    alarms: list[str] = []

    def _is_failure(outcome: str | None) -> bool:
        # 'failure:<ExceptionType>' is the canonical failure marker (see
        # agents/dispatcher.py docstring). 'success', 'dry_run', and any
        # other domain-specific outcome are NOT failures.
        return (outcome or "").startswith("failure")

    for agent, agent_rows in sorted(by_agent.items()):
        failures = [r for r in agent_rows if _is_failure(r["outcome"])]
        first = _fmt_ts(agent_rows[0]["timestamp"])
        last = _fmt_ts(agent_rows[-1]["timestamp"])
        print(f"{agent:<30} {len(agent_rows):>6} {len(failures):>10} {first:<20} {last:<20}")
        if len(agent_rows) >= 5:
            failure_pct = 100.0 * len(failures) / len(agent_rows)
            if failure_pct > 25:
                alarm_msg = (
                    f"{agent}: {failure_pct:.0f}% failure rate ({len(failures)}/{len(agent_rows)})"
                )
                alarms.append(alarm_msg)
                if args.enqueue_on_alarm:
                    details = f"{agent}:{failure_pct:.0f}%"
                    _enqueue_alarm(client, "high_failure_rate", details, alarm_msg)

    print()

    # Gap detection on dispatcher heartbeat
    dispatcher_rows = by_agent.get("task-dispatcher", [])
    if dispatcher_rows:
        prev = None
        for r in dispatcher_rows:
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            if prev is not None:
                gap_min = (ts - prev).total_seconds() / 60
                if gap_min > args.gap_minutes:
                    alarm_msg = (
                        f"task-dispatcher: {gap_min:.0f}min gap ending at {_fmt_ts(r['timestamp'])}"
                    )
                    alarms.append(alarm_msg)
                    if args.enqueue_on_alarm:
                        details = "dispatcher:gap"
                        _enqueue_alarm(client, "dispatcher_gap", details, alarm_msg)
            prev = ts

    # Recent failures (full detail)
    recent_failures = [r for r in rows if _is_failure(r["outcome"])][-10:]
    if recent_failures:
        print("Recent failures (last 10):")
        for r in recent_failures:
            print(
                f"  {_fmt_ts(r['timestamp'])} {r['agent_id']:<25} {r['action']:<15} {r['outcome']}"
            )
        print()

    if alarms:
        print("ALARMS:")
        for a in alarms:
            print(f"  - {a}")
        return 1

    print("All clear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
