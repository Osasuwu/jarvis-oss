"""PreToolUse hook: block writes to protected files (principal-aware).

Checks Edit/Write tool inputs for file paths that match the protected list.
Decision is principal-sensitive (#426):

- ``live`` (interactive owner) + repo-level canonical source → exit 0,
  let the harness ask for permission. Owner can approve a one-off edit.
- ``live`` + user-level mirror (``~/.claude/*``) → block. These are
  installer-managed; direct edits drift from source on next ``install.ps1``.
- ``autonomous`` / ``subagent`` / ``supervised`` + any protected file → block.
  No human eye on these contexts; protected-file edits must be promoted
  through the canonical PR + installer flow.

See ``docs/security/agent-boundaries.md`` for the full action × principal
matrix and ``scripts/principal.py`` for detection logic.
"""

import json
import os
import sys
from pathlib import Path

# Wire in principal detection. Importing by relative name works because the
# hook is invoked with cwd inside the repo and ``scripts/`` is the directory
# this file lives in; for safety we also fall back to a path-based import.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import principal as _principal  # noqa: E402
finally:
    pass

# Repo-level CANONICAL sources that are protected at the file level.
#
# Policy rationale: all code changes go through PRs with CI + code review,
# which is the primary safety gate. File-level blocking is reserved for the
# narrow surface where a bad subagent edit could leak secrets into git history
# *before* review has a chance to catch it — weakening the scanners themselves.
# Everything else (SOUL.md, CLAUDE.md, mcp-memory/*, .mcp.json, etc.) is
# fine to edit in a feature branch; review rejects anything wrong.
T2_CANONICAL = {
    # Secret-leakage surface: weakening these allows secrets into git history
    # before review, which is permanent even if the PR is later rejected.
    ".gitleaks.toml",
    ".pre-commit-config.yaml",
    "scripts/secret-scanner.py",
    # Enforcement infrastructure: a non-live principal that can edit these
    # scripts could strip the protected list or spoof principal detection,
    # then freely edit the scanner surface above.
    "scripts/protected-files.py",
    "scripts/principal.py",
}

# Backwards-compat alias — older imports / docs may still reference this.
PROTECTED_FILES = T2_CANONICAL

# User-level MIRROR paths (relative to ``~/.claude/``). Canonical source for
# these lives in the repo (``config/SOUL.md``, ``.claude-userlevel/...``);
# the installer copies/templates them into ``~/.claude/``. Direct edits drift
# from source on the next ``install.ps1 --apply``, so we block ALL principals
# (including ``live``) and direct the user to the installer flow.
_USER_LEVEL_PROTECTED_FILES = {
    "settings.json",
    "SOUL.md",
    ".mcp.json",
}


def _user_claude_home() -> str:
    """Return the user-level Claude home directory as a forward-slash string."""
    override = os.environ.get("JARVIS_CLAUDE_HOME")
    home = Path(override).expanduser() if override else (Path.home() / ".claude")
    return home.as_posix().rstrip("/")


def normalize_path(path: str) -> str:
    """Normalize a file path for comparison: forward slashes, strip leading ./"""
    path = path.replace("\\", "/")
    # Strip absolute prefix up to repo root patterns
    for marker in ("/jarvis/", "\\jarvis\\"):
        idx = path.find(marker)
        if idx != -1:
            path = path[idx + len(marker) :]
            break
    # Strip leading ./
    if path.startswith("./"):
        path = path[2:]
    return path


def _is_user_level_protected(normalized: str) -> bool:
    """True if `normalized` points at a user-level protected file under ``~/.claude/``.

    Anchored to the resolved user home (or ``$JARVIS_CLAUDE_HOME``) so paths
    like ``some-other-project/.claude/settings.json`` don't false-positive.

    On Windows, the filesystem is case-insensitive so prefix matching is too
    (e.g. ``c:/users/jdoe/.claude/...`` must still match regardless of drive
    letter / user dir casing); ``os.path.normcase`` handles this. POSIX is
    left case-sensitive by the same call.
    """
    claude_home = _user_claude_home()
    prefix = os.path.normcase(claude_home + "/")
    candidate = os.path.normcase(normalized)
    if not candidate.startswith(prefix):
        return False
    rel = normalized[len(prefix) :]  # slice the original so case of `rel` is preserved
    if rel in _USER_LEVEL_PROTECTED_FILES:
        return True
    # skills/<name>/SKILL.md — any user-level skill definition.
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


def should_block(file_path: str, principal: str) -> bool:
    """Principal-aware block decision.

    Returns ``True`` iff the hook should block the write attempt for this
    ``(path, principal)`` pair.

    Policy (matches ``docs/security/agent-boundaries.md`` matrix):
    - Not protected → never block.
    - ``live`` + ``canonical`` → don't block (let harness ask owner).
    - Any other combination of protected × principal → block.
    """
    classification = classify(file_path)
    if classification is None:
        return False
    if principal == "live" and classification == "canonical":
        return False
    return True


def _block_reason(file_path: str, classification: str, principal: str) -> str:
    """Compose a human-readable reason for the block decision."""
    if classification == "mirror":
        return (
            f"BLOCKED: '{file_path}' is a user-level mirror under ~/.claude/. "
            "Edit the canonical source in the jarvis repo "
            "(config/SOUL.md, .claude-userlevel/...), open a PR, then propagate "
            "with `install.ps1 -Apply` (or `install.sh -a`). Direct edits drift "
            "from source on next install."
        )
    # canonical, but principal != live
    return (
        f"BLOCKED: '{file_path}' is a protected canonical source and the "
        f"current principal is '{principal}'. Only interactive (live) owner "
        "sessions can edit canonical sources directly; autonomous loops, "
        "subagents, and dispatched agents must document the change in the PR "
        "description and leave the file for the owner to edit. See "
        "docs/security/agent-boundaries.md for the full matrix."
    )


def block(file_path: str, classification: str | None = None, principal: str | None = None):
    """Output deny JSON and exit 2.

    ``classification`` and ``principal`` are optional for backwards
    compatibility with the pre-#426 signature; main() always supplies them.
    """
    if classification is None:
        classification = classify(file_path) or "canonical"
    if principal is None:
        principal = "unknown"
    reason = _block_reason(file_path, classification, principal)
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

    classification = classify(file_path)
    if classification is None:
        sys.exit(0)

    principal = _principal.detect()

    # live + canonical → let the harness handle the permission ask.
    if principal == "live" and classification == "canonical":
        sys.exit(0)

    block(file_path, classification, principal)


if __name__ == "__main__":
    main()
