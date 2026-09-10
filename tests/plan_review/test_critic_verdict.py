"""Tests for agents.critic_verdict — critic verdict schema, fail-closed
resolution, and consensus (issue #1686 AC3, AC6, AC7, AC8).
"""

from __future__ import annotations

import pytest

from agents.critic_verdict import (
    InvalidVerdictError,
    consensus_reached,
    planner_actor,
    resolve_verdict,
    validate_objection,
    validate_verdict,
)


# AC6: verdict schema — objection carries either resolution or blocking+rationale


def test_validate_objection_with_resolution() -> None:
    obj = validate_objection({"description": "misses edge case", "resolution": "added a check"})
    assert obj.description == "misses edge case"
    assert obj.resolution == "added a check"
    assert obj.blocking is False
    assert obj.is_unresolved() is False


def test_validate_objection_blocking_requires_rationale() -> None:
    obj = validate_objection(
        {"description": "unsafe default", "blocking": True, "rationale": "could delete data"}
    )
    assert obj.blocking is True
    assert obj.rationale == "could delete data"
    assert obj.is_unresolved() is True


def test_validate_objection_blocking_without_rationale_raises() -> None:
    with pytest.raises(InvalidVerdictError):
        validate_objection({"description": "unsafe default", "blocking": True})


def test_validate_objection_neither_resolution_nor_blocking_raises() -> None:
    with pytest.raises(InvalidVerdictError):
        validate_objection({"description": "vague concern"})


def test_validate_verdict_builds_objections() -> None:
    verdict = validate_verdict(
        {
            "critic": "goal-fit",
            "objections": [
                {"description": "ok", "resolution": "fixed"},
                {"description": "bad", "blocking": True, "rationale": "why"},
            ],
        }
    )
    assert verdict.critic == "goal-fit"
    assert len(verdict.objections) == 2
    assert verdict.has_unresolved_blocking() is True


def test_validate_verdict_no_objections_has_no_unresolved_blocking() -> None:
    verdict = validate_verdict({"critic": "state-fit", "objections": []})
    assert verdict.has_unresolved_blocking() is False


def test_validate_verdict_missing_critic_raises() -> None:
    with pytest.raises(InvalidVerdictError):
        validate_verdict({"objections": []})


# AC7: absent or schema-invalid verdict, after exactly one re-run, is treated
# as an unresolved blocking objection — fail-closed, never fail-open.


def test_resolve_verdict_valid_passthrough() -> None:
    raw = {"critic": "goal-fit", "objections": []}
    verdict = resolve_verdict(raw, retried=False)
    assert verdict is not None
    assert verdict.has_unresolved_blocking() is False


def test_resolve_verdict_none_before_retry_returns_none() -> None:
    # Not yet retried — caller is expected to re-run once before we force-fail.
    assert resolve_verdict(None, retried=False) is None


def test_resolve_verdict_none_after_retry_forces_unresolved_blocking() -> None:
    verdict = resolve_verdict(None, retried=True)
    assert verdict is not None
    assert verdict.has_unresolved_blocking() is True


def test_resolve_verdict_invalid_after_retry_forces_unresolved_blocking() -> None:
    verdict = resolve_verdict({"objections": []}, retried=True)  # missing "critic"
    assert verdict is not None
    assert verdict.has_unresolved_blocking() is True


def test_resolve_verdict_invalid_before_retry_returns_none() -> None:
    assert resolve_verdict({"objections": []}, retried=False) is None


# AC8: consensus requires zero unresolved objections after <=1 revision cycle


def test_consensus_reached_when_no_unresolved_objections() -> None:
    verdicts = [
        validate_verdict({"critic": "goal-fit", "objections": []}),
        validate_verdict({"critic": "state-fit", "objections": []}),
    ]
    assert consensus_reached(verdicts, revisions=0) is True


def test_consensus_not_reached_with_unresolved_blocking() -> None:
    verdicts = [
        validate_verdict(
            {
                "critic": "goal-fit",
                "objections": [{"description": "x", "blocking": True, "rationale": "y"}],
            }
        ),
    ]
    assert consensus_reached(verdicts, revisions=0) is False


def test_consensus_not_reached_past_revision_cap() -> None:
    verdicts = [validate_verdict({"critic": "goal-fit", "objections": []})]
    assert consensus_reached(verdicts, revisions=2) is False


# AC3: planner actor stamping


def test_planner_actor_format() -> None:
    assert planner_actor("run-123") == "planner:run-123"
