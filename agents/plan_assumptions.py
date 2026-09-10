"""Checkable-predicate assumption lens for plan steps (issue #1686 AC4).

Convention (new to this issue — ``agents/plan_lock.py``'s ``## Plan`` grammar
has no assumptions concept): a step line prefixed ``Assumption:`` declares an
assumption. This module extracts declared assumptions and checks each one
reads as a checkable predicate ("X exists", "Y returns Z") rather than an
unverifiable prose belief ("I think", "probably", "should be fine").
"""

from __future__ import annotations

import re

_ASSUMPTION_PREFIX_RE = re.compile(r"^assumption:\s*(.+)$", re.IGNORECASE)

_PROSE_BELIEF_MARKERS = (
    "i think",
    "i believe",
    "probably",
    "maybe",
    "should be fine",
    "seems like",
    "seems to",
    "hopefully",
    "presumably",
)


def extract_assumptions(steps: tuple[str, ...]) -> tuple[str, ...]:
    """Pull the predicate text out of every ``Assumption:``-prefixed step."""
    assumptions = []
    for step in steps:
        match = _ASSUMPTION_PREFIX_RE.match(step.strip())
        if match:
            assumptions.append(match.group(1).strip())
    return tuple(assumptions)


def is_checkable_predicate(assumption_text: str) -> bool:
    """True if the assumption reads as a verifiable claim, not a belief."""
    lowered = assumption_text.strip().lower()
    return not any(marker in lowered for marker in _PROSE_BELIEF_MARKERS)


def validate_plan_assumptions(assumptions: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of assumptions that fail the checkable-predicate lens."""
    return tuple(a for a in assumptions if not is_checkable_predicate(a))
