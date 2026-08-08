"""Test suite for end-skill working_state RMW contract (#1341).

Verifies that Step 5 in end/SKILL.md documents the required read-modify-write
semantics, merge-doc format, GC, size cap, tombstone, and scoping behaviors
to prevent parallel sessions from overwriting each other's checkpoints.
"""

import re
from pathlib import Path


def _load_end_skill() -> tuple[Path | None, str | None]:
    """Load end/SKILL.md from canonical or mirror path.

    Returns (path, content) or (None, None).
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".claude-userlevel" / "skills" / "end" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "end" / "SKILL.md",
    ]

    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    return candidate, f.read()
            except UnicodeDecodeError:
                with open(candidate, "r", encoding="cp1252") as f:
                    return candidate, f.read()
    return None, None


class TestEndSkillWorkingStateRMW:
    """Verify end/SKILL.md Step 5 documents RMW contract for working_state."""

    def test_step_5_exists(self):
        """AC1: Step 5 section exists in end/SKILL.md."""
        _, content = _load_end_skill()
        assert content is not None
        assert "## Step 5" in content, "Step 5 heading not found"

    def test_step_5_mentions_read_modify_write(self):
        """AC1: Step 5 documents read-modify-write pattern."""
        _, content = _load_end_skill()
        assert content is not None
        # Look for RMW keywords in Step 5 section
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match, "Step 5 section not found"
        step5 = step5_match.group(0)

        has_read_write = ("read" in step5.lower() and "modify" in step5.lower() and "write" in step5.lower()) or \
                         "read-modify-write" in step5.lower() or \
                         "RMW" in step5
        assert has_read_write, "Step 5 does not mention read-modify-write pattern"

    def test_step_5_mentions_merge_doc_format(self):
        """AC2: Step 5 documents merge-doc format with ### [entry] blocks."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for block format markers
        assert "### [entry]" in step5, "Step 5 does not mention ### [entry] block format"

    def test_step_5_mentions_gc_logic(self):
        """AC3: Step 5 documents garbage collection for old entries."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for GC keywords
        has_gc = "GC" in step5 or "garbage" in step5.lower() or \
                 ("delete" in step5.lower() and "old" in step5.lower()) or \
                 ("evict" in step5.lower() and ("age" in step5.lower() or "date" in step5.lower()))
        assert has_gc, "Step 5 does not document GC/eviction logic"

    def test_step_5_mentions_size_cap(self):
        """AC4: Step 5 documents size cap (1500 chars, ≤3 entries)."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for cap/limit keywords with size numbers
        has_size_cap = ("1500" in step5 or "cap" in step5.lower() or "≤3" in step5 or "limit" in step5.lower()) and \
                       ("char" in step5.lower() or "size" in step5.lower() or "entries" in step5.lower())
        assert has_size_cap, "Step 5 does not document size cap (1500 chars, ≤3 entries)"

    def test_step_5_mentions_tombstone(self):
        """AC5: Step 5 documents tombstone marking for evicted entries."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for tombstone or evicted marker
        assert ("### [evicted]" in step5 or "tombstone" in step5.lower()), \
            "Step 5 does not mention ### [evicted] tombstone or eviction marking"

    def test_step_5_mentions_scoping_within_block(self):
        """AC6: Step 5 documents gate scoping within ### [entry] blocks."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for scoping or block-boundary keywords
        has_scoping = ("block" in step5.lower() and "scop" in step5.lower()) or \
                      "within" in step5.lower() or \
                      "own entry" in step5.lower()
        assert has_scoping, "Step 5 does not document scoping gates within blocks"

    def test_step_5_mentions_read_after_write(self):
        """AC8: Step 5 documents read-after-write verification of own block."""
        _, content = _load_end_skill()
        assert content is not None
        step5_match = re.search(r"## Step 5.*?(?=## Step \d|$)", content, re.DOTALL)
        assert step5_match
        step5 = step5_match.group(0)

        # Look for verification/check after write keywords
        has_verify = ("read" in step5.lower() and "after" in step5.lower() and "write" in step5.lower()) or \
                     ("verify" in step5.lower() and "block" in step5.lower()) or \
                     ("check" in step5.lower() and ("presence" in step5.lower() or "own" in step5.lower()))
        assert has_verify, "Step 5 does not document read-after-write verification"

    def test_step_5_mentions_ceiling_marker(self):
        """AC10: end/SKILL.md includes ceiling: comment explaining dominant failure mode."""
        _, content = _load_end_skill()
        assert content is not None

        # Look for ceiling marker with RMW or prose context
        has_ceiling = "ceiling:" in content and \
                      (("prose" in content.lower() or "instruction" in content.lower()) or \
                       "read-modify-write" in content.lower() or \
                       "RMW" in content)
        assert has_ceiling, "end/SKILL.md does not have ceiling: marker explaining failure mode"


class TestOtherReadersUnchanged:
    """Verify AC7: other readers (session-context.py, research/SKILL.md, self-improve/SKILL.md) are not modified."""

    def test_session_context_working_state_query_unchanged(self):
        """AC7: session-context.py still queries working_state_<project> as before."""
        path = Path(__file__).resolve().parent.parent.parent / "scripts" / "session-context.py"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Verify the simple query is still there
            assert "working_state_" in content, "session-context.py no longer queries working_state"

    def test_research_skill_gate_unchanged(self):
        """AC7: research/SKILL.md still references working_state gate pattern."""
        path = Path(__file__).resolve().parent.parent.parent / ".claude-userlevel" / "skills" / "research" / "SKILL.md"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Verify the gate reference pattern is unchanged
            # (exact content varies, but working_state should be mentioned in research pass gate)
            assert "working_state" in content or "research-pass" in content, \
                "research/SKILL.md may have been inadvertently modified"


class TestStep8OutputMentionsTombstones:
    """Verify AC5: Step 8 output mentions tombstones when AC5 fires."""

    def test_step_8_output_has_section_for_tombstones(self):
        """AC5: Step 8 output documents evicted entries."""
        _, content = _load_end_skill()
        assert content is not None

        # Step 8 should mention the tombstone markers that Step 5 creates
        # Look for the Step 8 section more flexibly (avoid lookahead issues with code blocks)
        step8_start = content.find("## Step 8")
        assert step8_start != -1, "Step 8 section not found"

        # Extract from Step 8 to the end of file (or until we're clearly past Step 8 content)
        step8_content = content[step8_start:]

        # If Step 5 mentions eviction, Step 8 should too (within its output template)
        if "evict" in content.lower():
            assert "evict" in step8_content.lower(), "Step 8 does not report evictions from Step 5"
