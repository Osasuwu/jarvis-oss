"""Table-driven tests for agents.plan_classifier.classify (issue #1685,
ordinal vocabulary per #1707).

Covers path-set x churn x area-count combinations including boundary
values, the exempt mechanical short-circuit, the class:3 true-HITL
precedence over class:2, config-driven threshold sensitivity (AC2), and
label_for()'s ordinal-to-board-label mapping.
"""

from __future__ import annotations

import pytest

from agents.plan_classifier import ChangeSet, classify, classify_task_row, label_for
from agents.plan_review_config import (
    Class2Thresholds,
    Class3Criteria,
    ExemptCriteria,
    ModelFloors,
    PlanReviewConfig,
)

_CFG = PlanReviewConfig(
    class_2=Class2Thresholds(
        shared_surface_globs=("mcp-memory/**", ".mcp.json"),
        churn_threshold=400,
        min_prod_areas=2,
    ),
    exempt=ExemptCriteria(mechanical_criteria=("docs-only", "typo-fix")),
    class_3=Class3Criteria(mechanical_criteria=("admin-rights-required", "physical-device-bound")),
    models=ModelFloors(planner="claude-opus-5", critic="claude-sonnet-5"),
)


@pytest.mark.parametrize(
    "paths,churn,prod_areas,mechanical,expected",
    [
        # default — nothing trips
        (("agents/foo.py",), 10, 1, (), 1),
        # shared-surface glob hit alone trips class 2
        (("mcp-memory/server.py",), 5, 1, (), 2),
        ((".mcp.json",), 1, 0, (), 2),
        # non-matching path does not trip on globs
        (("agents/bar.py",), 5, 1, (), 1),
        # churn boundary: exactly at threshold does NOT trip (strictly above)
        (("agents/foo.py",), 400, 1, (), 1),
        (("agents/foo.py",), 401, 1, (), 2),
        # prod-areas boundary: at-or-above min_prod_areas trips
        (("agents/foo.py",), 10, 1, (), 1),
        (("agents/foo.py",), 10, 2, (), 2),
        (("agents/foo.py",), 10, 3, (), 2),
        # exempt mechanical short-circuits even a shared-surface hit
        (("mcp-memory/server.py",), 500, 3, ("docs-only",), 1),
        (("agents/foo.py",), 5, 1, ("typo-fix",), 1),
        # true-HITL class_3 criterion wins even over an exempt-eligible path
        (("agents/foo.py",), 5, 1, ("admin-rights-required",), 3),
        # class_3 outranks class_2 even when a class_2 threshold also matches
        (("mcp-memory/server.py",), 500, 3, ("physical-device-bound",), 3),
        # unrecognized mechanical criterion does not short-circuit
        (("agents/foo.py",), 5, 1, ("not-a-real-criterion",), 1),
    ],
)
def test_classify_table(paths, churn, prod_areas, mechanical, expected) -> None:
    change = ChangeSet(
        paths=paths, churn_lines=churn, prod_areas=prod_areas, mechanical_criteria=mechanical
    )
    assert classify(_CFG, change) == expected


def test_exempt_takes_precedence_over_class_3_on_criterion_overlap() -> None:
    """If the same mechanical_criteria value is (invalidly) listed in both
    exempt and class_3, exempt wins — a change is never escalated to
    true-HITL by a criterion the config also calls no-review-required."""
    overlapping_cfg = PlanReviewConfig(
        class_2=_CFG.class_2,
        exempt=ExemptCriteria(mechanical_criteria=("docs-only", "ambiguous-criterion")),
        class_3=Class3Criteria(
            mechanical_criteria=("admin-rights-required", "ambiguous-criterion")
        ),
        models=_CFG.models,
    )
    change = ChangeSet(
        paths=("agents/foo.py",),
        churn_lines=5,
        prod_areas=1,
        mechanical_criteria=("ambiguous-criterion",),
    )
    assert classify(overlapping_cfg, change) == 1


def test_classifier_reads_thresholds_from_config_no_code_edit() -> None:
    """AC2: changing a threshold in config changes the verdict, no code edit."""
    change = ChangeSet(
        paths=("agents/foo.py",), churn_lines=50, prod_areas=1, mechanical_criteria=()
    )
    assert classify(_CFG, change) == 1

    lowered = PlanReviewConfig(
        class_2=Class2Thresholds(
            shared_surface_globs=_CFG.class_2.shared_surface_globs,
            churn_threshold=10,
            min_prod_areas=_CFG.class_2.min_prod_areas,
        ),
        exempt=_CFG.exempt,
        class_3=_CFG.class_3,
        models=_CFG.models,
    )
    assert classify(lowered, change) == 2


def test_classify_task_row_is_the_named_policy_entry_point() -> None:
    """AC7: one named bundle every consumer calls, not ad-hoc re-derived
    conditions per caller. classify_task_row() reads a task_queue-shaped
    dict (``scope_files`` per the repo's existing convention) instead of
    each of the four consumers (interactive lane, drain, container pick,
    CI diff-gate) hand-rolling ChangeSet construction."""
    row = {
        "scope_files": ["mcp-memory/server.py"],
        "churn_lines": 5,
        "prod_areas": 1,
        "mechanical_criteria": [],
    }
    assert classify_task_row(_CFG, row) == 2


def test_classify_task_row_defaults_missing_fields() -> None:
    """A row missing optional fields defaults to the least-alarming values
    rather than raising — matches ``row.get(...)`` conventions elsewhere
    in agents/ (escalation.py, task_queue.py)."""
    assert classify_task_row(_CFG, {}) == 1


@pytest.mark.parametrize(
    "cls,expected_label",
    [
        (1, None),
        (2, "afk:2-plan"),
        (3, "afk:3-human"),
    ],
)
def test_label_for(cls, expected_label) -> None:
    assert label_for(cls) == expected_label


# --- docs-only derivation (#1818) -------------------------------------------
#
# `mechanical_criteria` is populated by no production caller — neither the CI
# diff-gate's jq envelope nor `/implement` §3b passes one — so the exempt
# short-circuit above was dead code in prod and a docs-only PR spanning two
# top-level areas got hard-blocked (PR #1817). classify() now derives the
# criterion from paths when, and only when, the caller supplied none.

_DOCS_CHANGE = ("CONTEXT.md", "docs/research/x.md")


def test_docs_only_is_derived_when_caller_supplies_no_criteria() -> None:
    """The PR #1817 shape: root markdown + a docs/ file = two prod areas,
    which trips min_prod_areas — but it is documentation, so it exempts."""
    change = ChangeSet(paths=_DOCS_CHANGE, churn_lines=5, prod_areas=2, mechanical_criteria=())
    assert classify(_CFG, change) == 1


@pytest.mark.parametrize(
    "paths",
    [
        # skills/hooks markdown is agent behavior, not documentation about it
        (".claude/skills/foo/SKILL.md", "docs/x.md"),
        # rules files at the root are executable process, not documentation
        ("AGENTS.md", "docs/x.md"),
        ("CLAUDE.md", "docs/x.md"),
        # a code file anywhere in the set defeats the derivation
        ("agents/foo.py", "docs/x.md"),
    ],
)
def test_non_documentation_paths_defeat_the_derivation(paths) -> None:
    change = ChangeSet(paths=paths, churn_lines=5, prod_areas=2, mechanical_criteria=())
    assert classify(_CFG, change) == 2


def test_explicit_criteria_win_over_derivation() -> None:
    """A caller that names its own criteria is never second-guessed — in
    particular a true-HITL class_3 criterion is not downgraded by paths that
    happen to look like documentation."""
    change = ChangeSet(
        paths=_DOCS_CHANGE,
        churn_lines=5,
        prod_areas=2,
        mechanical_criteria=("admin-rights-required",),
    )
    assert classify(_CFG, change) == 3


def test_derivation_has_no_authority_of_its_own() -> None:
    """The derivation only names a criterion; whether it exempts is still the
    config's call. Drop `docs-only` from exempt and the same change classifies
    on thresholds like anything else."""
    no_docs_exempt = PlanReviewConfig(
        class_2=_CFG.class_2,
        exempt=ExemptCriteria(mechanical_criteria=("typo-fix",)),
        class_3=_CFG.class_3,
        models=_CFG.models,
    )
    change = ChangeSet(paths=_DOCS_CHANGE, churn_lines=5, prod_areas=2, mechanical_criteria=())
    assert classify(no_docs_exempt, change) == 2


def test_empty_path_set_derives_nothing() -> None:
    """`all()` over an empty set is vacuously true — an empty change must not
    be read as documentation."""
    change = ChangeSet(paths=(), churn_lines=500, prod_areas=0, mechanical_criteria=())
    assert classify(_CFG, change) == 2
