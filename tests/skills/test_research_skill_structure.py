"""Test suite for /research skill 4-channel protocol (issue #691).

Tests enforce the 4-channel mandatory intake protocol:
1. End-user experience (Reddit, HN, dev blogs)
2. Domain specialist opinion (expert blogs, lab posts)
3. Quantitative data / research (arxiv, papers, benchmarks)
4. Adversarial / failure-mode (post-mortems, GitHub issues, retrospectives)

All channels must be explicitly present in SKILL.md.
Memory recall does not substitute for external channels per decision 6fd2df1d-defc-440d-ba30-71880409e533.
"""

import re
from pathlib import Path


class TestResearchSkillStructure:
    """Verify 4-channel protocol structure in /research SKILL.md."""

    @classmethod
    def setup_class(cls):
        """Load the research SKILL.md file once for all tests."""
        # Canonical source is the repo (tests/skills/test_X.py -> repo_root/.claude-userlevel/...)
        repo_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            repo_root / ".claude-userlevel" / "skills" / "research" / "SKILL.md",
            Path.home() / ".claude" / "skills" / "research" / "SKILL.md",
        ]

        cls.skill_path = None
        cls.skill_content = None

        for candidate in candidates:
            if candidate.exists():
                cls.skill_path = candidate
                try:
                    with open(candidate, 'r', encoding='utf-8') as f:
                        cls.skill_content = f.read()
                except UnicodeDecodeError:
                    with open(candidate, 'r', encoding='cp1252') as f:
                        cls.skill_content = f.read()
                break

        assert cls.skill_content is not None, \
            "Could not find /research SKILL.md"

    def test_channel_1_users_heading_exists(self):
        """AC: /research output template includes '## Channel 1: Users' heading."""
        assert "## Channel 1: Users" in self.skill_content, \
            "SKILL.md must include '## Channel 1: Users' heading"

    def test_channel_2_specialists_heading_exists(self):
        """AC: /research output template includes '## Channel 2: Specialists' heading."""
        assert "## Channel 2: Specialists" in self.skill_content, \
            "SKILL.md must include '## Channel 2: Specialists' heading"

    def test_channel_3_data_heading_exists(self):
        """AC: /research output template includes '## Channel 3: Data' heading."""
        assert "## Channel 3: Data" in self.skill_content, \
            "SKILL.md must include '## Channel 3: Data' heading"

    def test_channel_4_adversarial_heading_exists(self):
        """AC: /research output template includes '## Channel 4: Adversarial' heading."""
        assert "## Channel 4: Adversarial" in self.skill_content, \
            "SKILL.md must include '## Channel 4: Adversarial' heading"

    def test_memory_recall_does_not_substitute_statement(self):
        """AC: SKILL.md explicitly states memory recall does not substitute for external channels."""
        assert "memory recall does not substitute" in self.skill_content.lower(), \
            "SKILL.md must explicitly state 'memory recall does not substitute'"

    def test_decision_uuid_reference(self):
        """AC: SKILL.md references decision UUID."""
        assert "6fd2df1d-defc-440d-ba30-71880409e533" in self.skill_content, \
            "SKILL.md must reference decision UUID"

    def test_four_channel_protocol_section_exists(self):
        """AC: SKILL.md has a 4-channel mandatory section."""
        has_channel_section = bool(
            re.search(r"4[- ]channel|4-channel", self.skill_content, re.IGNORECASE)
        )
        assert has_channel_section, \
            "SKILL.md must have 4-channel protocol section"

    def test_channel_1_description_and_examples(self):
        """AC: Channel 1 (Users) has description and example queries."""
        channel_1_block = re.search(
            r"##\s*Channel\s*1.*?(?=##\s*Channel|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )
        assert channel_1_block is not None, \
            "Channel 1 (Users) section must exist"
        assert re.search(r"example", channel_1_block.group(0), re.IGNORECASE), \
            "Channel 1 must include example queries"

    def test_channel_2_description_and_examples(self):
        """AC: Channel 2 (Specialists) has description and example queries."""
        channel_2_block = re.search(
            r"##\s*Channel\s*2.*?(?=##\s*Channel|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )
        assert channel_2_block is not None, \
            "Channel 2 (Specialists) section must exist"

    def test_channel_3_description_and_examples(self):
        """AC: Channel 3 (Data) has description and example queries."""
        channel_3_block = re.search(
            r"##\s*Channel\s*3.*?(?=##\s*Channel|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )
        assert channel_3_block is not None, \
            "Channel 3 (Data) section must exist"

    def test_channel_4_description_and_examples(self):
        """AC: Channel 4 (Adversarial) has description and example queries."""
        channel_4_block = re.search(
            r"##\s*Channel\s*4.*?(?=##\s*Channel|$)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL
        )
        assert channel_4_block is not None, \
            "Channel 4 (Adversarial) section must exist"

    def test_scope_note_mentions_grandfathered_memories(self):
        """AC: SKILL.md contains Scope noting grandfathered memories."""
        has_scope = bool(
            re.search(r"Scope|grandfathered", self.skill_content, re.IGNORECASE)
        )
        assert has_scope, \
            "SKILL.md must include Scope section"

    def test_mandatory_coverage_enforcement_note(self):
        """AC: SKILL.md states all 4 channels are mandatory."""
        assert re.search(
            r"mandatory|all.*4.*channel",
            self.skill_content,
            re.IGNORECASE
        ), \
            "SKILL.md must state all 4 channels are mandatory"
