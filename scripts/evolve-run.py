"""Weekly A-MEM neighbor-evolve wrapper — Phase 5.2-γ (#234).

Invokes `evolve-neighbors.py --apply --json --save-memory` as a subprocess,
parses the JSON summary (specifically `apply_outcomes[]`), emits one
`evolve_run` event to Supabase, and prints a human-readable recap to
stdout. Designed for the scheduled-tasks MCP cron registered alongside
this PR (`memory-evolve-weekly`), but safe to run manually (and that's
how it's smoke-tested).

Exit code: 0 on success, 1 when the inner script fails or stdout can't
be parsed. The scheduled-task prompt inspects the exit code + the
`pending_new` count in the recap to decide whether to spawn a review
chip.

Differs from `consolidation-run.py` (Phase 5.1d-α, #228) on two axes:
  - The inner script's --json shape is different (apply_outcomes array,
    not plans + apply_results), so summarize() matches evolve-neighbors's
    fields (applied / queued / skipped_all_keep / error).
  - The review-trigger threshold is lower: severity=medium at
    queued_pending >= 1, because evolution plans are smaller-grain than
    consolidation clusters. A single queued EVOLVE plan is worth a look.

Usage:
    python scripts/evolve-run.py                       # defaults
    python scripts/evolve-run.py --limit 30            # larger batch
    python scripts/evolve-run.py --dry-run             # skip --apply

Env: SUPABASE_URL, SUPABASE_KEY (event write), plus the inner script's
ANTHROPIC_API_KEY. .env auto-loaded.
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
INNER_SCRIPT = REPO_ROOT / "scripts" / "evolve-neighbors.py"

DEFAULT_LIMIT = 20
DEFAULT_CONFIDENCE_GATE = 0.85
# Evolution plans are smaller-grain than consolidation clusters, so one
# queued plan is already worth surfacing. Consolidation uses >=3.
REVIEW_THRESHOLD = 1


def run_inner(args) -> tuple[int, str, str, float]:
    """Run evolve-neighbors.py, return (rc, stdout, stderr, duration_s)."""
    cmd = [
        sys.executable,
        str(INNER_SCRIPT),
        "--json",
        "--limit",
        str(args.limit),
        "--confidence-gate",
        str(args.confidence_gate),
    ]
    if args.since:
        cmd.extend(["--since", args.since])
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


def summarize(result: dict, *, wrapper_applied: bool) -> dict:
    """Derive event-payload counts from the inner script's --json output.

    `wrapper_applied` is the wrapper's own view of whether it passed --apply
    (i.e. `not args.dry_run`). Used as a fallback when the inner JSON omits
    the `apply` field.
    """
    outcomes = result.get("apply_outcomes") or []
    by_status: dict[str, int] = defaultdict(int)
    for o in outcomes:
        by_status[o.get("status", "unknown")] += 1

    # Proposal-level rollup (actions distribution across all plans).
    results_list = result.get("results") or []
    actions: dict[str, int] = defaultdict(int)
    neighbor_count = 0
    for r in results_list:
        for p in r.get("proposals") or []:
            actions[p.get("action", "unknown")] += 1
        neighbor_count += len(r.get("neighbors") or [])

    inner_apply = result.get("apply")
    applied_mode = bool(inner_apply) if inner_apply is not None else wrapper_applied

    return {
        "updates_planned": len(results_list),
        "neighbors_evaluated": neighbor_count,
        "applied": by_status.get("applied", 0),
        "queued_pending": by_status.get("queued", 0),
        "skipped_all_keep": by_status.get("skipped_all_keep", 0),
        "errors": by_status.get("error", 0),
        "actions": dict(actions),
        "confidence_gate": result.get("confidence_gate"),
        "model": result.get("model"),
        "applied_mode": applied_mode,
    }


def write_event(
    *,
    severity: str,
    title: str,
    payload: dict,
) -> str | None:
    """Insert one evolve_run row into events. Best-effort."""
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
                    "event_type": "evolve_run",
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
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
    )
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO date floor on UPDATE applied_at (passthrough)",
    )
    p.add_argument(
        "--confidence-gate",
        type=float,
        default=DEFAULT_CONFIDENCE_GATE,
    )
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

    if stderr:
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")

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
            title="Evolve run — stdout parse failed",
            payload=payload,
        )
        print(
            json.dumps(
                {"status": "parse_failed", "event_id": event_id, "payload": payload},
                indent=2,
            )
        )
        return 1

    wrapper_applied = not args.dry_run
    if rc != 0:
        payload = {
            "started_at": started_at,
            "duration_s": round(duration, 2),
            "exit_code": rc,
            "summary": (
                summarize(inner, wrapper_applied=wrapper_applied) if inner else None
            ),
            "stderr_tail": (stderr or "")[-500:],
        }
        event_id = write_event(
            severity="high",
            title=f"Evolve run — inner script exited {rc}",
            payload=payload,
        )
        print(
            json.dumps(
                {"status": "subprocess_error", "event_id": event_id, "payload": payload},
                indent=2,
            )
        )
        return 1

    summary = summarize(inner, wrapper_applied=wrapper_applied)
    payload = {
        "started_at": started_at,
        "duration_s": round(duration, 2),
        "exit_code": 0,
        **summary,
    }

    pending_new = summary["queued_pending"]
    applied = summary["applied"]
    severity = "medium" if pending_new >= REVIEW_THRESHOLD else "info"
    title_mode = "apply" if summary["applied_mode"] else "dry-run"
    title = (
        f"Evolve run ({title_mode}) — {applied} applied, "
        f"{pending_new} pending, {summary['skipped_all_keep']} keep-only "
        f"(updates {summary['updates_planned']}, neighbors {summary['neighbors_evaluated']})"
    )
    event_id = write_event(severity=severity, title=title, payload=payload)

    recap = {
        "status": "ok",
        "event_id": event_id,
        "severity": severity,
        "title": title,
        "payload": payload,
        "pending_new": pending_new,
        "needs_review": pending_new >= REVIEW_THRESHOLD,
    }
    print(json.dumps(recap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
