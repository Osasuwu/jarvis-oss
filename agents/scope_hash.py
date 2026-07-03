"""Deterministic scope-files hash — single source of truth for drift detection.

Consolidated from copies previously living in ``executor.py``,
``perception_github.py``, and ``scripts/dispatcher_smoke_live.py`` (issue
#773). Kept in its own module so callers like ``morning_check.py`` don't
import a hash utility from ``agents/executor.py`` (a subprocess-spawning
module).
"""

from __future__ import annotations

import hashlib


def _hash_scope_files(scope_files: list[str] | tuple[str, ...]) -> str:
    """Deterministic scope-files hash for drift detection.

    Matches the approval-time hashing convention S2-1 expects. Sort the
    list so "files reordered" doesn't read as "files changed"; newline-join
    so a glob that grew by one file ``['a']`` vs ``['a', 'b']`` produces
    different hashes (concatenation ``'ab'`` vs ``'a'`` would too, but
    a separator makes the invariant human-readable).
    """
    normalized = "\n".join(sorted(scope_files or []))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
