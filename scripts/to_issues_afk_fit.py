"""AFK-fit static path-grep helper for /to-issues (issue #642).

The /to-issues AFK-fit checklist has four questions. Question 1 is static
and lives here: do the slice's declared-changed files intersect any
protected/safety-critical glob from the per-repo list in
``config/protected-paths.json``?

Questions 2-4 are LLM-judgement and live as prose in /to-issues SKILL.md.

Adding a new repo to the system means adding an entry to the JSON config —
never editing this module or any SKILL.md (issue #642 hard constraint).
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path


def load_protected_paths(path: str | Path) -> dict[str, list[str]]:
    """Load the per-repo protected-path map from JSON.

    Underscore-prefixed keys (`_comment`, etc.) are metadata and excluded.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: list(v) for k, v in data.items() if not k.startswith("_")}


def intersects_protected(
    declared_files: list[str],
    repo: str,
    config: dict[str, list[str]],
) -> list[str]:
    """Return the subset of ``declared_files`` that match a protected glob.

    Globs follow gitignore-style semantics translated into fnmatch — the leading
    ``**/`` is implicit (we match against the repo-relative path). Unknown
    repos return an empty list by design (fail-open); /to-issues prose must
    surface "unknown repo" as a manual judgement prompt instead.
    """
    globs = config.get(repo, [])
    if not globs:
        return []

    matched: list[str] = []
    for declared in declared_files:
        norm = declared.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        for glob in globs:
            if _matches(norm, glob):
                matched.append(declared)
                break
    return matched


def _matches(path: str, glob: str) -> bool:
    """fnmatch with `**` expanded to mean 'any depth'."""
    # fnmatch treats `**` as `*` (no recursion). Translate explicit `prefix/**`
    # into "starts with prefix/".
    if glob.endswith("/**"):
        prefix = glob[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if "/**/" in glob:
        head, tail = glob.split("/**/", 1)
        if not path.startswith(head + "/"):
            return False
        return fnmatch.fnmatchcase(path[len(head) + 1 :], "*/" + tail) or fnmatch.fnmatchcase(
            path[len(head) + 1 :], tail
        )
    return fnmatch.fnmatchcase(path, glob)
