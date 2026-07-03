"""One-shot live smoke test for the dispatcher — seed one row, spawn real Claude, verify.

Usage:
    python scripts/dispatcher_smoke_live.py seed      # insert a row, print marker
    python scripts/dispatcher_smoke_live.py check     # inspect row/audit/marker file
    python scripts/dispatcher_smoke_live.py cleanup   # delete row + audit + marker file

The workflow is deliberately split so the operator runs `python -m agents.dispatcher`
in between `seed` and `check`, keeping each subprocess boundary observable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from agents.scope_hash import _hash_scope_files

STATE_FILE = Path(__file__).parent / ".dispatcher_smoke_state.json"
MARKER_DIR = Path(__file__).parent / ".smoke-markers"


def seed() -> int:
    load_dotenv()
    from agents.supabase_client import get_client

    MARKER_DIR.mkdir(exist_ok=True)
    marker = uuid.uuid4().hex[:12]
    # Marker lives inside the project dir so the spawned Claude's acceptEdits
    # permission mode approves the Write. Writes outside the project root
    # still require explicit --add-dir flags, which the dispatcher does not
    # grant (and shouldn't for a test utility).
    smoke_file = MARKER_DIR / f"{marker}.ok"

    goal = (
        f"DISPATCHER SMOKE TEST. Create a file at the absolute path "
        f"{smoke_file.as_posix()!r} with the single line content 'ok'. "
        f"Then stop. Do not edit any source code. Do not run git. "
        f"Do not create issues, PRs, or commits. Do not make network calls. "
        f"Only use the Write tool once. This is a smoke test, not a real task."
    )

    scope_files: list[str] = []
    scope_hash = _hash_scope_files(scope_files)
    idem_key = hashlib.sha256(f"dispatcher-smoke-live-{marker}".encode("utf-8")).hexdigest()

    cli = get_client()
    inserted = (
        cli.table("task_queue")
        .insert(
            {
                "goal": goal,
                "scope_files": scope_files,
                "approved_at": datetime.now(UTC).isoformat(),
                "approved_by": "dispatcher-smoke-operator",
                "approved_scope_hash": scope_hash,
                "auto_dispatch": True,
                "idempotency_key": idem_key,
                "status": "pending",
            }
        )
        .execute()
        .data
    )
    if not inserted:
        print("INSERT returned no data", file=sys.stderr)
        return 1

    row = inserted[0]
    state = {
        "row_id": row["id"],
        "marker": marker,
        "smoke_file": str(smoke_file),
        "idempotency_key": idem_key,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"[seed] row_id={row['id']}")
    print(f"[seed] marker={marker}")
    print(f"[seed] expected smoke file: {smoke_file}")
    print(f"[seed] state saved to: {STATE_FILE}")
    print()
    print("Next: python -m agents.dispatcher")
    return 0


def check() -> int:
    load_dotenv()
    if not STATE_FILE.exists():
        print(f"No state file at {STATE_FILE} — run `seed` first", file=sys.stderr)
        return 1

    state = json.loads(STATE_FILE.read_text())
    row_id = state["row_id"]
    marker = state["marker"]
    smoke_file = Path(state["smoke_file"])

    from agents.supabase_client import get_client

    cli = get_client()

    row = (
        cli.table("task_queue")
        .select("id, status, goal, dispatched_at, escalated_reason")
        .eq("id", row_id)
        .limit(1)
        .execute()
        .data
    )
    audit = (
        cli.table("audit_log")
        .select("agent_id, tool_name, action, outcome, details, timestamp")
        .eq("target", f"task_queue:{row_id}")
        .order("timestamp", desc=True)
        .limit(5)
        .execute()
        .data
        or []
    )

    print(f"[check] marker={marker}")
    print(f"[check] row: {json.dumps(row[0] if row else None, indent=2, default=str)}")
    print(f"[check] audit_log rows ({len(audit)}):")
    for entry in audit:
        print(f"  - {entry['timestamp']} {entry['agent_id']}/{entry['action']}={entry['outcome']}")
    print(f"[check] smoke file exists? {smoke_file.exists()} ({smoke_file})")
    if smoke_file.exists():
        print(f"[check] smoke file content: {smoke_file.read_text()!r}")
    return 0


def cleanup() -> int:
    load_dotenv()
    if not STATE_FILE.exists():
        print(f"No state file at {STATE_FILE} — nothing to clean", file=sys.stderr)
        return 0

    state = json.loads(STATE_FILE.read_text())
    row_id = state["row_id"]
    smoke_file = Path(state["smoke_file"])

    from agents.supabase_client import get_client

    cli = get_client()
    cli.table("audit_log").delete().eq("target", f"task_queue:{row_id}").execute()
    cli.table("task_queue").delete().eq("id", row_id).execute()

    if smoke_file.exists():
        smoke_file.unlink()

    STATE_FILE.unlink()
    print(f"[cleanup] deleted row {row_id}, audit rows, marker file, state file")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["seed", "check", "cleanup"])
    args = parser.parse_args()
    return {"seed": seed, "check": check, "cleanup": cleanup}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
