"""Stop-hook entry: extract comm_patterns from one CCD session.

Hook input on stdin (Claude Code Stop event):
  {
    "session_id": "...",
    "transcript_path": "/path/to/file.jsonl",
    "cwd": "...",
    "hook_event_name": "Stop",
    ...
  }

Invariants (mirrors scripts/pre-compact-backup.py shape):
  * **Never** blocks the session end. Exits 0 on every path.
  * Sandcastle / worktree / headless sessions skip silently.
  * Watermark-driven idempotency: re-running on the same transcript
    produces zero new rows.

Registered in ``.claude-userlevel/settings.json`` under the ``Stop`` event.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python.
# Same shape as scripts/pre-compact-backup.py.
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if (
    __name__ == "__main__"
    and _venv_py.exists()
    and Path(sys.executable).resolve() != _venv_py.resolve()
):
    # Coerce any non-zero child exit to 0 — Stop hook must never block session
    # end. Child already prints diagnostics to stderr.
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]) and 0)

# ---------------------------------------------------------------------------
# Under venv — safe to import deps and our package.
# Module-level imports are wrapped: an ImportError here (missing supabase /
# dotenv on this venv) would propagate and exit non-zero, which Claude Code
# would surface as a blocked session end.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_root / "scripts"))

try:
    from dotenv import load_dotenv

    for _env in [_root / ".env", _root.parent / ".env"]:
        if _env.exists():
            load_dotenv(_env, override=True)
            break
except Exception:
    pass

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

try:
    from comm_patterns.classifier import call_ollama
    from comm_patterns.extractor import extract_session
    from comm_patterns.store import SupabaseStore
except Exception as _import_err:  # pragma: no cover — env-specific
    print(f"[comm-patterns-extract] import skipped: {_import_err}", file=sys.stderr)
    sys.exit(0)


def _read_hook_input() -> dict:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[comm-patterns-extract] bad hook input: {type(e).__name__}", file=sys.stderr)
        return {}


def main() -> int:
    payload = _read_hook_input()
    session_id = payload.get("session_id") or ""
    transcript_path_s = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()

    if not session_id or not transcript_path_s:
        print("[comm-patterns-extract] missing session_id / transcript_path; skip", file=sys.stderr)
        return 0

    try:
        transcript_path = Path(transcript_path_s)
        device = socket.gethostname()
        store = SupabaseStore()
        stats = extract_session(
            device=device,
            session_id=session_id,
            transcript_path=transcript_path,
            cwd=cwd,
            store=store,
            classify_fn=call_ollama,
            source_provenance="extractor:stop-hook",
        )
        print(
            f"[comm-patterns-extract] session={session_id[:8]} "
            f"skipped={stats['skipped']} seen={stats['turns_seen']} "
            f"classified={stats['turns_classified']} rows={stats['rows_written']} "
            f"watermark={stats['watermark_before']}->{stats['watermark_after']}",
            file=sys.stderr,
        )
    except Exception as e:
        # Fail-soft: never block session end. Truncate the message — Supabase
        # constraint errors sometimes echo row values, which can include
        # scrubbed-but-still-sensitive surrounding context.
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[comm-patterns-extract] error: {msg}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
