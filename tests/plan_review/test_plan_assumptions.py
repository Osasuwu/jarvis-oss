"""Tests for agents.plan_assumptions — the checkable-predicate assumption
lens for plan steps (issue #1686 AC4).

Convention (new to this issue — agents/plan_lock.py's ``## Plan`` grammar
has no assumptions concept): a step line prefixed ``Assumption:`` declares
an assumption. The lens checks each declared assumption reads as a
checkable predicate ("X exists", "Y returns Z") rather than an unverifiable
belief ("I think", "probably", "should be fine").
"""

from __future__ import annotations

from agents.plan_assumptions import (
    extract_assumptions,
    is_checkable_predicate,
    validate_plan_assumptions,
)


def test_extract_assumptions_picks_prefixed_steps() -> None:
    steps = (
        "Add a new column to the users table",
        "Assumption: the users table has fewer than 1M rows",
        "Run the migration",
        "assumption: the migration runner supports dry-run mode",
    )
    assumptions = extract_assumptions(steps)
    assert assumptions == (
        "the users table has fewer than 1M rows",
        "the migration runner supports dry-run mode",
    )


def test_extract_assumptions_empty_when_none_declared() -> None:
    steps = ("Add a column", "Run the migration")
    assert extract_assumptions(steps) == ()


def test_is_checkable_predicate_true_for_verifiable_claim() -> None:
    assert is_checkable_predicate("the users table has fewer than 1M rows") is True
    assert is_checkable_predicate("config/plan_review.yaml exists") is True
    assert is_checkable_predicate("the API returns a 200 on success") is True


def test_is_checkable_predicate_false_for_prose_belief() -> None:
    assert is_checkable_predicate("I think this should be fine") is False
    assert is_checkable_predicate("probably safe to skip validation") is False
    assert is_checkable_predicate("this seems like the right approach") is False


def test_validate_plan_assumptions_returns_failing_subset() -> None:
    assumptions = (
        "the users table has fewer than 1M rows",
        "probably fine either way",
    )
    failing = validate_plan_assumptions(assumptions)
    assert failing == ("probably fine either way",)


def test_validate_plan_assumptions_empty_when_all_checkable() -> None:
    assumptions = ("the users table has fewer than 1M rows",)
    assert validate_plan_assumptions(assumptions) == ()
