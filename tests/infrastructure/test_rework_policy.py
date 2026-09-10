"""Tests for rework loop-stop policy decision module (#634, fingerprint model #1816).

Verifies that the pure-function policy module correctly decides whether
a PR rework loop should continue, has converged, or has terminated due to
one of four guard conditions:
  - max_attempts: ≥3 attempts
  - scope_creep: LOC delta >50% OR files outside initial PR diff
  - no_convergence: finding fingerprint set unchanged across two blocking attempts
  - conflict: same file:line touched in multiple attempts

Findings model (#1816): each attempt now carries a `findings` list of
{"class": str, "file": str} dicts — the same shape emitted by the code-gate's
Layer B verdict — instead of `n_critical`/`n_major` counts. Convergence is
"findings == []" (mirrors the merge gate's `blocking=false`); the
no-convergence guard fires when the *set* of finding fingerprints
("<class>|<file>", deduped) is identical between two consecutive blocking
attempts — the rework round made no measurable progress, not just "the
count didn't drop."
"""

from __future__ import annotations

from rework_policy import (
    LoopDecision,
    decide,
    fingerprint,
)


# -- Test data helpers -------------------------------------------------------


def make_attempt(
    attempt_num: int,
    findings: list[dict] | None = None,
    files_touched: set[str] | None = None,
    loc_delta: int | None = None,
    conflicts: dict[str, set[int]] | None = None,
) -> dict:
    """Helper to construct an attempt record."""
    return {
        "attempt": attempt_num,
        "findings": findings if findings is not None else [],
        "files_touched": files_touched or set(),
        "loc_delta": loc_delta or 0,
        "conflicts": conflicts or {},  # {file: {line_nums}}
    }


def f(cls: str, file: str) -> dict:
    """Shorthand for a single finding dict."""
    return {"class": cls, "file": file}


# ============================================================================
# AC Tests: Max Attempts Guard
# ============================================================================


class TestMaxAttemptsGuard:
    """max_attempts: ≥3 attempts → stuck_attempts."""

    def test_attempt_3_no_convergence_returns_stuck_attempts(self):
        """AC: attempt 3, still blocking with new findings each time → stuck_attempts."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
            make_attempt(3, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=3,
            history=history,
            initial_files={"a.py", "b.py", "c.py"},
        )
        assert result.decision == LoopDecision.STUCK_ATTEMPTS

    def test_attempt_2_within_attempts_continues(self):
        """AC: history with shrinking, changing findings at attempt 2 → continue."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py", "c.py"},
        )
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# AC Tests: Scope Creep Guard
# ============================================================================


class TestScopeCreepGuard:
    """scope_creep: LOC delta >50% OR files outside initial diff → stuck_scope."""

    def test_loc_delta_51_percent_triggers_scope_creep(self):
        """AC: attempt 2, LOC delta = 51% of original → stuck_scope."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], loc_delta=100),
            make_attempt(2, findings=[f("concurrency", "c.py")], loc_delta=151),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.STUCK_SCOPE

    def test_loc_delta_exactly_50_percent_is_safe(self):
        """Edge: LOC delta exactly 50% should NOT trigger scope_creep."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], loc_delta=100),
            make_attempt(2, findings=[f("concurrency", "c.py")], loc_delta=150),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONTINUE

    def test_file_outside_initial_diff_triggers_scope_creep(self):
        """AC: attempt 2, files touched include one outside initial diff → stuck_scope."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], files_touched={"a.py", "b.py"}),
            make_attempt(2, findings=[f("concurrency", "c.py")], files_touched={"a.py", "c.py"}),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.STUCK_SCOPE

    def test_all_files_within_initial_diff_is_safe(self):
        """All files in attempt stay within initial diff → scope OK."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], files_touched={"a.py", "b.py"}),
            make_attempt(2, findings=[f("concurrency", "a.py")], files_touched={"a.py"}),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# AC Tests: No Convergence Guard
# ============================================================================


class TestNoConvergenceGuard:
    """no_convergence: finding fingerprint set unchanged across two blocking
    attempts → stuck_no_convergence."""

    def test_identical_fingerprint_set_triggers_no_convergence(self):
        """AC: attempt 2 has the exact same {class,file} set as attempt 1 → stuck."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.STUCK_NO_CONVERGENCE

    def test_identical_set_different_order_still_triggers(self):
        """Order-insensitive: same set in different list order still stuck."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("exception-handling", "b.py"), f("regression", "a.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.STUCK_NO_CONVERGENCE

    def test_shrinking_to_empty_converges(self):
        """A clean descent to empty findings converges."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
            make_attempt(3, findings=[]),
        ]
        result = decide(
            attempts=3,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONVERGED

    def test_changed_fingerprint_set_is_progress(self):
        """A different (even same-size) fingerprint set is progress, not stuck."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")]),
            make_attempt(2, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "c.py"},
        )
        assert result.decision == LoopDecision.CONTINUE

    def test_partial_overlap_is_progress(self):
        """A set that shrank (subset of previous) counts as progress."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# AC Tests: Conflict Guard
# ============================================================================


class TestConflictGuard:
    """conflict: same file:line touched in 2 different attempts → stuck_conflict."""

    def test_same_file_line_in_two_attempts_triggers_conflict(self):
        """AC: file.py:42 touched in attempts 1 and 2 → stuck_conflict."""
        history = [
            make_attempt(
                1,
                findings=[f("regression", "file.py")],
                conflicts={"file.py": {42}},
            ),
            make_attempt(
                2,
                findings=[f("concurrency", "file.py")],
                conflicts={"file.py": {42}},
            ),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"file.py"},
        )
        assert result.decision == LoopDecision.STUCK_CONFLICT

    def test_different_lines_in_same_file_is_safe(self):
        """Same file, different lines → no conflict."""
        history = [
            make_attempt(
                1,
                findings=[f("regression", "file.py")],
                conflicts={"file.py": {42}},
            ),
            make_attempt(
                2,
                findings=[f("concurrency", "file.py")],
                conflicts={"file.py": {43}},
            ),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"file.py"},
        )
        assert result.decision == LoopDecision.CONTINUE

    def test_different_files_is_safe(self):
        """Different files → no conflict."""
        history = [
            make_attempt(
                1,
                findings=[f("regression", "a.py")],
                conflicts={"a.py": {42}},
            ),
            make_attempt(
                2,
                findings=[f("concurrency", "b.py")],
                conflicts={"b.py": {42}},
            ),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# AC Tests: Convergence
# ============================================================================


class TestConvergence:
    """Convergence target (#1816): findings == [] on the latest attempt.

    This matches the MERGE gate's `blocking=false` — a PR is "rework-done"
    only when Layer B's verdict carries zero findings.
    """

    def test_convergence_at_attempt_3(self):
        """Descent to empty findings by attempt 3 → converged."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
            make_attempt(3, findings=[]),
        ]
        result = decide(
            attempts=3,
            history=history,
            initial_files={"a.py"},
        )
        assert result.decision == LoopDecision.CONVERGED

    def test_convergence_at_attempt_2(self):
        """Hitting empty findings at attempt 2 → converged."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")]),
            make_attempt(2, findings=[]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py"},
        )
        assert result.decision == LoopDecision.CONVERGED

    def test_not_converged_with_finding_remaining(self):
        """Any remaining finding means not converged."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("design-modularity", "c.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py", "c.py"},
        )
        assert result.decision == LoopDecision.CONTINUE

    def test_single_remaining_finding_does_not_converge(self):
        """Even one finding blocks convergence (matches the merge gate)."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision != LoopDecision.CONVERGED
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# Edge Tests: Boundaries
# ============================================================================


class TestBoundaryConditions:
    """Edge cases: exact boundaries for guards."""

    def test_attempt_exactly_3_is_stuck(self):
        """Edge: attempt exactly = 3 → stuck (not ≥ 4)."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
            make_attempt(3, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=3,
            history=history,
            initial_files={"a.py"},
        )
        assert result.decision == LoopDecision.STUCK_ATTEMPTS

    def test_attempt_2_is_allowed(self):
        """Edge: attempt exactly = 2 → still allowed (not yet stuck)."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py"},
        )
        # Will continue unless another guard fires
        assert result.decision in (LoopDecision.CONTINUE, LoopDecision.CONVERGED)

    def test_loc_delta_exactly_50_is_boundary(self):
        """Edge: LOC delta exactly 50% → allowed (> not >=)."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], loc_delta=100),
            make_attempt(2, findings=[f("concurrency", "c.py")], loc_delta=150),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py"},
        )
        assert result.decision != LoopDecision.STUCK_SCOPE

    def test_empty_findings_on_first_attempt_converges(self):
        """Edge: even a single attempt with empty findings converges immediately."""
        history = [
            make_attempt(1, findings=[]),
        ]
        result = decide(
            attempts=1,
            history=history,
            initial_files={"a.py"},
        )
        assert result.decision == LoopDecision.CONVERGED


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Multi-guard scenarios."""

    def test_multiple_guards_could_fire_first_one_wins(self):
        """If multiple guards would fire, which takes precedence?

        Current design: guards are independent, any one firing terminates.
        We assert the actual result, not a precedence order.
        """
        # Scope creep + no convergence (identical fingerprint set) both present
        history = [
            make_attempt(1, findings=[f("regression", "a.py")], loc_delta=100),
            make_attempt(2, findings=[f("regression", "a.py")], loc_delta=151),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py"},
        )
        # Should hit one of the stuck guards
        assert result.decision in (
            LoopDecision.STUCK_SCOPE,
            LoopDecision.STUCK_NO_CONVERGENCE,
        )

    def test_happy_path_attempt_2_converging(self):
        """Happy path: attempt 2 reaches empty findings → converged."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py"},
        )
        assert result.decision == LoopDecision.CONVERGED

    def test_happy_path_attempt_2_still_improving(self):
        """Happy path: attempt 2, still making progress (different fingerprint set)."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=2,
            history=history,
            initial_files={"a.py", "b.py", "c.py"},
        )
        assert result.decision == LoopDecision.CONTINUE


# ============================================================================
# Fingerprint helper
# ============================================================================


class TestFingerprint:
    """Verify the fingerprint() helper matches the event-dispatch convention."""

    def test_fingerprint_is_class_pipe_file_strings(self):
        result = fingerprint([f("regression", "a.py"), f("concurrency", "b.py")])
        assert result == frozenset({"regression|a.py", "concurrency|b.py"})

    def test_fingerprint_dedupes(self):
        result = fingerprint([f("regression", "a.py"), f("regression", "a.py")])
        assert result == frozenset({"regression|a.py"})

    def test_fingerprint_empty_list(self):
        assert fingerprint([]) == frozenset()


# ============================================================================
# Return Value Structure
# ============================================================================


class TestReturnStructure:
    """Verify the return value has all required fields."""

    def test_result_has_decision_field(self):
        """Result object must have .decision field."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py")]),
        ]
        result = decide(
            attempts=1,
            history=history,
            initial_files={"a.py"},
        )
        assert hasattr(result, "decision")
        assert isinstance(result.decision, LoopDecision)

    def test_result_has_reason_field(self):
        """Result object must have .reason field for debugging."""
        history = [
            make_attempt(1, findings=[f("regression", "a.py"), f("exception-handling", "b.py")]),
            make_attempt(2, findings=[f("regression", "a.py")]),
            make_attempt(3, findings=[f("concurrency", "c.py")]),
        ]
        result = decide(
            attempts=3,
            history=history,
            initial_files={"a.py"},
        )
        assert hasattr(result, "reason")
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0
