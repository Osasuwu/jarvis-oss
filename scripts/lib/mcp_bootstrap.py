"""Shared stdlib-only helper for the three MCP bootstrap scripts (#1312 AC#4).

Each launcher (run-memory-server.py, run-status-server.py,
run-telegram-mcp.py) finds the venv python then hands off to
run_server_tracked() here: the child's stdout is left connected to the
parent's (it IS the JSON-RPC transport and must never be touched or
captured), the child's stderr is redirected to a per-server log file instead
of inheriting the parent's, and a non-zero exit appends one breadcrumb line
(timestamp, server name, rc, stderr tail) to .claude/mcp-failures.jsonl for
scripts/session-context.py's _check_mcp_failures to surface.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ceiling: hard truncate-on-overflow rather than rotation/compaction — the log
# is a debugging aid for the latest crash, not an audit trail, so losing older
# bytes on overflow is fine; revisit if a server starts needing pre-crash history.
_MAX_LOG_BYTES = 1_000_000
_TAIL_BYTES = 4096


def run_server_tracked(name, python, server, root, _subprocess_run=None, _now=None) -> int:
    root = Path(root)
    log_dir = root / ".claude" / "logs"
    failures_path = root / ".claude" / "mcp-failures.jsonl"
    log_path = log_dir / f"mcp-{name}.stderr.log"
    run = _subprocess_run or subprocess.run

    errf = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _truncate_if_oversized(log_path)
        errf = open(log_path, "ab")
    except OSError:
        errf = None

    try:
        result = run([python, server], stdout=None, stderr=errf, stdin=None)
    finally:
        if errf is not None:
            errf.close()

    rc = result.returncode
    if rc != 0:
        _record_failure(failures_path, name, rc, log_path, _now)
    return rc


def _truncate_if_oversized(log_path: Path, max_bytes: int = None) -> None:
    if max_bytes is None:
        max_bytes = _MAX_LOG_BYTES
    try:
        if log_path.exists() and log_path.stat().st_size > max_bytes:
            log_path.write_bytes(b"")
    except OSError:
        pass


def _read_tail(log_path: Path, max_bytes: int = _TAIL_BYTES) -> str:
    try:
        data = log_path.read_bytes()
    except OSError:
        return ""
    return data[-max_bytes:].decode("utf-8", errors="replace")


def _record_failure(
    failures_path: Path, name: str, exit_code: int, log_path: Path, _now=None
) -> None:
    now = _now if _now is not None else datetime.now(timezone.utc)
    entry = {
        "server": name,
        "timestamp": now.isoformat(),
        "exit_code": exit_code,
        "stderr_tail": _read_tail(log_path),
    }
    try:
        failures_path.parent.mkdir(parents=True, exist_ok=True)
        with open(failures_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
