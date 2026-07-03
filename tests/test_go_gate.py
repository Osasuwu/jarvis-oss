"""Tests for the GO-gate scorer pure function.

Covers all four dimensions + edge cases through the function's return value.
No side effects — the function under test is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from strategic_ideation.go_gate import (
    EMPTY_SCORES,
    GoGateScores,
    ProposalDisposition,
    QualityRating,
    compute_go_gate,
    DispositionRecord,
)


def _d(
    proposal_id: str,
    status: str,
    dispositioned: bool = True,
) -> DispositionRecord:
    """Shorthand builder for test dispositions."""
    return DispositionRecord(
        proposal_id=proposal_id,
        status=status,
        surfaced_at=datetime(2026, 6, 1, tzinfo=UTC),
        dispositioned_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        if dispositioned
        else None,
    )


def _r(proposal_id: str, score: float) -> QualityRating:
    """Shorthand builder for test quality ratings."""
    return QualityRating(
        proposal_id=proposal_id,
        score=score,
        rated_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )


# ── acceptance ───────────────────────────────────────────────────────────


class TestAcceptance:
    def test_all_accepted(self):
        """acceptance = 1.0 when every surfaced proposal is accepted."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.ACCEPTED),
            _d("p3", ProposalDisposition.ACCEPTED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 1.0

    def test_half_accepted(self):
        """acceptance = 0.5 when half of surfaced proposals are accepted."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.REJECTED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 0.5

    def test_none_accepted(self):
        """acceptance = 0.0 when no proposal is accepted."""
        dispositions = [
            _d("p1", ProposalDisposition.REJECTED),
            _d("p2", ProposalDisposition.REJECTED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 0.0

    def test_acceptance_excludes_expired(self):
        """Expired proposals are surfaced but not counted as accepted."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.EXPIRED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 0.5

    def test_acceptance_excludes_deferred(self):
        """Deferred proposals are surfaced but not counted as accepted."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.DEFERRED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 0.5


# ── applied-substance ────────────────────────────────────────────────────


class TestAppliedSubstance:
    def test_count_applied(self):
        """applied-substance = number of north-star-linked applied proposals."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.ACCEPTED),
        ]
        scores = compute_go_gate(
            dispositions,
            applied_proposal_ids={"p1", "p2"},
        )
        assert scores.applied_substance == 2

    def test_subset_applied(self):
        """Only owner-confirmed north-star-linked proposals count."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.ACCEPTED),
            _d("p3", ProposalDisposition.ACCEPTED),
        ]
        scores = compute_go_gate(
            dispositions,
            applied_proposal_ids={"p2"},
        )
        assert scores.applied_substance == 1

    def test_zero_applied(self):
        """applied-substance = 0 when nothing is confirmed as applied."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        scores = compute_go_gate(dispositions)
        assert scores.applied_substance == 0

    def test_no_dispositions_caps(self):
        """applied-substance works even when nothing was surfaced."""
        scores = compute_go_gate([], applied_proposal_ids=set())
        assert scores.applied_substance == 0

    def test_applied_without_acceptance(self):
        """applied-substance is independent of acceptance count."""
        # p1 is accepted and applied, p2 is only applied
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        scores = compute_go_gate(
            dispositions,
            applied_proposal_ids={"p2"},
        )
        assert scores.applied_substance == 1
        assert scores.acceptance == 1.0  # independent axis


# ── engagement-presence ──────────────────────────────────────────────────


class TestEngagementPresence:
    def test_full_engagement(self):
        """engagement-presence = 1.0 when every proposal received a disposition."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.REJECTED),
            _d("p3", ProposalDisposition.DEFERRED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.engagement_presence == 1.0

    def test_partial_engagement(self):
        """engagement-presence reflects ratio of dispositioned proposals."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.REJECTED),
            _d("p3", ProposalDisposition.EXPIRED, dispositioned=False),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.engagement_presence == pytest.approx(2 / 3, abs=1e-4)

    def test_expired_proposals_no_engagement(self):
        """Expired proposals (owner took no action) do not count as engaged."""
        dispositions = [
            _d("p1", ProposalDisposition.EXPIRED, dispositioned=False),
            _d("p2", ProposalDisposition.EXPIRED, dispositioned=False),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.engagement_presence == 0.0

    def test_surfaced_only_no_engagement(self):
        """Surfaced-but-not-dispositioned proposals do not count as engaged."""
        dispositions = [
            DispositionRecord(
                proposal_id="p1",
                status=ProposalDisposition.SURFACED,
                surfaced_at=datetime(2026, 6, 1, tzinfo=UTC),
                dispositioned_at=None,
            ),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.engagement_presence == 0.0


# ── proposal-quality ─────────────────────────────────────────────────────


class TestProposalQuality:
    def test_average_rating(self):
        """proposal-quality = mean of owner value-if-applied ratings."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        ratings = [_r("p1", 4.0), _r("p2", 3.0)]
        scores = compute_go_gate(dispositions, quality_ratings=ratings)
        assert scores.proposal_quality == 3.5

    def test_single_rating(self):
        """proposal-quality = the single rating when only one exists."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        ratings = [_r("p1", 5.0)]
        scores = compute_go_gate(dispositions, quality_ratings=ratings)
        assert scores.proposal_quality == 5.0

    def test_no_ratings(self):
        """proposal-quality = 0.0 when no quality ratings exist."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        scores = compute_go_gate(dispositions)
        assert scores.proposal_quality == 0.0

    def test_all_zero_ratings(self):
        """proposal-quality handles zero-valued ratings correctly."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        ratings = [_r("p1", 0.0), _r("p2", 0.0)]
        scores = compute_go_gate(dispositions, quality_ratings=ratings)
        assert scores.proposal_quality == 0.0


# ── edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_window(self):
        """All scores are zero for an empty window."""
        scores = compute_go_gate([])
        assert scores == EMPTY_SCORES

    def test_all_rejected(self):
        """All-rejected window: only engagement-presence may be non-zero."""
        dispositions = [
            _d("p1", ProposalDisposition.REJECTED),
            _d("p2", ProposalDisposition.REJECTED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.acceptance == 0.0
        assert scores.applied_substance == 0
        assert scores.engagement_presence == 1.0
        assert scores.proposal_quality == 0.0

    def test_no_quality_ratings(self):
        """Proposal-quality defaults to 0 when no ratings provided."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.REJECTED),
        ]
        scores = compute_go_gate(dispositions)
        assert scores.proposal_quality == 0.0

    def test_mixed_scenario(self):
        """Realistic mixed scenario exercises all dimensions."""
        dispositions = [
            _d("p1", ProposalDisposition.ACCEPTED),
            _d("p2", ProposalDisposition.ACCEPTED),
            _d("p3", ProposalDisposition.REJECTED),
            _d("p4", ProposalDisposition.DEFERRED),
            _d("p5", ProposalDisposition.EXPIRED, dispositioned=False),
        ]
        ratings = [
            _r("p1", 4.5),
            _r("p2", 3.0),
        ]
        scores = compute_go_gate(
            dispositions,
            applied_proposal_ids={"p1"},
            quality_ratings=ratings,
        )
        assert scores.acceptance == pytest.approx(2 / 5, abs=1e-4)  # 0.4
        assert scores.applied_substance == 1
        assert scores.engagement_presence == pytest.approx(4 / 5, abs=1e-4)  # 0.8
        assert scores.proposal_quality == 3.75

    def test_as_dict_serialisation(self):
        """as_dict produces the expected mapping."""
        scores = GoGateScores(
            acceptance=0.5,
            applied_substance=2,
            engagement_presence=0.75,
            proposal_quality=4.0,
        )
        d = scores.as_dict()
        assert d["acceptance"] == 0.5
        assert d["applied_substance"] == 2
        assert d["engagement_presence"] == 0.75
        assert d["proposal_quality"] == 4.0

    def test_applied_proposal_not_in_dispositions(self):
        """applied-substance can reference proposals outside the window."""
        dispositions = [_d("p1", ProposalDisposition.ACCEPTED)]
        scores = compute_go_gate(
            dispositions,
            applied_proposal_ids={"p99"},
        )
        assert scores.applied_substance == 1  # independent of disposition list
