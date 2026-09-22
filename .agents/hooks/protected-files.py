"""PreToolUse hook: block edits to protected files, regardless of who is editing.

Standalone counterpart to a user-level (interactive-session-scoped) copy of the same
check: this copy carries zero imports from any project-internal helper library or
session/principal-detection module, so it runs unmodified as a project-scoped hook in
CI/Actions, where none of those repo-internal seams exist.

Consequently it is NOT principal-aware — unlike a user-level hook, it always blocks
edits to protected files, with no "live owner" bypass. This is an intentional
fail-closed trade-off for the CI surface: the alternative (branch on whether a human
is present) has no reliable signal to make that branch on from inside a hook
subprocess, which always receives piped stdin whether or not a terminal is attached.
The accepted consequence is that this project hook also blocks the live owner's own
local interactive edits to canonical protected files, even where a still-present
user-level hook alone would allow them. See docs/agent-safety-hooks.md for the
practice this hook backs; its "Our own choice" part records this trade-off and
why the presence-detection alternative was dropped.

Matches Edit/Write/NotebookEdit only: a shell command that writes a protected file
is not seen by this hook. Exits 0 on empty or unparsable input (fails open there).

Reads tool_input from stdin (JSON). Exits 2 to block if the edited path matches a
protected file.

Wire it up via the matcher in `settings.snippet.json` (same directory).

CUSTOMIZE: the two sets below are placeholders. Replace them with your own repo's
files whose own compromise would weaken review itself — secret-scanner config,
CI gate definitions, branch-protection scripts — not just "important" files.
Everything else should go through ordinary PR + CI + review instead (option 1,
"Written rules only", in docs/agent-safety-hooks.md covers when that is enough).
"""

import json
import os
import sys

# Canonical protected files: repo-relative paths, checked against every edit
# regardless of harness (CI, agent, or the live operator's own local edit).
# CUSTOMIZE: replace with the files in your own repo that gate review itself.
PROTECTED_CANONICAL = {
    ".agents/hooks/protected-files.py",
    ".agents/hooks/github-authority-guard.py",
    ".agents/hooks/secret-scanner.py",
    ".agents/hooks/settings.snippet.json",
    ".gitleaks.toml",
}

# Mirror copies of the same protected content living at a second path (e.g. a
# user-level settings mirror of a project-level hook). Blocked for the same
# reason as the canonical copy: editing the mirror instead of the canonical
# file is not a legitimate way around this hook.
# CUSTOMIZE: leave empty if your repo has no such mirrored copies.
PROTECTED_MIRROR: set[str] = set()

FILE_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")


def normalize_path(path: str) -> str:
    """Normalize a tool-reported path to a repo-relative, forward-slash form."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def _canonicalize(path: str) -> str:
    """Resolve a path to its real, case-normalized filesystem identity.

    realpath collapses symlinks and NTFS aliases (trailing dot, trailing
    space, `::$DATA` alternate data stream) to the same underlying file;
    normcase folds case on case-insensitive filesystems. Two paths a naive
    string compare sees as different can still be the same file on disk —
    #74 found a case-insensitive filesystem let `.Agents/Hooks/
    Secret-Scanner.py` through a case-sensitive `endswith` match.
    """
    return os.path.normcase(os.path.realpath(path))


def classify(path: str) -> str | None:
    """Return 'canonical', 'mirror', or None for an unprotected path."""
    project_dir = _project_dir()
    input_canonical = _canonicalize(
        path if os.path.isabs(path) else os.path.join(project_dir, path)
    )
    canonical_matches = {
        _canonicalize(os.path.join(project_dir, p)): p for p in PROTECTED_CANONICAL
    }
    mirror_matches = {
        _canonicalize(os.path.join(project_dir, p)): p for p in PROTECTED_MIRROR
    }
    if input_canonical in canonical_matches:
        return "canonical"
    if input_canonical in mirror_matches:
        return "mirror"

    # Fallback: canonicalization can't help when the path doesn't exist on
    # disk (realpath still normalizes it, but without a real file/symlink to
    # resolve against, a resolved non-existent path can still coincide with
    # or miss a real one in edge cases) — the endswith string check is a
    # safety net, not a narrowing of what canonical comparison already caught.
    normalized = normalize_path(path)
    if any(normalized.endswith(p) for p in PROTECTED_CANONICAL):
        return "canonical"
    if any(normalized.endswith(p) for p in PROTECTED_MIRROR):
        return "mirror"
    return None


def block_reason(path: str, kind: str) -> str:
    return (
        f"BLOCKED: {path} is a protected file ({kind}). This hook has no "
        "live-operator bypass by design — it must run identically in CI, where "
        "there is no human to have earned one. Open a PR instead; see "
        "docs/agent-safety-hooks.md for why this file is protected this way."
    )


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in FILE_WRITE_TOOLS:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not isinstance(path, str) or not path:
        sys.exit(0)

    kind = classify(path)
    if kind is None:
        sys.exit(0)

    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": block_reason(path, kind),
        }
    }
    json.dump(result, sys.stdout)
    sys.exit(2)


if __name__ == "__main__":
    main()
