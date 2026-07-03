"""Pure decision core — Planner with 3-class file routing.

Given a desired ``Manifest`` and an ``ActualState`` (snapshot of the live
repo), emits an ordered action plan (list of ``Action``). All dry-run safe
— no network or filesystem mutation.

Ordering invariants
-------------------
1. MANAGED and LANGUAGE-TEST files are written BEFORE branch protection
   is applied (a bare repo would otherwise deadlock on checks that have
   never run).
2. Within the same class, file order is stable (sorted by path).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .manifest import FileClass, Manifest


class ActionKind(enum.Enum):
    WRITE_FILE = "write_file"
    """Overwrite or create a file at the target path."""

    DELETE_FILE = "delete_file"
    """Remove a file no longer in the managed set."""

    SET_CHECK_CONTEXTS = "set_check_contexts"
    """Update required check-contexts (branch protection or repo settings)."""


@dataclass
class Action:
    """A single planned action expressed by the Planner."""

    kind: ActionKind
    path: str
    content: Optional[str] = None
    """File content (for WRITE_FILE). Always None until render phase —
    the Executor must call Renderer.render() to populate this field
    before file mutations. Reserved for future slice."""

    file_class: Optional[str] = None
    """File class for traceability (managed/language_test/repo_custom)."""

    context_names: List[str] = field(default_factory=list)
    """Check-context names (for SET_CHECK_CONTEXTS)."""


class ActualState:
    """Snapshot of live repo state for comparison with the manifest.

    Pure data — populated by the ``Auditor`` shell.
    """

    def __init__(
        self,
        files: Optional[Dict[str, str]] = None,
        required_check_contexts: Optional[List[str]] = None,
    ):
        self.files: Dict[str, str] = files or {}
        """Path → content hash (or empty string if content unknown)."""

        self.required_check_contexts: List[str] = required_check_contexts or []


class Planner:
    """Produces an ordered action plan from manifest + actual state."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest

    def plan(self, actual: ActualState) -> List[Action]:
        """Compute the action plan.

        Returns actions in execution order:
        1. File writes (MANAGED → LANGUAGE-TEST)
        2. Check-context sync
        """
        actions: List[Action] = []
        seen_paths: set[str] = set()

        # ── WRITE_FILE for managed files ──────────────────────────────
        for path in sorted(self.manifest.resolved_managed_files):
            fclass = self.manifest.class_for_file(path)
            actions.append(Action(
                kind=ActionKind.WRITE_FILE,
                path=path,
                file_class=fclass.value,
            ))
            seen_paths.add(path)

        # ── WRITE_FILE for LANGUAGE-TEST files ────────────────────────
        for path in sorted(self.manifest.language_test_files):
            if path not in seen_paths:
                actions.append(Action(
                    kind=ActionKind.WRITE_FILE,
                    path=path,
                    file_class=FileClass.LANGUAGE_TEST.value,
                ))
                seen_paths.add(path)

        # ── DELETE_FILE for files that are in actual but not in any list ──
        actual_paths = set(actual.files.keys())
        all_managed = set(self._all_watched_paths())
        for path in sorted(actual_paths - all_managed):
            actions.append(Action(
                kind=ActionKind.DELETE_FILE,
                path=path,
            ))

        # ── SET_CHECK_CONTEXTS ─────────────────────────────────────────
        required = self.manifest.required_check_contexts
        if required:
            actions.append(Action(
                kind=ActionKind.SET_CHECK_CONTEXTS,
                path="<repo-settings>",
                context_names=list(required),
            ))

        return actions

    def _all_watched_paths(self) -> List[str]:
        # Every entry in custom_files classifies as REPO_CUSTOM — class_for_file
        # checks custom_files first — so no per-item filter is needed; the whole
        # list is watched (and thus excluded from DELETE_FILE).
        return (
            self.manifest.resolved_managed_files
            + self.manifest.language_test_files
            + list(self.manifest.custom_files)
        )

    def classify_file(self, path: str) -> FileClass:
        """Public 3-class router for a single file path.

        Used by tests and callers who need to know a file's disposition
        without constructing the full plan.
        """
        return self.manifest.class_for_file(path)
