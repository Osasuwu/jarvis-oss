"""LabelMigrator — pure planner from clean schema + actual snapshot → plan.

Four migration behaviors (all tested through the public ``plan()`` method):

* **Rename** — an actual label maps to a clean label under a different name.
  Emits ``gh label edit --name`` (rename preserves issue/PR associations).
* **Collision merge** — multiple actual labels map to the same clean label.
  Emits a merge action.
* **Orphan** — actual label has no mapping and no match in the clean schema.
  Flagged confirm-required, never auto-executed.
* **Add** — clean label missing from the actual set.

Routine (post-migration) mode is **additive-only**: no renames, no merges,
no orphan detection.

The action containers are immutable value objects: ``frozen=True`` *and*
``tuple`` collection fields, so a constructed plan cannot be mutated in place
by a caller (a plain ``list`` field on a frozen dataclass is still
``.append()``-able — the footgun this avoids). An executor resolves a clean
label's color/description from the canonical schema by name when applying an
``AddAction``; the planner intentionally carries only the name.
"""

from __future__ import annotations

from dataclasses import dataclass

from .label_schema import CleanLabel


@dataclass(frozen=True)
class ActualLabel:
    """A label as found in a repo snapshot."""

    name: str
    color: str = ""
    description: str = ""


# ── Plan action types ────────────────────────────────────────────────


@dataclass(frozen=True)
class RenameAction:
    """Rename an actual label in place to match the clean name."""

    old_name: str
    new_name: str


@dataclass(frozen=True)
class MergeAction:
    """Consolidate one or more source labels into a single target.

    Execution strategy differs from RenameAction: an executor must reassign
    all issues/PRs bearing each source label to the target, then delete the
    source labels (a multi-step destructive operation). Do not use ``gh label
    edit --name`` for merge actions — that only works for renames.

    A single-source merge (``len(source_names) == 1``) is NOT a rename
    optimisation. The planner emits it only when the rename target already
    exists in the actual set, so it must still execute as re-tag-then-delete —
    a plain rename would hit GitHub 422 (name already in use). An executor must
    not special-case ``len == 1`` into a ``gh label edit --name`` rename.
    """

    source_names: tuple[str, ...]
    target_name: str


@dataclass(frozen=True)
class AddAction:
    """Create a clean label that does not yet exist on the repo.

    Executor contract: resolve the label's color and description from the
    canonical schema by calling clean_label_by_name(label_name). The planner
    carries only the name; the executor fills in the color/description when
    applying the action.
    """

    label_name: str


@dataclass(frozen=True)
class OrphanLabel:
    """Label in the actual set that has no mapping to the clean schema.

    These are **never auto-executed** — the owner must confirm each one.
    """

    name: str
    reason: str = "No mapping to clean schema"


@dataclass(frozen=True)
class LabelPlan:
    """Complete migration plan produced by the LabelMigrator.

    Collection fields are tuples: the plan is an immutable value object,
    safe to pass around without a caller mutating it in place.
    """

    renames: tuple[RenameAction, ...] = ()
    merges: tuple[MergeAction, ...] = ()
    adds: tuple[AddAction, ...] = ()
    orphans: tuple[OrphanLabel, ...] = ()

    @property
    def has_actions(self) -> bool:
        """True when the plan contains at least one action of any kind
        (including confirm-required orphans)."""
        return bool(self.renames or self.merges or self.adds or self.orphans)

    @property
    def has_executable_actions(self) -> bool:
        """True when the plan has actions safe to auto-execute
        (renames/merges/adds). Excludes orphans, which are confirm-required —
        an executor should gate auto-execution on this, not ``has_actions``."""
        return bool(self.renames or self.merges or self.adds)

    @property
    def needs_review(self) -> bool:
        """True when the plan contains confirm-required orphans."""
        return bool(self.orphans)


# ── Migrator ─────────────────────────────────────────────────────────


class LabelMigrator:
    """Pure planner: clean schema + mapping + actual snapshot → LabelPlan.

    Parameters
    ----------
    clean_schema:
        Iterable of CleanLabel — the desired canonical label set.
    mapping:
        Optional dict mapping *actual* label names → *clean* label names.
        Only labels present in the mapping are candidates for rename/merge.
        Actual labels not in the mapping and not matching a clean name are
        flagged as orphans. Every mapping *target* must be a name in
        ``clean_schema`` — a typo'd target is rejected at construction time
        rather than silently producing a rename toward a non-existent label.

    Raises
    ------
    ValueError:
        If any mapping value is not a name present in ``clean_schema``.
    """

    def __init__(
        self,
        clean_schema: list[CleanLabel],
        mapping: dict[str, str] | None = None,
    ):
        self._clean_names: set[str] = {lb.name for lb in clean_schema}
        # Copy the mapping dict to prevent caller mutation bypassing validation.
        self._mapping: dict[str, str] = dict(mapping) if mapping is not None else {}

        # Fail fast on mapping targets that don't exist in the clean schema.
        # Without this, a typo (e.g. "priorty:high") silently produces a
        # RenameAction pointing at a non-existent clean label that an
        # executor would apply blindly.
        bad_targets = {
            target
            for target in self._mapping.values()
            if target not in self._clean_names
        }
        if bad_targets:
            raise ValueError(
                "mapping targets not present in clean schema: "
                f"{sorted(bad_targets)}"
            )

    # ── Public API ────────────────────────────────────────────────────

    def plan(
        self,
        actual: list[ActualLabel],
        *,
        routine: bool = False,
    ) -> LabelPlan:
        """Produce a LabelPlan reconciling *actual* labels toward the schema.

        Parameters
        ----------
        actual:
            Labels currently present on the repo (from a snapshot).
        routine:
            When True, emit *only* AddActions for clean labels not yet
            present.  No renames, merges, or orphan detection.
        """
        if routine:
            return self._plan_routine(actual)
        return self._plan_migration(actual)

    # ── Internal: migration mode ──────────────────────────────────────

    def _plan_migration(self, actual: list[ActualLabel]) -> LabelPlan:
        actual_names = {a.name for a in actual}

        # Already match a clean label — canonical, no action needed.
        already_clean = actual_names & self._clean_names

        # Labels with an explicit mapping entry, *excluding* any that are
        # already canonical. A label whose name is already a clean name is
        # never renamed away, even if a stale mapping entry also names it as
        # a source — leaving the canonical label in place beats emitting a
        # destructive rename of a label that's already correct.
        mapped = (actual_names & set(self._mapping.keys())) - already_clean

        # Labels present in actual but not in clean schema and not mapped.
        orphan_names = actual_names - self._clean_names - mapped

        # Group mapped labels by their target clean name.
        target_to_sources: dict[str, list[str]] = {}
        for actual_name in sorted(mapped):  # Deterministic iteration order
            target = self._mapping[actual_name]
            target_to_sources.setdefault(target, []).append(actual_name)

        renames: list[RenameAction] = []
        merges: list[MergeAction] = []
        for target in sorted(target_to_sources.keys()):  # Deterministic order
            sources = target_to_sources[target]
            # If the target is already in the actual set or there are multiple
            # sources, emit a merge action. A rename would fail (target already
            # exists) or lose information (multiple sources mapping to one).
            if target in already_clean or len(sources) > 1:
                merges.append(
                    MergeAction(
                        source_names=tuple(sorted(sources)),
                        target_name=target,
                    )
                )
            else:
                # Single source, target not yet in actual → rename.
                # src ≠ target is guaranteed: src ∈ mapped ⊂ (actual \ clean),
                # target ∈ clean (validated at __init__), so they never coincide.
                src = sources[0]
                renames.append(RenameAction(old_name=src, new_name=target))

        # Clean labels not yet present and not already targeted by a
        # rename/merge.
        targeted = already_clean | set(target_to_sources.keys())
        adds = [
            AddAction(label_name=name)
            for name in sorted(self._clean_names - targeted)
        ]

        orphans = [OrphanLabel(name=name) for name in sorted(orphan_names)]

        return LabelPlan(
            renames=tuple(renames),
            merges=tuple(merges),
            adds=tuple(adds),
            orphans=tuple(orphans),
        )

    # ── Internal: routine (additive-only) mode ────────────────────────

    def _plan_routine(self, actual: list[ActualLabel]) -> LabelPlan:
        actual_names = {a.name for a in actual}
        missing = self._clean_names - actual_names
        adds = tuple(AddAction(label_name=name) for name in sorted(missing))
        return LabelPlan(adds=adds)
