"""Stop-hook entry: Deriver — per-session-end implicit-memory pass.

Invokes the Deriver pipeline to extract ``user`` and ``feedback`` memory
candidates from the accumulated session transcript, then inserts them
with ``requires_review=true`` for later owner review via ``/learn``.

Must run **after** ``deriver-accumulator.py`` in the Stop hook chain, so
the buffer is populated before the Deriver reads it.

Hook input on stdin (Claude Code Stop event)::

  {
    "session_id": "...",
    "transcript_path": "/path/to/file.jsonl",
    "cwd": "...",
    "hook_event_name": "Stop",
    ...
  }

Invariants:
  * **Never** blocks the session end.  Exits 0 on every path.
  * Sandcastle / worktree / headless sessions skip silently (delegated to
    the accumulator — if the accumulator skipped, the buffer is absent,
    and the Deriver returns empty).
  * All inserted rows have ``requires_review=true`` and
    ``source_provenance='deriver:<session-id>'``.

Registered in ``.claude-userlevel/settings.json`` under the ``Stop`` event.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python.
# Same shape as scripts/deriver-accumulator.py and scripts/comm-patterns-extract.py.
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
    #
    # subprocess.call itself can raise OSError (PermissionError, ENOEXEC) if
    # the venv binary exists but isn't executable — e.g. permissions stripped
    # by a git checkout on a case-sensitive filesystem, or a botched reinstall.
    # The docstring contract is "Never blocks the session end. Exits 0 on
    # every path." — so we swallow OSError too.
    try:
        sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]) and 0)
    except OSError as _e:
        print(f"[deriver-sessionend] venv re-exec failed: {_e}", file=sys.stderr)
        sys.exit(0)

# ---------------------------------------------------------------------------
# Under venv — import deps
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
    from deriver.pipeline import derive_from_session, project_hash
except Exception as _import_err:
    print(f"[deriver-sessionend] import skipped: {_import_err}", file=sys.stderr)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Hook input
# ---------------------------------------------------------------------------


def _read_hook_input() -> dict:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[deriver-sessionend] bad hook input: {type(e).__name__}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    payload = _read_hook_input()
    session_id = payload.get("session_id") or ""
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_CWD", "")

    if not session_id:
        print("[deriver-sessionend] missing session_id; skip", file=sys.stderr)
        return 0

    if not cwd:
        print("[deriver-sessionend] missing cwd; skip", file=sys.stderr)
        return 0

    phash = project_hash(cwd)
    try:
        inserted = derive_from_session(
            session_id,
            project_hash=phash,
        )
        count = len(inserted)
        if count:
            ids = ",".join(str(u)[:8] for u in inserted)
            print(
                f"[deriver-sessionend] session={session_id[:8]} candidates={count} ids={ids}",
                file=sys.stderr,
            )
        else:
            print(f"[deriver-sessionend] session={session_id[:8]} no candidates", file=sys.stderr)
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[deriver-sessionend] error: {msg}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
