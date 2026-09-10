"""PreToolUse hook: block writes to protected files (standalone, project-level).

Standalone counterpart to ``scripts/protected-files.py`` (#1792): this copy
carries zero imports from ``scripts/lib``, ``hook-resolver``, or
``principal`` so it runs unmodified as a project-scoped hook in CI/Actions,
where none of those repo-internal seams exist. Consequently it is NOT
principal-aware — unlike the user-level hook, it always blocks both
canonical and mirror protected files, with no "live owner" bypass. This is
an intentional fail-closed trade-off for the CI surface; see the PR
description for the accepted consequence (it also blocks the live owner's
local interactive edits to canonical files via this project hook, even
though the still-unchanged user-level hook alone would allow them).

See ``docs/security/agent-boundaries.md`` for the full protected-file
policy this classification is drawn from.
"""

import json
import os
import sys
from pathlib import Path

# Repo-level CANONICAL sources that are protected at the file level.
#
# Policy rationale: all code changes go through PRs with CI + code review,
# which is the primary safety gate. File-level blocking is reserved for the
# narrow surface where a bad subagent edit could leak secrets into git history
# *before* review has a chance to catch it — weakening the scanners themselves.
T2_CANONICAL = {
    ".gitleaks.toml",
    ".pre-commit-config.yaml",
}

# User-level MIRROR paths (relative to ``~/.claude/``). Canonical source for
# these lives in the repo; a manual copy (the install script was retired in
# #1800) puts them into ``~/.claude/``. Kept for conceptual parity with the
# user-level hook even though a project-scoped hook observing a ``~/.claude/*``
# path is unlikely in practice.
_USER_LEVEL_PROTECTED_FILES = {
    "settings.json",
    "SOUL.md",
}


def _user_claude_home() -> str:
    """Return the user-level Claude home directory as a forward-slash string.

    Standalone inline lookup (no harness seam): honours ``$JARVIS_CLAUDE_HOME``
    when set, falling back to ``~/.claude``.
    """
    override = os.environ.get("JARVIS_CLAUDE_HOME")
    home = Path(override) if override else (Path.home() / ".claude")
    return home.as_posix().rstrip("/")


def normalize_path(path: str) -> str:
    """Normalize a file path for comparison: forward slashes, strip leading ./"""
    path = path.replace("\\", "/")
    for marker in ("/jarvis/", "\\jarvis\\"):
        idx = path.find(marker)
        if idx != -1:
            path = path[idx + len(marker) :]
            break
    if path.startswith("./"):
        path = path[2:]
    return path


def _is_user_level_protected(normalized: str) -> bool:
    """True if `normalized` points at a user-level protected file under ``~/.claude/``."""
    claude_home = _user_claude_home()
    prefix = os.path.normcase(claude_home + "/")
    candidate = os.path.normcase(normalized)
    if not candidate.startswith(prefix):
        return False
    rel = normalized[len(prefix) :]
    if rel in _USER_LEVEL_PROTECTED_FILES:
        return True
    parts = rel.split("/")
    return len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md"


def classify(file_path: str) -> str | None:
    """Classify a path as ``"canonical"`` (repo-side T2), ``"mirror"``
    (user-level T2 under ``~/.claude/``), or ``None`` (not protected).
    """
    normalized = normalize_path(file_path)
    if normalized in T2_CANONICAL:
        return "canonical"
    if _is_user_level_protected(normalized):
        return "mirror"
    return None


def is_protected(file_path: str) -> bool:
    """Backwards-compat: True iff path matches any protected category."""
    return classify(file_path) is not None


def should_block(file_path: str) -> bool:
    """Standalone block decision — no principal awareness.

    Returns ``True`` iff the path is protected (canonical or mirror). Unlike
    the user-level hook, there is no live-principal bypass: this project
    hook runs the same way in CI/Actions and in an interactive session, and
    it must fail closed in both.
    """
    return classify(file_path) is not None


def _block_reason(file_path: str, classification: str) -> str:
    """Compose a human-readable reason for the block decision."""
    if classification == "mirror":
        return (
            f"BLOCKED: '{file_path}' is a user-level mirror under ~/.claude/. "
            "Edit the canonical source in the jarvis repo "
            "(config/SOUL.md, .claude-userlevel/...), open a PR, then copy the "
            "changed file into ~/.claude/ by hand on this device (no installer). "
            "Direct edits here drift silently from the repo source."
        )
    return (
        f"BLOCKED: '{file_path}' is a protected canonical source (project-level "
        "hook, no principal bypass). Edits to enforcement/secret-scanning "
        "infrastructure must go through a PR + review, even for the live owner. "
        "See docs/security/agent-boundaries.md for the full matrix."
    )


def block(file_path: str, classification: str | None = None):
    """Output deny JSON and exit 2."""
    if classification is None:
        classification = classify(file_path) or "canonical"
    reason = _block_reason(file_path, classification)
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    json.dump(result, sys.stdout)
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    if not should_block(file_path):
        sys.exit(0)

    block(file_path, classify(file_path))


if __name__ == "__main__":
    main()
