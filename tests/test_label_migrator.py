"""Tests for the LabelMigrator pure planner.

Covers all four migration behaviors + routine mode through the public
``plan()`` interface (schema + snapshot in → plan out), plus canonical-schema
invariants and plan-object flags.

Plan collection fields are tuples (immutable value object), so equality
assertions compare against tuples, not lists.
"""

from __future__ import annotations

import pytest

from repo_baseline.label_migrator import (
    ActualLabel,
    AddAction,
    LabelMigrator,
    MergeAction,
    OrphanLabel,
    RenameAction,
)
from repo_baseline.label_schema import (
    CleanLabel,
    CLEAN_LABELS,
    clean_label_by_name,
    clean_label_names,
)


# ── Canonical schema invariants ──────────────────────────────────────


class TestSchemaInvariants:
    def test_no_epic_label_in_clean_schema(self):
        """'epic' must not be a canonical label.

        CLAUDE.md decision 2a7ae10e: milestone is the only grouping
        primitive — the term 'epic' is not used. Shipping an 'epic' label
        into the canonical schema would create it on every owned repo via
        the executor, permanently contradicting the convention.
        """
        assert "epic" not in clean_label_names()
        assert clean_label_by_name("epic") is None
        assert all(lb.name != "epic" for lb in CLEAN_LABELS)

    def test_clean_hex_colors_are_lowercase(self):
        """GitHub normalizes label colors to lowercase; the canonical
        schema must match so drift-detection string comparison doesn't
        report false positives on every sync run."""
        for lb in CLEAN_LABELS:
            assert lb.color == lb.color.lower(), (
                f"{lb.name} has non-lowercase color {lb.color!r}"
            )

    def test_clean_label_by_name_round_trips(self):
        """The O(1) lookup returns the same object present in CLEAN_LABELS."""
        for lb in CLEAN_LABELS:
            assert clean_label_by_name(lb.name) is lb
        assert clean_label_by_name("definitely-not-a-label") is None

    def test_no_same_color_across_categories(self):
        """No two labels in *different* categories share a hex color.

        Same color across semantic categories is visually ambiguous — a blue
        chip could be area:skills or sandcastle. Several pairs were de-duped by
        hand (status:in-progress, area:ci-quality, tier:2-review, tier:3-human,
        sandcastle, task); this pins the invariant so a future addition can't
        silently reintroduce a collision. Labels within the *same* category may
        share a color (the category context disambiguates), so the check is
        scoped to cross-category pairs only.
        """
        first_seen: dict[str, CleanLabel] = {}
        collisions: list[str] = []
        for lb in CLEAN_LABELS:
            prior = first_seen.get(lb.color)
            if prior is not None and prior.category != lb.category:
                collisions.append(
                    f"{prior.name} ({prior.category}) and "
                    f"{lb.name} ({lb.category}) share color {lb.color!r}"
                )
            first_seen.setdefault(lb.color, lb)
        assert not collisions, (
            "Cross-category color collisions:\n" + "\n".join(collisions)
        )


# ── Fixture helpers ──────────────────────────────────────────────────


def _schema(*names: str) -> list[CleanLabel]:
    """Build a minimal clean schema from the canonical set."""
    by_name = {lb.name: lb for lb in CLEAN_LABELS}
    return [by_name[n] for n in names]


def _actual(*names: str) -> list[ActualLabel]:
    """Build an actual-label snapshot from names alone."""
    return [ActualLabel(name=n) for n in names]


# ── Rename behaviour ─────────────────────────────────────────────────


class TestRename:
    def test_single_rename_via_mapping(self):
        """Actual label mapped to a clean label with different name → rename."""
        migrator = LabelMigrator(
            clean_schema=_schema("area:quality"),
            mapping={"old-quality": "area:quality"},
        )
        plan = migrator.plan(_actual("old-quality"))
        assert plan.renames == (
            RenameAction(old_name="old-quality", new_name="area:quality"),
        )
        assert plan.merges == ()
        assert plan.adds == ()
        assert plan.orphans == ()

    def test_rename_preserves_identity(self):
        """Plan emits rename, never delete+create (no AddAction for renamed)."""
        migrator = LabelMigrator(
            clean_schema=_schema("needs-research"),
            mapping={"needs-investigation": "needs-research"},
        )
        plan = migrator.plan(_actual("needs-investigation"))
        assert len(plan.renames) == 1
        # The clean label "needs-research" should NOT appear as an add
        # since it's already the target of the rename.
        added_names = {a.label_name for a in plan.adds}
        assert "needs-research" not in added_names

    def test_rename_when_target_already_in_actual_produces_merge(self):
        """When target is already in actual, emit merge not rename (avoid 422).

        This tests the CRITICAL M1 finding: if actual contains both a source
        label and its rename target simultaneously, the planner must emit a
        MergeAction (re-tag + delete) instead of a RenameAction that GitHub
        would reject with HTTP 422 (name already in use).
        """
        migrator = LabelMigrator(
            clean_schema=_schema("task"),
            mapping={"old-task": "task"},
        )
        plan = migrator.plan(_actual("task", "old-task"))
        # No rename — would fail on GitHub
        assert plan.renames == ()
        # Instead, merge old-task into task
        assert plan.merges == (MergeAction(source_names=("old-task",), target_name="task"),)
        assert plan.adds == ()
        assert plan.orphans == ()

    def test_already_matching_no_rename(self):
        """Actual label already matching clean name → no action."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high"),
            mapping={},
        )
        plan = migrator.plan(_actual("priority:high"))
        assert plan.renames == ()
        assert plan.merges == ()
        assert plan.adds == ()
        assert plan.orphans == ()


# ── Collision-on-target → merge ──────────────────────────────────────


class TestMerge:
    def test_two_labels_collide_into_one_target(self):
        """Two actual labels mapping to the same clean label → merge."""
        migrator = LabelMigrator(
            clean_schema=_schema("area:quality"),
            mapping={
                "test-audit": "area:quality",
                "area:ci-quality": "area:quality",
            },
        )
        plan = migrator.plan(_actual("test-audit", "area:ci-quality"))
        assert plan.merges == (
            MergeAction(
                source_names=("area:ci-quality", "test-audit"),
                target_name="area:quality",
            ),
        )
        assert plan.renames == ()
        # area:quality already targeted by the merge → not in adds.
        added_names = {a.label_name for a in plan.adds}
        assert "area:quality" not in added_names

    def test_many_to_one_consolidation(self):
        """Three-or-more sources → single merge set."""
        migrator = LabelMigrator(
            clean_schema=_schema("area:quality"),
            mapping={
                "quality-old": "area:quality",
                "qa-old": "area:quality",
                "test-audit": "area:quality",
            },
        )
        plan = migrator.plan(
            _actual("quality-old", "qa-old", "test-audit")
        )
        assert len(plan.merges) == 1
        merge = plan.merges[0]
        assert merge.target_name == "area:quality"
        assert sorted(merge.source_names) == [
            "qa-old",
            "quality-old",
            "test-audit",
        ]
        assert plan.renames == ()

    def test_mix_rename_and_merge(self):
        """One rename + one merge coexist when mapping cardinalities differ."""
        migrator = LabelMigrator(
            clean_schema=_schema("area:quality", "needs-research"),
            mapping={
                # Single source → rename
                "old-research": "needs-research",
                # Multiple sources → merge
                "qa-legacy": "area:quality",
                "test-audit": "area:quality",
            },
        )
        plan = migrator.plan(
            _actual("old-research", "qa-legacy", "test-audit")
        )
        assert len(plan.renames) == 1
        assert plan.renames[0] == RenameAction(
            old_name="old-research", new_name="needs-research"
        )
        assert len(plan.merges) == 1
        assert plan.merges[0].target_name == "area:quality"
        assert len(plan.merges[0].source_names) == 2


# ── Orphan detection (confirm-required) ──────────────────────────────


class TestOrphan:
    def test_unmapped_actual_label_flagged(self):
        """Actual label not in clean schema and not mapped → orphan."""
        migrator = LabelMigrator(
            clean_schema=_schema("task"),
            mapping={},
        )
        plan = migrator.plan(_actual("task", "some-adhoc-label"))
        assert len(plan.orphans) == 1
        assert plan.orphans[0] == OrphanLabel(name="some-adhoc-label")
        # "task" is already clean → no orphan.
        assert plan.renames == ()
        assert plan.merges == ()

    def test_orphan_carries_default_reason(self):
        """Orphan reason is populated so an executor can surface *why* a
        label is confirm-required (pins MINOR-10 — the field was previously
        never asserted, so a default-reason change would go unnoticed)."""
        migrator = LabelMigrator(clean_schema=_schema("task"), mapping={})
        plan = migrator.plan(_actual("task", "adhoc"))
        assert plan.orphans[0].reason == "No mapping to clean schema"

    def test_multiple_orphans_listed(self):
        """Multiple unmapped labels → all flagged."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("task", "adhoc-1", "adhoc-2"))
        assert len(plan.orphans) == 2
        orphan_names = {o.name for o in plan.orphans}
        assert orphan_names == {"adhoc-1", "adhoc-2"}

    def test_mapped_label_not_orphan(self):
        """Mapped label is NOT flagged as orphan even if not in clean."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high"),
            mapping={"urgent": "priority:high"},
        )
        plan = migrator.plan(_actual("urgent"))
        # "urgent" has a mapping → no orphan; becomes a rename.
        assert plan.orphans == ()
        assert len(plan.renames) == 1

    def test_no_orphans_when_all_mapped(self):
        """All actual labels accounted for → zero orphans."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high", "task"),
            mapping={
                "urgent": "priority:high",
                "chore": "task",
            },
        )
        plan = migrator.plan(_actual("urgent", "chore"))
        assert plan.orphans == ()


# ── Add behaviour ────────────────────────────────────────────────────


class TestAdd:
    def test_missing_clean_labels_added(self):
        """Clean labels not in actual set → AddAction."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high", "task", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("priority:high"))
        added_names = {a.label_name for a in plan.adds}
        assert added_names == {"draft", "task"}
        assert "priority:high" not in added_names

    def test_no_adds_when_schema_complete(self):
        """All clean labels present → no adds."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("task", "draft"))
        assert plan.adds == ()


# ── Mapping validation ───────────────────────────────────────────────


class TestMappingValidation:
    def test_unknown_mapping_target_rejected(self):
        """A mapping target not present in the clean schema is rejected at
        construction time — a typo'd target must not silently produce a
        rename toward a non-existent label (MAJOR-3)."""
        with pytest.raises(ValueError, match="not present in clean schema"):
            LabelMigrator(
                clean_schema=_schema("priority:high"),
                mapping={"urgent": "priorty:high"},  # typo in target
            )

    def test_valid_mapping_target_accepted(self):
        """A mapping target present in the clean schema constructs cleanly."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high"),
            mapping={"urgent": "priority:high"},
        )
        assert migrator is not None


# ── Clean-name-also-mapped interaction (MAJOR 5) ─────────────────────


class TestCleanNameAlsoMapped:
    def test_clean_named_label_not_renamed_away(self):
        """A label already matching a clean name is left untouched even if a
        stale mapping entry also names it as a source — no destructive
        rename, no orphan, no duplicate add. Pins the subtle MAJOR-5
        interaction where a name lives in both `clean_names` and `mapping`."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"),
            mapping={"task": "draft"},  # stale: 'task' is itself canonical
        )
        plan = migrator.plan(_actual("task"))
        assert plan.renames == ()
        assert plan.merges == ()
        assert plan.orphans == ()
        # 'task' stays as-is; only the genuinely-missing 'draft' is added.
        assert plan.adds == (AddAction(label_name="draft"),)


# ── Plan flags (MINOR 11) ────────────────────────────────────────────


class TestPlanFlags:
    def test_only_orphans_needs_review_but_not_executable(self):
        """A plan with only orphans is review-required but has nothing safe
        to auto-execute — `has_executable_actions` must be False so an
        executor gating on it does not fire on confirm-required work."""
        migrator = LabelMigrator(clean_schema=_schema("task"), mapping={})
        plan = migrator.plan(_actual("task", "adhoc"))
        assert plan.has_actions is True
        assert plan.has_executable_actions is False
        assert plan.needs_review is True

    def test_adds_are_executable_no_review(self):
        """A plan with adds and no orphans is executable and needs no review."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"), mapping={}
        )
        plan = migrator.plan(_actual("task"))  # 'draft' missing → add
        assert plan.has_executable_actions is True
        assert plan.needs_review is False

    def test_empty_plan_flags_all_false(self):
        """A converged repo yields a plan with every flag False."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"), mapping={}
        )
        plan = migrator.plan(_actual("task", "draft"))
        assert plan.has_actions is False
        assert plan.has_executable_actions is False
        assert plan.needs_review is False


# ── Routine (additive-only) mode ─────────────────────────────────────


class TestRoutine:
    def test_additive_only(self):
        """Routine mode only produces adds — no renames/merges/orphans."""
        migrator = LabelMigrator(
            clean_schema=_schema("priority:high", "task"),
            mapping={"urgent": "priority:high"},  # ignored in routine
        )
        plan = migrator.plan(
            _actual("urgent", "task"),
            routine=True,
        )
        # "priority:high" is missing from actual but has a mapping → still
        # emitted as an add (routine ignores mapping).
        assert len(plan.adds) == 1
        assert plan.adds[0].label_name == "priority:high"
        assert plan.renames == ()
        assert plan.merges == ()
        assert plan.orphans == ()

    def test_routine_idempotent_when_complete(self):
        """Routine on an already-converged repo → empty plan."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("task", "draft"), routine=True)
        assert plan.has_actions is False

    def test_routine_ignores_ad_hoc_labels(self):
        """Ad-hoc labels in routine mode are silently accepted."""
        migrator = LabelMigrator(
            clean_schema=_schema("task"),
            mapping={},
        )
        plan = migrator.plan(
            _actual("task", "some-adhoc-label"),
            routine=True,
        )
        # "some-adhoc-label" is not in clean schema but should NOT be
        # flagged as orphan in routine mode.
        assert plan.orphans == ()
        assert plan.renames == ()
        assert plan.merges == ()
        assert plan.adds == ()


# ── Integration: full cycle ──────────────────────────────────────────


class TestIntegration:
    def test_complex_migration_scenario(self):
        """Multiple behaviours in one plan."""
        migrator = LabelMigrator(
            clean_schema=_schema(
                "priority:high",
                "priority:medium",
                "task",
                "dependencies",
                "draft",
                "status:ready",
                "status:in-progress",
                "area:quality",
                "needs-research",
            ),
            mapping={
                "urgent": "priority:high",
                "chore": "task",
                "wip": "status:in-progress",
                "qa-flag": "area:quality",
                "test-quality": "area:quality",
            },
        )
        plan = migrator.plan(
            _actual(
                "urgent",
                "chore",
                "wip",
                "qa-flag",
                "test-quality",
                "status:ready",
                "ad-hoc-label",
            )
        )
        # 3 renames (urgent→high, chore→task, wip→in-progress)
        assert len(plan.renames) == 3
        rename_targets = {r.new_name for r in plan.renames}
        assert rename_targets == {"priority:high", "task", "status:in-progress"}

        # 1 merge (qa-flag + test-quality → area:quality)
        assert len(plan.merges) == 1
        assert plan.merges[0].target_name == "area:quality"
        assert sorted(plan.merges[0].source_names) == [
            "qa-flag",
            "test-quality",
        ]

        # Adds for clean labels not present and not targeted.
        added_names = {a.label_name for a in plan.adds}
        assert "priority:medium" in added_names
        assert "dependencies" in added_names
        assert "draft" in added_names
        assert "needs-research" in added_names
        # Already in actual:
        assert "status:ready" not in added_names
        # Targeted by rename/merge:
        assert "priority:high" not in added_names
        assert "task" not in added_names
        assert "status:in-progress" not in added_names
        assert "area:quality" not in added_names

        # 1 orphan
        assert len(plan.orphans) == 1
        assert plan.orphans[0].name == "ad-hoc-label"

    def test_already_converged_is_noop(self):
        """When actual matches clean schema exactly → empty plan."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "dependencies", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("task", "dependencies", "draft"))
        assert plan.has_actions is False

    def test_adds_only_when_nothing_to_migrate(self):
        """Plan with no mapping and partial actual → only adds."""
        migrator = LabelMigrator(
            clean_schema=_schema("task", "dependencies", "draft"),
            mapping={},
        )
        plan = migrator.plan(_actual("task"))
        assert plan.adds == (
            AddAction(label_name="dependencies"),
            AddAction(label_name="draft"),
        )
        assert plan.renames == ()
        assert plan.merges == ()
        assert plan.orphans == ()
