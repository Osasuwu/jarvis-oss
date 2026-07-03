"""Pure-function policy module for /rework loop-stop decision (#634).

Decides whether a PR rework loop should continue, has converged, or has
terminated due to one of four guard conditions. Zero side effects, zero I/O.

The interface is single:
    decide(attempts, history, initial_files) →
        PolicyResult(decision, reason)

where `decision` is one of:
    - CONTINUE: loop should continue to next attempt
    - CONVERGED: findings targets met, loop may terminate successfully
    - STUCK_ATTEMPTS: ≥3 attempts reached without convergence
    - STUCK_SCOPE: LOC delta >50% or files outside initial diff
    - STUCK_NO_CONVERGENCE: critical+major not strictly decreasing
    - STUCK_CONFLICT: same file:line touched in multiple attempts

The policy evaluates all guards independently; any single guard firing
triggers a STUCK verdict. Convergence is checked last — if no guard fires
and findings targets are met, the loop converged.

Strictly decreasing: (n_critical, n_major) lex-decreases per attempt.
Specifically: comparing attempt t to attempt t-1, both components must
strictly decrease: (n_critical_t < n_critical_{t-1}) AND
(n_major_t < n_major_{t-1}).

Convergence target (two-gate model, #989): n_critical == 0 AND n_major == 0.
This mirrors the MERGE gate in code-review.yml, which blocks on any
CRITICAL/MAJOR/BLOCKING severity heading. A PR is "rework-done" only when no
merge-blocking finding remains. MINOR findings never gate either side — they
are swept best-effort while /rework is already in context for a bug-triggered
round, never as a convergence requirement (was n_major <= 2 before #989, which
let a PR self-declare done while the merge gate still rejected its majors —
the #976 ping-pong).

Constants (per #634, target revised #989):
    MAX_ATTEMPTS_THRESHOLD = 3
    LOC_DELTA_THRESHOLD = 50 (percent)
    TARGET_CRITICAL = 0
    TARGET_MAJOR = 0
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ============================================================================
# Constants (per #634)
# ============================================================================

MAX_ATTEMPTS_THRESHOLD = 3
LOC_DELTA_THRESHOLD_PERCENT = 50
TARGET_CRITICAL = 0
# Two-gate model (#989): convergence requires ZERO majors, matching the merge
# gate's CRITICAL/MAJOR/BLOCKING block. Was MAX_MAJOR_FINDINGS = 2.
TARGET_MAJOR = 0


# ============================================================================
# Return types
# ============================================================================


class LoopDecision(str, Enum):
    """Loop verdict enum."""

    CONTINUE = "continue"
    CONVERGED = "converged"
    STUCK_ATTEMPTS = "stuck_attempts"
    STUCK_SCOPE = "stuck_scope"
    STUCK_NO_CONVERGENCE = "stuck_no_convergence"
    STUCK_CONFLICT = "stuck_conflict"


@dataclass
class PolicyResult:
    """Result of the policy decision.

    Attributes:
        decision: One of LoopDecision enum values.
        reason: Human-readable explanation of the verdict.
    """

    decision: LoopDecision
    reason: str


# ============================================================================
# Guards (independent checks)
# ============================================================================


def _check_max_attempts(attempts: int) -> tuple[bool, str]:
    """Guard: ≥3 attempts → stuck_attempts.

    Returns:
        (fired: bool, reason: str)
    """
    if attempts >= MAX_ATTEMPTS_THRESHOLD:
        reason = f"Reached maximum attempts threshold ({MAX_ATTEMPTS_THRESHOLD})"
        return True, reason
    return False, ""


def _check_scope_creep(
    history: list[dict], initial_files: set[str]
) -> tuple[bool, str]:
    """Guard: LOC delta >50% OR files outside initial diff → stuck_scope.

    LOC delta is measured as a percentage of the first attempt's LOC count.
    Files must stay within the union of files touched in the initial diff.

    Returns:
        (fired: bool, reason: str)
    """
    if not history:
        return False, ""

    first_attempt = history[0]
    initial_loc = first_attempt.get("loc_delta", 0)

    for attempt in history[1:]:
        # Check LOC delta threshold
        current_loc = attempt.get("loc_delta", 0)
        if initial_loc > 0:
            loc_delta_percent = ((current_loc - initial_loc) / initial_loc) * 100
            if loc_delta_percent > LOC_DELTA_THRESHOLD_PERCENT:
                reason = (
                    f"LOC delta {loc_delta_percent:.1f}% exceeds threshold "
                    f"({LOC_DELTA_THRESHOLD_PERCENT}%)"
                )
                return True, reason

        # Check files stay within initial diff
        files_touched = attempt.get("files_touched", set())
        outside_files = files_touched - initial_files
        if outside_files:
            reason = (
                f"Attempt {attempt.get('attempt', '?')} touched files "
                f"outside initial diff: {', '.join(sorted(outside_files))}"
            )
            return True, reason

    return False, ""


def _check_no_convergence(history: list[dict]) -> tuple[bool, str]:
    """Guard: n_critical + n_major not strictly decreasing → stuck_no_convergence.

    Strictly decreasing: (n_critical, n_major) as a lexicographic pair
    must have both components strictly decrease between consecutive attempts.

    Returns:
        (fired: bool, reason: str)
    """
    if len(history) < 2:
        return False, ""

    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]

        prev_critical = prev.get("n_critical", 0)
        prev_major = prev.get("n_major", 0)
        curr_critical = curr.get("n_critical", 0)
        curr_major = curr.get("n_major", 0)

        # Both must strictly decrease
        if not (curr_critical < prev_critical and curr_major < prev_major):
            reason = (
                f"Findings not strictly decreasing: "
                f"attempt {prev.get('attempt', '?')} ({prev_critical}c, {prev_major}m) → "
                f"attempt {curr.get('attempt', '?')} ({curr_critical}c, {curr_major}m)"
            )
            return True, reason

    return False, ""


def _check_conflict(history: list[dict]) -> tuple[bool, str]:
    """Guard: same file:line touched in 2 different attempts → stuck_conflict.

    Maintains a map of (file, line) pairs seen in previous attempts.
    If any pair appears in multiple attempts, guard fires.

    Returns:
        (fired: bool, reason: str)
    """
    seen_locations: dict[tuple[str, int], int] = {}  # (file, line) -> attempt_num

    for attempt in history:
        attempt_num = attempt.get("attempt", 0)
        conflicts = attempt.get("conflicts", {})

        for file, lines in conflicts.items():
            for line in lines:
                location = (file, line)
                if location in seen_locations:
                    prev_attempt = seen_locations[location]
                    reason = (
                        f"Location {file}:{line} touched in both "
                        f"attempt {prev_attempt} and attempt {attempt_num}"
                    )
                    return True, reason
                seen_locations[location] = attempt_num

    return False, ""


# ============================================================================
# Convergence check
# ============================================================================


def _check_convergence(history: list[dict]) -> bool:
    """Check if convergence target is met.

    Convergence target (two-gate, #989): n_critical == 0 AND n_major == 0.

    Returns:
        True if the latest attempt meets both targets.
    """
    if not history:
        return False

    latest = history[-1]
    n_critical = latest.get("n_critical", 0)
    n_major = latest.get("n_major", 0)

    return n_critical == TARGET_CRITICAL and n_major == TARGET_MAJOR


# ============================================================================
# Main decision function
# ============================================================================


def decide(
    attempts: int,
    history: list[dict],
    initial_files: set[str],
) -> PolicyResult:
    """Decide whether the rework loop should continue, converge, or get stuck.

    Args:
        attempts: Current attempt number (1-indexed).
        history: List of attempt records. Each record should have:
            {
                "attempt": int,
                "n_critical": int,
                "n_major": int,
                "files_touched": set[str],
                "loc_delta": int,
                "conflicts": dict[str, set[int]],  # {file: {line_nums}}
            }
        initial_files: Set of file paths from the initial PR diff.

    Returns:
        PolicyResult with decision and reason.

    Notes:
        - Guards are evaluated in order; the first to fire determines the verdict.
        - Convergence is checked first: if converged, return immediately.
        - Then remaining guards are checked.
        - The policy module is side-effect-free; callers handle GH/memory writes.
    """
    # Check convergence first — convergence takes precedence
    if _check_convergence(history):
        reason = (
            f"Convergence target met: "
            f"n_critical={history[-1].get('n_critical', 0)} (target={TARGET_CRITICAL}), "
            f"n_major={history[-1].get('n_major', 0)} (target={TARGET_MAJOR})"
        )
        return PolicyResult(decision=LoopDecision.CONVERGED, reason=reason)

    # Check guards in order (after convergence check).
    # Order: more specific reasons before generic limits, so that we report
    # the actual reason a loop is stuck rather than just the attempt count.
    guards = [
        ("scope", _check_scope_creep(history, initial_files)),
        ("convergence", _check_no_convergence(history)),
        ("conflict", _check_conflict(history)),
        ("attempts", _check_max_attempts(attempts)),
    ]

    for guard_name, (fired, reason) in guards:
        if fired:
            if guard_name == "attempts":
                decision = LoopDecision.STUCK_ATTEMPTS
            elif guard_name == "scope":
                decision = LoopDecision.STUCK_SCOPE
            elif guard_name == "convergence":
                decision = LoopDecision.STUCK_NO_CONVERGENCE
            elif guard_name == "conflict":
                decision = LoopDecision.STUCK_CONFLICT
            else:
                decision = LoopDecision.CONTINUE  # Should not reach
            return PolicyResult(decision=decision, reason=reason)

    # No guard fired and not converged yet
    reason = "No guard fired; loop may continue to next attempt"
    return PolicyResult(decision=LoopDecision.CONTINUE, reason=reason)
