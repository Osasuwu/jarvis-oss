"""Menu renderer: pure module producing the proposal-menu text block.

Given queue rows + trust-ceiling config, returns the rendered block for
the session-start surface.  No I/O, no side effects — takes data as
parameters, returns a string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


# ── Domain types ─────────────────────────────────────────────────────────


class Tier(Enum):
    """Proposal tier — higher = more impactful / urgent."""

    M0 = 0
    M1 = 1
    M2 = 2
    M3 = 3

    @classmethod
    def from_str(cls, s: str) -> Tier:
        mapping = {"M0": cls.M0, "M1": cls.M1, "M2": cls.M2, "M3": cls.M3}
        return mapping[s.upper()]

    def __str__(self) -> str:
        return self.name


BenefitKind = Literal["metric", "judgement"]
Traceability = Literal[
    "advances:C_x→target",
    "local-hygiene",
    "exploratory/no-claim",
]


@dataclass(frozen=True)
class QueueRow:
    """A single row from the strategic proposal queue (surface-facing fields)."""

    proposal_id: str
    title: str
    why: str
    tier: Tier
    benefit_kind: BenefitKind
    traceability: Traceability
    confidence: float


@dataclass(frozen=True)
class CeilingConfig:
    """Trust ceiling that gates auto-apply."""

    ceiling: Tier  # proposals above this tier are "above ceiling"


# ── Public API ───────────────────────────────────────────────────────────


SEPARATOR = "─" * 72


def render_menu(
    proposals: list[QueueRow],
    ceiling: CeilingConfig,
) -> str:
    """Render the proposal-menu text block for the session-start surface.

    Parameters
    ----------
    proposals:
        Queue rows to render (may be empty).
    ceiling:
        Current trust-ceiling config.

    Returns
    -------
    Rendered text block.  Always includes the header — an empty proposal
    list still shows the header so a quiet cycle is distinguishable from
    a broken surface.
    """
    above_count = sum(
        1 for p in proposals if p.tier.value > ceiling.ceiling.value
    )

    lines: list[str] = [
        SEPARATOR,
        f"  Strategic Proposals  ·  ceiling: {ceiling.ceiling}"
        f"  ({above_count} above ceiling)",
        SEPARATOR,
    ]

    if not proposals:
        lines.append("  (no proposals — surface is idle)")
        lines.append(SEPARATOR)
        return "\n".join(lines)

    for p in proposals:
        lines.append(
            f"  [{p.tier}] {p.title}"
        )
        lines.append(f"   why: {p.why}")
        lines.append(f"   kind: {p.benefit_kind}  ·  trace: {p.traceability}")

    lines.append(SEPARATOR)
    return "\n".join(lines)
