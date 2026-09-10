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
    - STUCK_NO_CONVERGENCE: finding fingerprint set unchanged across two
      consecutive blocking attempts
    - STUCK_CONFLICT: same file:line touched in multiple attempts

The policy evaluates all guards independently; any single guard firing
triggers a STUCK verdict. Convergence is checked last — if no guard fires
and findings targets are met, the loop converged.

Findings model (code-gate #1816): each attempt's `findings` is a list of
{"class": str, "file": str} dicts — the same shape as Layer B's verdict
`findings` array (`{blocking, findings}`), no severity ladder, no line
number. `fingerprint(findings)` reduces that list to a frozenset of
"<class>|<file>" strings (deduped), mirroring the `finding_fingerprint`
computation in event-dispatch.yml's review-negative-claude-bot job.

No-convergence guard: fires when two consecutive attempts have the exact
same non-empty fingerprint set — the rework round touched the diff but did
not change *which* findings the reviewer would raise, so no real progress
was made. A shrinking, growing, or otherwise-different set is progress (or
regression caught by other guards) and does not fire this guard.

Convergence target (#1816, supersedes the #989 n_critical/n_major target):
findings == [] on the latest attempt. This mirrors the MERGE gate in
code-review.yml, which blocks on Layer B's `blocking: true` verdict. A PR is
"rework-done" only when no finding remains — there is no severity ladder to
partially satisfy.

Constants:
    MAX_ATTEMPTS_THRESHOLD = 3
    LOC_DELTA_THRESHOLD = 50 (percent)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ============================================================================
# Constants (per #634)
# ============================================================================

MAX_ATTEMPTS_THRESHOLD = 3
LOC_DELTA_THRESHOLD_PERCENT = 50


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


def _check_scope_creep(history: list[dict], initial_files: set[str]) -> tuple[bool, str]:
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


def fingerprint(findings: list[dict]) -> frozenset[str]:
    """Reduce a findings list to a deduped frozenset of "<class>|<file>" strings.

    Mirrors the `finding_fingerprint` computation in event-dispatch.yml's
    review-negative-claude-bot job (`jq -c '[.[] | "\\(.class)|\\(.file)"] | unique'`).
    """
    return frozenset(f"{item['class']}|{item['file']}" for item in findings)


def _check_no_convergence(history: list[dict]) -> tuple[bool, str]:
    """Guard: finding fingerprint set unchanged across two consecutive
    blocking attempts → stuck_no_convergence.

    A blocking attempt is one whose findings list is non-empty. If two
    consecutive blocking attempts have the exact same fingerprint set, the
    rework round made no measurable progress.

    Returns:
        (fired: bool, reason: str)
    """
    if len(history) < 2:
        return False, ""

    for i in range(1, len(history)):
        prev = history[i - 1]
        curr = history[i]

        prev_fp = fingerprint(prev.get("findings", []))
        curr_fp = fingerprint(curr.get("findings", []))

        if prev_fp and curr_fp and prev_fp == curr_fp:
            reason = (
                f"Finding fingerprint set unchanged: "
                f"attempt {prev.get('attempt', '?')} and "
                f"attempt {curr.get('attempt', '?')} both raised "
                f"{sorted(curr_fp)}"
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

    Convergence target (#1816): findings == [] on the latest attempt.

    Returns:
        True if the latest attempt has no findings.
    """
    if not history:
        return False

    latest = history[-1]
    return len(latest.get("findings", [])) == 0


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
                "findings": list[dict],  # [{"class": str, "file": str}, ...]
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
        reason = "Convergence target met: findings=[] on latest attempt"
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
