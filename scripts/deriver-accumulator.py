"""Stop-hook accumulator: buffer session transcript for later Deriver consumption.

Parses the Claude Code session JSONL transcript and appends user/agent turns
to a session-scoped local buffer file under
``~/.claude/.deriver-buffer/<project-hash>/<session-id>.jsonl``.

The buffer is consumed by the SessionEnd hook (Slice 6) which runs the
Deriver (Ollama + fallback) on the accumulated content, then clears the file.

Invariants:
- **Never** blocks the session end. Exits 0 on every path.
- Survives ``claude`` restart mid-session — appends, never truncates.
- Non-standard sessions (sandcastle, worktree, headless) skip silently.

Registered in ``.claude-userlevel/settings.json`` under the ``Stop`` event.

Hook input (stdin, JSON):
  session_id, transcript_path, cwd, hook_event_name
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: re-exec under venv if running under system Python
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent.parent
_venv_py = _root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if (
    __name__ == "__main__"
    and _venv_py.exists()
    and Path(sys.executable).resolve() != _venv_py.resolve()
):
    sys.exit(subprocess.call([str(_venv_py), str(Path(__file__).resolve())]) and 0)

# ---------------------------------------------------------------------------
# Buffer config
# ---------------------------------------------------------------------------

# Buffer root lives under ~/.claude so it survives across sessions and follows
# the same convention as session-snapshots (scripts/pre-compact-backup.py).
BUFFER_ROOT = Path.home() / ".claude" / ".deriver-buffer"


def _project_hash(cwd: str) -> str:
    """Stable hash of the project root directory.

    Uses the first 12 hex chars of SHA-256 of the absolute, resolved cwd
    path.  Same project → same hash across devices (assuming the same clone
    path within the user's home), so the SessionEnd hook (Slice 6) can find
    the buffer.
    """
    raw = os.path.realpath(cwd).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:12]


def _should_skip(cwd: str) -> bool:
    """Return True for non-standard sessions that should not be accumulated."""
    if not cwd:
        return True
    cwd_lower = cwd.lower()
    # Sandcastle containers
    if "sandcastle" in cwd_lower:
        return True
    # git worktrees under .claude/
    if ".claude" in cwd_lower and "worktrees" in cwd_lower:
        return True
    return False


def accumulate(session_id: str, transcript_path: str, cwd: str) -> Path | None:
    """Read the transcript, append entries to the session buffer file.

    Returns the buffer file path, or None if skipped.
    """
    if _should_skip(cwd):
        return None

    p = Path(transcript_path)
    if not p.exists():
        print(f"[deriver-accumulator] transcript not found: {transcript_path}", file=sys.stderr)
        return None

    # Resolve buffer path
    proj_hash = _project_hash(cwd)
    buffer_dir = BUFFER_ROOT / proj_hash
    buffer_dir.mkdir(parents=True, exist_ok=True)
    buffer_path = buffer_dir / f"{session_id}.jsonl"

    # Read transcript entries and append new ones to the buffer
    # JSONL format: one JSON object per line, already the transcript format.
    count = 0
    with p.open("r", encoding="utf-8", errors="replace") as f_in:
        with buffer_path.open("a", encoding="utf-8", newline="") as f_out:
            for line in f_in:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines — they are rare but not zero in
                    # interrupted sessions.
                    continue
                # Only accumulate user and assistant turns (not system
                # messages or tool results — those are derivable from the
                # user/assistant content).
                role = obj.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                f_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
                count += 1

    if count:
        print(
            f"[deriver-accumulator] buffered {count} turns to {buffer_path}",
            file=sys.stderr,
        )
    else:
        print(f"[deriver-accumulator] no buffered turns for {session_id}", file=sys.stderr)

    return buffer_path


def main():
    hook_input = json.loads(sys.stdin.read())

    session_id = hook_input.get("session_id") or hook_input.get("sessionId") or "unknown-session"
    transcript_path = hook_input.get("transcript_path") or hook_input.get("transcriptPath") or ""
    cwd = hook_input.get("cwd") or os.environ.get("CLAUDE_CWD", "")

    accumulate(session_id, transcript_path, cwd)

    # Always exit 0 — Stop hook must never block session end.
    sys.exit(0)


if __name__ == "__main__":
    main()
