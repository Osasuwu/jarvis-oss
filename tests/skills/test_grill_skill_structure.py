"""Test suite for /grill skill anti-sycophancy improvements (issue #689).

Tests enforce the two key anti-sycophancy prompt-level edits:
1. Third-person reframing when grilling proposals (researcher role instead of direct advice)
2. Assumption verbalization opening phase (calibrate expectations before questioning)

All features must be explicitly present in SKILL.md with literal example phrasing.
Decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448 must be referenced.
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

    def test_assumption_verbalization_phase_heading(self):
        """AC: SKILL.md includes 'Assumption verbalization' as a named first phase.

        Must have explicit heading or section titled with 'Assumption verbalization'
        or similar before the WHY/HOW questioning starts.
        """
        has_assumption_section = bool(
            re.search(
                r"Assumption.*verbali|verbali.*assumption|assump.*phase|phase.*assump",
                self.skill_content,
                re.IGNORECASE
            )
        )
        assert has_assumption_section, \
            "SKILL.md must include explicit 'Assumption verbalization' phase heading"

    def test_assumption_verbalization_lists_expectations(self):
        """AC: Assumption verbalization phase lists 3-5 assumptions about user level/context/time.

        Must mention assumptions about expertise, time constraints, context, or similar
        calibration points.
        """
        # Look for the assumption section and check it mentions relevant calibration points
        assumption_section = re.search(
            r"(?:Assumption.*verbali|assumption.*phase).*?(?=##|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )

        assert assumption_section is not None, \
            "Assumption verbalization section must exist in SKILL.md"

        section_text = assumption_section.group(0).lower()

        # Check for mention of key calibration dimensions
        has_calibration = bool(
            re.search(
                r"expertise|time.*constraint|time.*budget|context|experience|level|scope",
                section_text,
                re.IGNORECASE
            )
        )
        assert has_calibration, \
            "Assumption verbalization phase must mention assumptions about user expertise, time, or context"

    def test_assumption_verbalization_asks_for_correction(self):
        """AC: Assumption verbalization phase explicitly asks user to confirm or correct assumptions.

        Must include language like 'confirm', 'correct', 'adjust', 'wrong about', etc.
        """
        assumption_section = re.search(
            r"(?:Assumption.*verbali|assumption.*phase).*?(?=##|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )

        assert assumption_section is not None, \
            "Assumption verbalization section must exist"

        section_text = assumption_section.group(0)

        has_confirmation_request = bool(
            re.search(
                r"confirm|correct|adjust|wrong about|off base|disagree",
                section_text,
                re.IGNORECASE
            )
        )
        assert has_confirmation_request, \
            "Assumption verbalization must ask user to confirm or correct assumptions"

    def test_decision_uuid_reference(self):
        """AC: SKILL.md references decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448.

        This UUID must appear somewhere in the file to link to the decision basis.
        """
        assert "316c5911-9f06-44de-8f99-20fe3e9fa448" in self.skill_content, \
            "SKILL.md must reference decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448"

    def test_phase_ordering_assumption_before_why_how(self):
        """AC: Assumption verbalization phase comes before WHY/HOW questioning in SKILL.md.

        If both assumption phase and questioning phase mention WHY/HOW,
        assumption section must appear first in the file.
        """
        assumption_match = re.search(
            r"assumption.*verbali|assumption.*phase",
            self.skill_content,
            re.IGNORECASE
        )
        why_how_match = re.search(
            r"WHY.*HOW|why.*how|questioning.*phase",
            self.skill_content,
            re.IGNORECASE
        )

        # If both exist, assumption must come first
        if assumption_match and why_how_match:
            assert assumption_match.start() < why_how_match.start(), \
                "Assumption verbalization phase must come before WHY/HOW questioning phase"

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
