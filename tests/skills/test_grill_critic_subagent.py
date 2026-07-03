"""Test suite for /grill cross-context CRITIC subagent (issue #692).

Tests enforce the four AC dimensions resolved in the 2026-05-17 grill session:

AC1 (trigger heuristic, decision c29c2b00-e9e1-43d1-93ff-ada5820c434c):
    SKILL.md names exactly two triggers — AC-lock gate and
    record_decision(reversibility in {hard, irreversible}).
AC2 (context scrubbing, part of decision 222e9bfe-2150-400c-afd9-a3e8defb5988):
    Scrubbing is behavioural (Agent + nudge prompt), not structural
    (no isolation=worktree). Documented alongside the NEUTRAL-RESEARCHER
    precedent reference.
AC3 (CRITIC.md exists with fixed-schema output, decisions 222e9bfe... and
    5d084972-5adb-4df7-8edb-717a3515f522):
    Sibling file under .claude-userlevel/skills/grill/CRITIC.md.
    Fixed schema: <=3 risks (with severity) / <=3 unmentioned alternatives /
    1 challenged assumption. No prose.
AC4 (loopback rule, part of decision 222e9bfe-2150-400c-afd9-a3e8defb5988):
    Per-item disposition (accept/reject/defer) is FORCED and BLOCKS AC-lock.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
SKILL_DIR = REPO_ROOT / ".claude-userlevel" / "skills" / "grill"
SKILL_MD = SKILL_DIR / "SKILL.md"
CRITIC_MD = SKILL_DIR / "CRITIC.md"


def _read(path: Path) -> str:
    assert path.exists(), f"Required file not found: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_md() -> str:
    return _read(SKILL_MD)


@pytest.fixture(scope="module")
def critic_md() -> str:
    return _read(CRITIC_MD)


class TestAC1TriggerHeuristic:
    """AC1: explicit trigger list in SKILL.md naming both AC-lock and hard-decision."""

    def test_skill_md_mentions_cross_context_phase(self, skill_md: str):
        assert re.search(r"cross[- ]context (review|critic)", skill_md, re.IGNORECASE), \
            "SKILL.md must reference the cross-context review phase by name"

    def test_skill_md_names_ac_lock_trigger(self, skill_md: str):
        assert re.search(r"AC[- ]lock", skill_md), \
            "SKILL.md must name the AC-lock gate as a CRITIC trigger"

    def test_skill_md_names_hard_irreversible_trigger(self, skill_md: str):
        assert re.search(r"reversibility.*(hard|irreversible)", skill_md, re.IGNORECASE), \
            "SKILL.md must name record_decision(reversibility in {hard, irreversible}) as a CRITIC trigger"

    def test_skill_md_excludes_why_how_ceremony(self, skill_md: str):
        # WHY->HOW and HOW->AC were explicitly REJECTED as ceremony in decision
        # c29c2b00. Allowed: discussing them as rejected. Forbidden: listing them
        # as active triggers without rejection framing. The positive form is
        # easier to make robust than a negative regex: if either ceremony
        # pattern is mentioned anywhere, SKILL.md must also state they were
        # rejected as ceremony.
        if re.search(r"WHY.*HOW|HOW.*AC", skill_md):
            assert re.search(
                r"reject(ed)?\s+as\s+ceremony|considered.*reject|rejected\s+as\s+ceremony",
                skill_md,
                re.IGNORECASE,
            ), "SKILL.md mentions WHY->HOW/HOW->AC; must explicitly note they were rejected as ceremony"

    def test_skill_md_references_trigger_decision(self, skill_md: str):
        assert "c29c2b00-e9e1-43d1-93ff-ada5820c434c" in skill_md, \
            "SKILL.md must cite the trigger-heuristic decision UUID c29c2b00..."


class TestAC2ContextScrubbing:
    """AC2: behavioural scrubbing documented; no worktree-isolation assumption."""

    def test_skill_md_documents_scrubbing_approach(self, skill_md: str):
        assert re.search(r"behaviour?al|nudge", skill_md, re.IGNORECASE), \
            "SKILL.md must document the behavioural-not-structural scrubbing approach"

    def test_skill_md_disclaims_structural_isolation(self, skill_md: str):
        # SKILL.md must state that isolation is behavioural and not via worktree
        # so the operator does not pass isolation=worktree, which would lose
        # project memory/codebase the critic needs for grounded critique.
        text = skill_md.lower()
        mentions_isolation_disclaimer = (
            "isolation" in text
            and ("worktree" in text or "structural" in text)
            and "behavioural" in text.replace("behavioral", "behavioural")
        )
        assert mentions_isolation_disclaimer, \
            "SKILL.md must explicitly disclaim structural (worktree) isolation in favour of behavioural nudge"

    def test_skill_md_references_neutral_researcher_precedent(self, skill_md: str):
        assert "NEUTRAL-RESEARCHER" in skill_md, \
            "SKILL.md must reference the NEUTRAL-RESEARCHER.md precedent from /reason"


class TestAC3CriticMdFixedSchema:
    """AC3: CRITIC.md exists, is a verbatim-pasteable system block, fixed schema."""

    def test_critic_md_exists(self):
        assert CRITIC_MD.exists(), f"CRITIC.md must exist at {CRITIC_MD}"

    def test_critic_md_has_pasteable_system_block(self, critic_md: str):
        # NEUTRAL-RESEARCHER convention: a fenced code block titled "System block"
        # (or equivalent) that the operator pastes verbatim into the subagent prompt.
        assert re.search(r"system block", critic_md, re.IGNORECASE), \
            "CRITIC.md must contain a 'System block' section for verbatim paste"
        # Must include at least one fenced code block (the pasteable prompt body).
        assert "```" in critic_md, "CRITIC.md must have a fenced code block holding the prompt"

    def test_critic_md_specifies_max_3_risks_with_severity(self, critic_md: str):
        assert re.search(r"(<=|≤|at most|max|maximum|up to)\s*3\s*risks?", critic_md, re.IGNORECASE), \
            "CRITIC.md must specify the <=3 risks bound"
        assert re.search(r"severity", critic_md, re.IGNORECASE), \
            "CRITIC.md must require severity tagging per risk"

    def test_critic_md_specifies_max_3_alternatives(self, critic_md: str):
        assert re.search(r"(<=|≤|at most|max|maximum|up to)\s*3\s*(unmentioned\s+)?alternatives?", critic_md, re.IGNORECASE), \
            "CRITIC.md must specify the <=3 unmentioned-alternatives bound"

    def test_critic_md_specifies_one_challenged_assumption(self, critic_md: str):
        # The "1 challenged assumption" slot — exactly one, not a list.
        assert re.search(r"1\s+challenged\s+assumption|one\s+challenged\s+assumption", critic_md, re.IGNORECASE), \
            "CRITIC.md must specify the 1-challenged-assumption slot"

    def test_critic_md_forbids_freeform_prose(self, critic_md: str):
        # Fixed schema is load-bearing — prose lets the critic hedge.
        assert re.search(r"no prose|not\s+prose|do not.*prose|don'?t.*prose|fixed (schema|format)", critic_md, re.IGNORECASE), \
            "CRITIC.md must forbid free-form prose / require fixed schema"

    def test_critic_md_references_contract_decision(self, critic_md: str):
        assert "222e9bfe-2150-400c-afd9-a3e8defb5988" in critic_md, \
            "CRITIC.md must cite the contract decision UUID 222e9bfe..."

    def test_critic_md_references_bundle_layout_decision(self, critic_md: str):
        assert "5d084972-5adb-4df7-8edb-717a3515f522" in critic_md, \
            "CRITIC.md must cite the bundle-layout decision UUID 5d084972..."

    def test_skill_md_links_to_critic_md(self, skill_md: str):
        # Operator must be able to navigate from SKILL.md driver to CRITIC.md.
        assert re.search(r"\[.*CRITIC.*\]\(.*CRITIC\.md\)|CRITIC\.md", skill_md), \
            "SKILL.md must link to CRITIC.md so the operator can find the prompt template"


class TestAC4LoopbackBlocksAcLock:
    """AC4: forced per-item disposition (accept/reject/defer) BLOCKS AC-lock."""

    def test_skill_md_names_three_dispositions(self, skill_md: str):
        text = skill_md.lower()
        assert "accept" in text and "reject" in text and "defer" in text, \
            "SKILL.md must name the three dispositions (accept/reject/defer)"

    def test_skill_md_states_disposition_is_forced(self, skill_md: str):
        assert re.search(r"forced|mandatory|required|must", skill_md, re.IGNORECASE), \
            "SKILL.md must state that per-item disposition is forced/required"

    def test_skill_md_states_disposition_blocks_ac_lock(self, skill_md: str):
        # The load-bearing claim: without disposition on every item, AC-lock cannot proceed.
        assert re.search(r"block.*AC[- ]lock|AC[- ]lock.*block|cannot.*lock.*AC|AC[- ]lock.*until", skill_md, re.IGNORECASE), \
            "SKILL.md must state that disposition gating BLOCKS AC-lock"

    def test_skill_md_states_per_item_not_bulk(self, skill_md: str):
        # The rule is per-item, not "accept all" / "reject all" sweep.
        assert re.search(r"per[- ]item|per[- ]finding|each (risk|alternative|finding|item)", skill_md, re.IGNORECASE), \
            "SKILL.md must require per-item (not bulk) disposition"
