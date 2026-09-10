"""Tests for agents.implement_plan_gate — the interactive `/implement` lane's
ex-ante plan-gate trigger evaluation (#1688; ordinal vocabulary per #1707).

Reuses agents.plan_classifier.classify_task_row (the one named policy entry
point every consumer calls, per its own docstring) — no second
implementation of thresholds/classification here (AC1, AC6).
"""

from __future__ import annotations

from agents.implement_plan_gate import PRIORITY_CRITICAL_LABEL, evaluate_trigger
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
    class_3=Class3Criteria(mechanical_criteria=("admin-rights-required",)),
    models=ModelFloors(planner="claude-opus-5", critic="claude-sonnet-5"),
)


# ── AC1: trigger evaluation uses the shared classifier ──────────────────────


def test_class_1_change_does_not_require_a_plan():
    result = evaluate_trigger(_CFG, paths=("agents/foo.py",))
    assert result.classification == 1
    assert result.requires_plan is False


def test_shared_surface_path_trips_class_2_and_requires_plan():
    result = evaluate_trigger(_CFG, paths=("mcp-memory/server.py",))
    assert result.classification == 2
    assert result.requires_plan is True


def test_config_driven_no_hardcoded_threshold():
    """AC6 in spirit: a config change (not a code change) flips the verdict —
    proves this lane reads agents.plan_classifier's live config, not a copy."""
    lowered = PlanReviewConfig(
        class_2=Class2Thresholds(
            shared_surface_globs=_CFG.class_2.shared_surface_globs,
            churn_threshold=5,
            min_prod_areas=_CFG.class_2.min_prod_areas,
        ),
        exempt=_CFG.exempt,
        class_3=_CFG.class_3,
        models=_CFG.models,
    )
    result = evaluate_trigger(lowered, paths=("agents/foo.py",), churn_lines=10)
    assert result.classification == 2
    assert result.requires_plan is True


# ── AC3: priority:critical carve-out skips only the plan requirement ────────


def test_priority_critical_skips_plan_requirement_on_class_2():
    result = evaluate_trigger(
        _CFG, paths=("mcp-memory/server.py",), labels=(PRIORITY_CRITICAL_LABEL,)
    )
    assert result.classification == 2, "carve-out must not mask the real classification"
    assert result.carve_out is True
    assert result.requires_plan is False


def test_priority_critical_is_a_noop_on_class_1():
    result = evaluate_trigger(_CFG, paths=("agents/foo.py",), labels=(PRIORITY_CRITICAL_LABEL,))
    assert result.classification == 1
    assert result.carve_out is True
    assert result.requires_plan is False


# ── AC4: class-1 issues see no behavior change ───────────────────────────────


def test_class_1_never_carries_carve_out_when_no_critical_label():
    result = evaluate_trigger(_CFG, paths=("agents/foo.py",))
    assert result.carve_out is False
    assert result.requires_plan is False


# ── #1707: class 3 (true-HITL) never gets requires_plan silently rewritten ──


def test_class_3_change_does_not_require_plan_gate_either():
    """A true-HITL class-3 change is not a class-2 plan-gate case — it
    routes to the human, not the planner. requires_plan stays False."""
    result = evaluate_trigger(
        _CFG, paths=("agents/foo.py",), mechanical_criteria=("admin-rights-required",)
    )
    assert result.classification == 3
    assert result.requires_plan is False
