#!/usr/bin/env python3
"""Status line for Claude Code.

Reads the status-line JSON payload on stdin and prints a single line:

    <model> | ctx <N>% | <git-branch>

Replaces the old (now-unsupported) settings.json `statusLine.type: "inline"`
+ `format: "{model} | ctx {context_usage}% | {git_branch}"`, which Claude Code
no longer recognises (only `type: "command"` is valid). Wired in via
settings.json -> statusLine.command.

Payload fields used (see Claude Code status-line schema):
- model.display_name          -> model name
- context_window.used_percentage -> context usage (may be null early in session)
- session_id                  -> key into the compaction-generation counter
- cwd                         -> dir to resolve the git branch from

The `gen K` segment is the dumb-zone signal under auto-compact: `ctx %` only
tracks quality up to the first compaction (it resets on every summary), whereas
`gen K` counts how many lossy summaries this session has survived — the metric
that actually keeps climbing as the session degrades. Written by the PreCompact
hook (`scripts/pre-compact-backup.py` -> `_bump_compaction_count`); suppressed
when K is 0 so fresh sessions stay clean.
"""
import json
import subprocess
import sys
from pathlib import Path


def _compaction_gen(session_id: str | None) -> int:
    """Read the per-session compaction count written by the PreCompact hook.

    Mirror of `_sanitize_session_id` in pre-compact-backup.py. Best-effort:
    any miss/error -> 0 (rendered as no `gen` segment at all).
    """
    if not session_id:
        return 0
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    if not safe:
        return 0
    try:
        f = Path.home() / ".claude" / "compaction-counts" / f"{safe}.txt"
        return int(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # No/invalid payload: emit nothing rather than a Python traceback.
        return

    model = data.get("model", {}).get("display_name") or "?"

    cw = data.get("context_window") or {}
    used = cw.get("used_percentage")
    ctx = f"ctx {int(used)}%" if isinstance(used, (int, float)) else "ctx -"

    gen = _compaction_gen(data.get("session_id") or data.get("sessionId"))
    if gen > 0:
        ctx = f"{ctx} · gen {gen}"

    cwd = data.get("cwd") or data.get("workspace", {}).get("current_dir") or "."
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    branch = branch or "no repo"

    print(f"{model} | {ctx} | {branch}")


if __name__ == "__main__":
    main()
