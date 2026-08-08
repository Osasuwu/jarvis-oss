"""Test suite for /grill skill anti-sycophancy improvements (issue #689).

Tests enforce the surviving anti-sycophancy prompt-level edit:
1. Third-person reframing when grilling proposals (researcher role instead of direct advice)

Decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448 must be referenced.

The opening-phase "assumption verbalization" behavior originally covered here was
superseded by issue #1413, which replaced it with a session-parameter gate (time
budget / decision stage / cadence) and dropped the confirmation-gate restatement of
expertise/context. See tests/skills/test_grill_frontier_rounds.py for the current
Phase 1 (session-parameter gate) and Phase 2 (dependency-gated frontier rounds)
coverage.
"""

import re
from pathlib import Path


class TestGrillSkillStructure:
    """Verify anti-sycophancy structure in /grill SKILL.md."""

    @classmethod
    def setup_class(cls):
        """Load the grill SKILL.md file once for all tests from canonical source."""
        # Canonical source is in the repo, not in ~/.claude/
        repo_candidates = [
            Path(__file__).parent.parent.parent / ".claude-userlevel" / "skills" / "grill" / "SKILL.md",
        ]

        # Fallback to mirrors
        mirror_candidates = [
            Path.home() / ".claude" / "skills" / "grill" / "SKILL.md",
            Path("/c/Users/jdoe/.claude/skills/grill/SKILL.md"),
        ]

        cls.skill_path = None
        cls.skill_content = None

        # Try canonical source first
        for candidate in repo_candidates:
            if candidate.exists():
                cls.skill_path = candidate
                with open(candidate, 'r', encoding='utf-8') as f:
                    cls.skill_content = f.read()
                break

        # Fall back to mirrors
        if cls.skill_content is None:
            for candidate in mirror_candidates:
                if candidate.exists():
                    cls.skill_path = candidate
                    with open(candidate, 'r', encoding='utf-8') as f:
                        cls.skill_content = f.read()
                    break

        # If not found yet, check if running in a worktree with env var
        if cls.skill_content is None:
            import os
            if 'CLAUDE_SKILL_PATH' in os.environ:
                skill_path = Path(os.environ['CLAUDE_SKILL_PATH'])
                if skill_path.exists():
                    cls.skill_path = skill_path
                    with open(skill_path, 'r', encoding='utf-8') as f:
                        cls.skill_content = f.read()

        assert cls.skill_content is not None, \
            f"Could not find /grill SKILL.md. Checked {repo_candidates} and {mirror_candidates}"

    def test_third_person_reviewer_framing_exists(self):
        """AC: SKILL.md includes explicit third-person reviewer framing for proposal critique.

        Must include reference to 'third-person' or 'senior engineer reviewing' or similar
        that indicates reframing away from direct address ('you proposed') toward
        reviewer voice ('the user proposed...as a reviewer I would').
        """
        has_third_person = bool(
            re.search(
                r"third[- ]person|senior.*engineer|as a.*review|the user proposed",
                self.skill_content,
                re.IGNORECASE
            )
        )
        assert has_third_person, \
            "SKILL.md must include explicit third-person reviewer framing for proposal critique"

    def test_third_person_example_phrasing(self):
        """AC: SKILL.md includes literal example phrasing of third-person reviewer frame.

        Example: 'the user proposed X. As a senior engineer reviewing this proposal,
        what would I push back on?'
        """
        has_example = bool(
            re.search(
                r"the user proposed|senior.*engineer.*reviewing|what would.*push back",
                self.skill_content,
                re.IGNORECASE
            )
        )
        assert has_example, \
            "SKILL.md must include literal example phrasing of third-person reviewer framing"

    def test_decision_uuid_reference(self):
        """AC: SKILL.md references decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448.

        This UUID must appear somewhere in the file to link to the decision basis.
        """
        assert "316c5911-9f06-44de-8f99-20fe3e9fa448" in self.skill_content, \
            "SKILL.md must reference decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448"

    def test_arxiv_reference_for_sycophancy_baseline(self):
        """AC (optional but recommended): SKILL.md references arxiv 2505.23840 for sycophancy baseline.

        This is optional but recommended to document the research basis for third-person reframing.
        """
        # This is a soft check; optional is fine but presence is good
        has_arxiv_ref = "2505.23840" in self.skill_content
        # Not asserting, just noting in test name for documentation
        if not has_arxiv_ref:
            # This is informational, not a hard requirement
            pass
