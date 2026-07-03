"""Weekly consolidation wrapper — Phase 5.1d-α (#225).

Invokes `consolidation-merge-plan.py --apply --json --save-memory` as a
subprocess, parses the JSON summary, emits one `consolidation_run` event
to Supabase, and prints a human-readable recap to stdout. Designed for
the scheduled-tasks MCP cron registered in this PR, but safe to run
manually (and that's how it's smoke-tested).

Exit code: 0 on success, 1 when the inner script fails or stdout can't
be parsed. The scheduled-task prompt inspects the exit code + the
"pending_new" count in the recap to decide whether to fire a review
notification.

Usage:
    python scripts/consolidation-run.py                       # defaults
    python scripts/consolidation-run.py --threshold 0.78      # passthrough
    python scripts/consolidation-run.py --dry-run             # skip --apply

Env: SUPABASE_URL, SUPABASE_KEY (event write), plus the inner script's
ANTHROPIC_API_KEY + VOYAGE_API_KEY. .env auto-loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
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


REPO_ROOT = Path(__file__).resolve().parent.parent
INNER_SCRIPT = REPO_ROOT / "scripts" / "consolidation-merge-plan.py"

DEFAULT_THRESHOLD = 0.80
DEFAULT_MIN_SIZE = 3
DEFAULT_CONFIDENCE_GATE = 0.85
DEFAULT_LIMIT = 20


def run_inner(args) -> tuple[int, str, str, float]:
    """Run consolidation-merge-plan.py, return (rc, stdout, stderr, duration_s)."""
    cmd = [
        sys.executable,
        str(INNER_SCRIPT),
        "--json",
        "--min-size",
        str(args.min_size),
        "--threshold",
        str(args.threshold),
        "--limit",
        str(args.limit),
        "--confidence-gate",
        str(args.confidence_gate),
    ]
    if not args.dry_run:
        cmd.append("--apply")
    if args.save_memory:
        cmd.append("--save-memory")

    t0 = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = time.monotonic() - t0
    return proc.returncode, proc.stdout, proc.stderr, duration


def summarize(result: dict) -> dict:
    """Derive event-payload counts from the inner script's --json output."""
    apply_results = result.get("apply_results") or []
    by_status: dict[str, int] = defaultdict(int)
    for r in apply_results:
        by_status[r.get("status", "unknown")] += 1

    clusters = result.get("clusters") or []
    plans = result.get("plans") or []
    by_decision: dict[str, int] = defaultdict(int)
    for p in plans:
        by_decision[p.get("decision", "unknown")] += 1

    return {
        "clusters_planned": len(clusters),
        "skipped_seen": int(result.get("skipped_seen") or 0),
        "applied": by_status.get("applied", 0),
        "queued_pending": by_status.get("queued", 0),
        "noted_keep_distinct": by_status.get("noted", 0),
        "errors": by_status.get("error", 0),
        "decisions": dict(by_decision),
        "threshold": result.get("params", {}).get("threshold"),
        "confidence_gate": result.get("confidence_gate"),
        "model": result.get("model"),
        "applied_mode": bool(result.get("apply")),
    }


def write_event(
    *,
    severity: str,
    title: str,
    payload: dict,
) -> str | None:
    """Insert one consolidation_run row into events. Best-effort."""
    try:
        from supabase import create_client
    except ImportError:
        print("! supabase-py not installed; skipping event emit", file=sys.stderr)
        return None

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key:
        print("! SUPABASE_URL/KEY missing; skipping event emit", file=sys.stderr)
        return None

    client = create_client(sb_url, sb_key)
    try:
        resp = (
            client.table("events")
            .insert(
                {
                    "event_type": "consolidation_run",
                    "severity": severity,
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--confidence-gate", type=float, default=DEFAULT_CONFIDENCE_GATE)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Omit --apply (for smoke-test only; events will still emit)",
    )
    p.add_argument(
        "--no-save-memory",
        dest="save_memory",
        action="store_false",
        help="Skip saving the markdown plan as a Jarvis memory",
    )
    p.set_defaults(save_memory=True)
    args = p.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    rc, stdout, stderr, duration = run_inner(args)

    # stderr is informational progress (cluster N/M, skipped count, etc).
    # Tee it up so the scheduled-task session sees what the inner script
    # reported even when everything succeeds.
    if stderr:
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")

    # Parse stdout. Inner script always emits a JSON object when --json
    # is set, even for the "no clusters" path.
    try:
        inner = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as e:
        payload = {
            "started_at": started_at,
            "duration_s": round(duration, 2),
            "exit_code": rc,
            "error": f"stdout parse failed: {e}",
            "stderr_tail": (stderr or "")[-500:],
            "stdout_head": (stdout or "")[:500],
        }
        event_id = write_event(
            severity="high",
            title="Consolidation run — stdout parse failed",
            payload=payload,
        )
        print(
            json.dumps(
                {"status": "parse_failed", "event_id": event_id, "payload": payload},
                indent=2,
            )
        )
        return 1

    if rc != 0:
        payload = {
            "started_at": started_at,
            "duration_s": round(duration, 2),
            "exit_code": rc,
            "summary": summarize(inner) if inner else None,
            "stderr_tail": (stderr or "")[-500:],
        }
        event_id = write_event(
            severity="high",
            title=f"Consolidation run — inner script exited {rc}",
            payload=payload,
        )
        print(
            json.dumps(
                {"status": "subprocess_error", "event_id": event_id, "payload": payload},
                indent=2,
            )
        )
        return 1

    summary = summarize(inner)
    payload = {
        "started_at": started_at,
        "duration_s": round(duration, 2),
        "exit_code": 0,
        **summary,
    }

    pending_new = summary["queued_pending"]
    applied = summary["applied"]
    severity = "medium" if pending_new >= 3 else "info"
    title_mode = "apply" if summary["applied_mode"] else "dry-run"
    title = (
        f"Consolidation run ({title_mode}) — {applied} applied, "
        f"{pending_new} pending, {summary['noted_keep_distinct']} noted "
        f"(clusters {summary['clusters_planned']}, skipped {summary['skipped_seen']})"
    )
    event_id = write_event(severity=severity, title=title, payload=payload)

    recap = {
        "status": "ok",
        "event_id": event_id,
        "severity": severity,
        "title": title,
        "payload": payload,
        # Surfaced for the scheduled-task prompt — so it can decide
        # whether to spawn a review chip without re-reading the payload.
        "pending_new": pending_new,
        "needs_review": pending_new >= 3,
    }
    print(json.dumps(recap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
