"""Pins the /status anchored-routing contract in CLAUDE.md (#1018 AC6/AC7).

The /status skill is routed by an *anchored* trigger set — only the exact
words `статус` / `status` / `статус <repo>` fire it. A bare/unrelated use of
the word must not be read as a command to run a repo-state investigation
(the original failure mode this slice closes). These are documentation
guards: if someone edits the routing table and drops the anchor language,
red CI forces the contract back into the doc rather than letting routing
silently widen.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC6 — routing table maps ONLY статус / status / статус <repo> to /status
# ---------------------------------------------------------------------------


class TestRoutingRow:
    def test_status_row_present(self):
        # the skill-routing table has a row pointing at the /status skill
        assert "`/status`" in CLAUDE_MD

    def test_row_lists_all_three_anchored_triggers(self):
        # the row must enumerate every anchored trigger so the mapping is explicit
        for trigger in ("статус", "status", "статус <repo>"):
            assert trigger in CLAUDE_MD, f"missing anchored trigger: {trigger}"

    def test_row_marks_routing_as_anchored(self):
        # the word "anchored" must appear so the constraint is not lost on edit
        assert "anchored" in CLAUDE_MD.lower()


# ---------------------------------------------------------------------------
# AC7 — anchored-routing behavior documented: a bare unrelated use of the
#       word does NOT trigger an investigation
# ---------------------------------------------------------------------------


class TestAntiInvestigationNote:
    def test_anti_investigation_rule_documented(self):
        # there is a prose rule stating bare/unrelated word use does not fire /status
        lowered = CLAUDE_MD.lower()
        assert "do not fire it" in lowered or "do not fire" in lowered
        # and it must name the over-eager-investigation failure mode it closes
        assert "investigation" in lowered

    def test_rule_names_the_skill(self):
        # the anti-investigation note is explicitly about /status, not generic
        assert "`/status` is anchored routing" in CLAUDE_MD
