"""GO-gate scorer: pure function computing Phase-1 → Phase-2 gate scores.

Given a window of proposal dispositions and owner quality-ratings, produce
the four dimension scores that determine whether the strategic-ideation
lane has reached consumption-readiness.

This module has zero side effects — no DB, no I/O, no imports outside the
stdlib. Designed to be called from any context (orchestrator, test, CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ── Input types ──────────────────────────────────────────────────────────


class ProposalDisposition:
    """Canonical disposition verdicts for a surfaced strategic proposal."""

    SURFACED = "surfaced"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DEFERRED = "deferred"

    _ALL = {SURFACED, ACCEPTED, REJECTED, EXPIRED, DEFERRED}


@dataclass(frozen=True)
class DispositionRecord:
    """A single proposal's disposition within the validation window."""

    proposal_id: str
    status: str  # one of ProposalDisposition values
    surfaced_at: datetime
    dispositioned_at: datetime | None = None


@dataclass(frozen=True)
class QualityRating:
    """Owner's value-if-applied rating for a proposal (independent of acceptance)."""

    proposal_id: str
    score: float  # value-if-applied rating
    rated_at: datetime


# ── Output type ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoGateScores:
    """Four-dimension GO-gate output.

    All dimensions are normalised to [0, 1] for cross-dimension comparison
    at the decision point.  The single exception is *applied_substance*,
    which Phase 1 deliberately keeps as a raw count so the owner can see
    *how many* north-star-linked proposals were acted on, not just the rate.
    """

    acceptance: float
    applied_substance: int
    engagement_presence: float
    proposal_quality: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "acceptance": self.acceptance,
            "applied_substance": self.applied_substance,
            "engagement_presence": self.engagement_presence,
            "proposal_quality": self.proposal_quality,
        }


# ── Public API ───────────────────────────────────────────────────────────


EMPTY_SCORES = GoGateScores(
    acceptance=0.0,
    applied_substance=0,
    engagement_presence=0.0,
    proposal_quality=0.0,
)


def compute_go_gate(
    dispositions: list[DispositionRecord],
    applied_proposal_ids: set[str] | None = None,
    quality_ratings: list[QualityRating] | None = None,
) -> GoGateScores:
    """Compute the four GO-gate dimension scores for a validation window.

    Parameters
    ----------
    dispositions:
        All proposals that were surfaced in the window, with their final
        disposition status.
    applied_proposal_ids:
        Proposal IDs the owner confirmed as *north-star-linked and applied*
        (Phase 1: manual confirmation, not auto-apply).
    quality_ratings:
        Owner value-if-applied ratings collected over the window.

    Returns
    -------
    GoGateScores with all four dimensions populated.
    """
    if not dispositions:
        return EMPTY_SCORES

    applied = applied_proposal_ids or set()
    ratings = quality_ratings or []

    # How many proposals were surfaced in the window?
    surfaced_count = len(dispositions)

    # --- acceptance: accepted / surfaced ---
    accepted_count = sum(
        1 for d in dispositions if d.status == ProposalDisposition.ACCEPTED
    )
    acceptance = accepted_count / surfaced_count if surfaced_count > 0 else 0.0

    # --- applied-substance: raw count of north-star-linked applied proposals ---
    applied_substance = len(applied)

    # --- engagement-presence: proposals that received any disposition / surfaced ---
    dispositioned = [
        d
        for d in dispositions
        if d.status
        in (
            ProposalDisposition.ACCEPTED,
            ProposalDisposition.REJECTED,
            ProposalDisposition.DEFERRED,
        )
        and d.dispositioned_at is not None
    ]
    engagement_presence = len(dispositioned) / surfaced_count if surfaced_count > 0 else 0.0

    # --- proposal-quality: mean of owner value-if-applied ratings ---
    proposal_quality = (
        sum(r.score for r in ratings) / len(ratings) if ratings else 0.0
    )

    return GoGateScores(
        acceptance=round(acceptance, 4),
        applied_substance=applied_substance,
        engagement_presence=round(engagement_presence, 4),
        proposal_quality=round(proposal_quality, 4),
    )
