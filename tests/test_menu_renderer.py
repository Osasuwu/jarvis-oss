"""Tests for the strategic-proposal menu renderer.

Covers populated render, ceiling-header count correctness, empty-render,
mixed-tier ordering — all verified through the function's return value.
"""

from __future__ import annotations

from strategic_ideation.menu_renderer import (
    CeilingConfig,
    QueueRow,
    Tier,
    BenefitKind,
    Traceability,
    render_menu,
)


def _row(
    proposal_id: str,
    title: str = "Test proposal",
    tier: Tier = Tier.M1,
    benefit_kind: BenefitKind = "metric",
    traceability: Traceability = "local-hygiene",
    confidence: float = 0.8,
) -> QueueRow:
    """Shorthand builder for test queue rows."""
    return QueueRow(
        proposal_id=proposal_id,
        title=title,
        why="Because it improves X",
        tier=tier,
        benefit_kind=benefit_kind,
        traceability=traceability,
        confidence=confidence,
    )


def _ceiling(tier: Tier = Tier.M2) -> CeilingConfig:
    return CeilingConfig(ceiling=tier)


# ── header ───────────────────────────────────────────────────────────────


class TestHeader:
    def test_ceiling_displayed(self):
        """Header includes the current trust ceiling."""
        result = render_menu([], _ceiling(Tier.M1))
        assert "ceiling: M1" in result

    def test_above_ceiling_count_shown(self):
        """Header shows correct count of proposals above ceiling."""
        proposals = [
            _row("p1", tier=Tier.M0),
            _row("p2", tier=Tier.M2),
            _row("p3", tier=Tier.M3),
        ]
        result = render_menu(proposals, _ceiling(Tier.M2))
        assert "1 above ceiling" in result, (
            f"Expected 1 above ceiling (M3 > M2), got: {result}"
        )

    def test_all_below_ceiling(self):
        """Header shows 0 above when all proposals are at or below ceiling."""
        proposals = [
            _row("p1", tier=Tier.M0),
            _row("p2", tier=Tier.M1),
            _row("p3", tier=Tier.M2),
        ]
        result = render_menu(proposals, _ceiling(Tier.M2))
        assert "0 above ceiling" in result


# ── populated render ─────────────────────────────────────────────────────


class TestPopulatedRender:
    def test_proposal_fields_shown(self):
        """Each proposal line includes title, tier, benefit_kind, traceability."""
        proposals = [
            _row(
                "p1",
                title="Improve recall accuracy",
                tier=Tier.M2,
                benefit_kind="metric",
                traceability="advances:C_x→target",
            ),
        ]
        result = render_menu(proposals, _ceiling())
        assert "Improve recall accuracy" in result
        assert "[M2]" in result
        assert "metric" in result
        assert "advances:C_x→target" in result

    def test_multiple_proposals(self):
        """Multiple proposals each produce their own block."""
        proposals = [
            _row("p1", title="First proposal"),
            _row("p2", title="Second proposal"),
        ]
        result = render_menu(proposals, _ceiling())
        assert "First proposal" in result
        assert "Second proposal" in result

    def test_why_reasoning_included(self):
        """The 'why' reasoning is included for each proposal."""
        p1 = QueueRow(
            proposal_id="p1",
            title="Fix parser",
            why="Reduces false negatives by 15%",
            tier=Tier.M3,
            benefit_kind="metric",
            traceability="local-hygiene",
            confidence=0.9,
        )
        result = render_menu([p1], _ceiling())
        assert "Reduces false negatives by 15%" in result

    def test_judgement_benefit_kind(self):
        """benefit_kind=judgement renders without error."""
        proposals = [
            _row("p1", title="Refactor auth", benefit_kind="judgement"),
        ]
        result = render_menu(proposals, _ceiling())
        assert "judgement" in result

    def test_exploratory_traceability(self):
        """traceability=exploratory/no-claim renders without error."""
        proposals = [
            _row("p1", traceability="exploratory/no-claim"),
        ]
        result = render_menu(proposals, _ceiling())
        assert "exploratory/no-claim" in result


# ── empty render ─────────────────────────────────────────────────────────


class TestEmptyRender:
    def test_header_shown_when_empty(self):
        """Empty queue still renders the header."""
        result = render_menu([], _ceiling(Tier.M2))
        assert "ceiling: M2" in result
        assert "0 above ceiling" in result
        assert "no proposals" in result.lower() or "idle" in result.lower()

    def test_empty_is_not_empty_string(self):
        """Empty render returns a non-empty string (informational)."""
        result = render_menu([], _ceiling())
        assert len(result) > 20  # must contain more than just whitespace

    def test_empty_includes_idle_message(self):
        """Empty queue shows an idle indicator."""
        result = render_menu([], _ceiling())
        assert "idle" in result.lower()


# ── mixed tiers ──────────────────────────────────────────────────────────


class TestMixedTier:
    def test_mixed_tier_all_displayed(self):
        """Proposals of different tiers each render their tier tag."""
        proposals = [
            _row("p1", tier=Tier.M0),
            _row("p2", tier=Tier.M1),
            _row("p3", tier=Tier.M3),
        ]
        result = render_menu(proposals, _ceiling())
        assert "[M0]" in result
        assert "[M1]" in result
        assert "[M3]" in result

    def test_low_ceiling_high_count(self):
        """Lower ceiling = more proposals above ceiling."""
        proposals = [
            _row("p1", tier=Tier.M1),
            _row("p2", tier=Tier.M2),
            _row("p3", tier=Tier.M3),
        ]
        ceil_m0 = _ceiling(Tier.M0)
        ceil_m2 = _ceiling(Tier.M2)
        result_m0 = render_menu(proposals, ceil_m0)
        result_m2 = render_menu(proposals, ceil_m2)

        # With ceiling M0, all 3 are above
        assert "3 above ceiling" in result_m0
        # With ceiling M2, only M3 is above
        assert "1 above ceiling" in result_m2


# ── structure ────────────────────────────────────────────────────────────


class TestStructure:
    def test_separator_bookends(self):
        """Output starts and ends with the separator line."""
        proposals = [_row("p1"), _row("p2")]
        result = render_menu(proposals, _ceiling())
        lines = result.split("\n")
        assert lines[0].startswith("─")
        assert lines[-1].startswith("─")

    def test_header_line_second(self):
        """The header line follows the opening separator."""
        proposals = [_row("p1")]
        result = render_menu(proposals, _ceiling())
        lines = result.split("\n")
        assert "Strategic Proposals" in lines[1]
