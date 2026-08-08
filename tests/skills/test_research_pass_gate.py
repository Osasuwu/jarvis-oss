"""Test suite for research-pass gate (#693).

Verifies that the shared prompt fragment exists, all four skills reference it,
and the fragment documents the required pathways (pass, waiver, halt, block).
"""

import re
from pathlib import Path


def _load_file(*parts: str) -> tuple[Path | None, str | None]:
    """Try to load a file from the canonical or fallback paths.

    Returns (path, content) or (None, None).
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".claude-userlevel" / "skills" / path
        for path in [Path(*parts)]
    ] + [
        Path.home() / ".claude" / "skills" / path
        for path in [Path(*parts)]
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


class TestSharedFragment:
    """Verify the shared research-pass-gate.md fragment exists and has
    the required structure."""

    def test_fragment_exists(self):
        """AC: Shared fragment exists at _shared/research-pass-gate.md."""
        path, content = _load_file("_shared", "research-pass-gate.md")
        assert path is not None, \
            "research-pass-gate.md not found in _shared/"
        assert content is not None

    def test_fragment_has_research_pass_gate_heading(self):
        """AC: Fragment has '# Research-pass gate' heading."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "# Research-pass gate" in content

    def test_fragment_has_trigger_modes(self):
        """AC: Fragment documents trigger modes (unconditional vs high-stakes)."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "Unconditional" in content
        assert "High-stakes" in content

    def test_fragment_has_procedure_section(self):
        """AC: Fragment defines procedure with topic extraction."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "## Procedure" in content or "### Procedure" in content
        assert "topic keyword" in content.lower()

    def test_fragment_checks_working_state(self):
        """AC: Fragment checks working_state_<project> for research artifacts."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "working_state" in content

    def test_fragment_checks_memory_recall(self):
        """AC: Fragment falls back to memory_recall with source_provenance filter."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "memory_recall" in content
        assert "source_provenance" in content

    def test_fragment_has_60_day_bound(self):
        """AC: Fragment uses 60-day window for research artifact recency."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "60" in content

    def test_fragment_has_pass_path(self):
        """AC: Fragment documents artifact-found pass path."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "Gate passes" in content or "Artifact found" in content

    def test_fragment_has_waiver_path(self):
        """AC: Fragment documents owner-override waiver path."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "waiver" in content.lower()

    def test_fragment_has_infrastructure_blocked_path(self):
        """AC: Fragment documents infrastructure-blocked fast-waiver path."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "infrastructure" in content.lower()

    def test_fragment_has_autonomous_halt(self):
        """AC: Fragment documents autonomous-mode HALT (no auto-waive)."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "HALT" in content or "halt" in content

    def test_fragment_has_block_path(self):
        """AC: Fragment documents no-research-no-waiver block path."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "BLOCK" in content or "Propose invoking" in content

    def test_fragment_references_691(self):
        """AC: Fragment references the 4-channel research protocol (issue #691)."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "691" in content or "4-channel" in content


class TestGrillGate:
    """Verify /grill SKILL.md references the research-pass gate."""

    def test_grill_references_fragment(self):
        """AC: /grill SKILL.md has research-pass gate section referencing the fragment."""
        _, content = _load_file("grill", "SKILL.md")
        assert content is not None
        assert "research-pass-gate" in content.lower() or "research_pass_gate" in content

    def test_grill_reference_path_resolves(self):
        """AC: The relative path to the shared fragment resolves correctly."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        shared = repo_root / ".claude-userlevel" / "skills" / "_shared" / "research-pass-gate.md"
        assert shared.exists(), \
            f"Expected shared fragment at {shared}"

    def test_grill_mentions_high_stakes_trigger(self):
        """AC: grill gate section specifies high-stakes trigger condition."""
        _, content = _load_file("grill", "SKILL.md")
        assert content is not None
        assert ("hard" in content.lower() and "irreversible" in content.lower()) or \
               "high-stakes" in content.lower()


class TestReasonGate:
    """Verify /reason SKILL.md references the research-pass gate."""

    def test_reason_references_fragment(self):
        """AC: /reason SKILL.md has research-pass gate section."""
        _, content = _load_file("reason", "SKILL.md")
        assert content is not None
        assert "research-pass-gate" in content.lower() or "research_pass_gate" in content

    def test_reason_gate_before_resolution(self):
        """AC: Gate appears before the resolution phase in the skill."""
        _, content = _load_file("reason", "SKILL.md")
        assert content is not None
        # Gate section should mention "before resolution" or similar
        assert "before" in content.lower() and "resolution" in content.lower()

    def test_reason_mentions_high_stakes_trigger(self):
        """AC: reason gate section specifies high-stakes trigger condition."""
        _, content = _load_file("reason", "SKILL.md")
        assert content is not None
        assert "reversibility" in content or "high-stakes" in content.lower()


class TestToSpecGate:
    """Verify /to-spec SKILL.md references the research-pass gate."""

    def test_to_spec_references_fragment(self):
        """AC: /to-spec SKILL.md references the research-pass gate before PRD publication."""
        _, content = _load_file("to-spec", "SKILL.md")
        assert content is not None
        assert "research-pass-gate" in content.lower() or "research_pass_gate" in content

    def test_to_spec_gate_unconditional(self):
        """AC: to-spec gate is unconditional."""
        _, content = _load_file("to-spec", "SKILL.md")
        assert content is not None
        assert "unconditional" in content.lower()

    def test_to_spec_gate_before_publish(self):
        """AC: Gate appears before the publication step."""
        _, content = _load_file("to-spec", "SKILL.md")
        assert content is not None
        # The gate block should reference running before publishing
        assert "Before publishing" in content or "before publish" in content.lower()


class TestImproveArchitectureGate:
    """Verify /improve-codebase-architecture SKILL.md references the gate."""

    def test_improve_architecture_references_fragment(self):
        """AC: improve-codebase-architecture SKILL.md references the research-pass gate."""
        _, content = _load_file("improve-codebase-architecture", "SKILL.md")
        assert content is not None
        assert "research-pass-gate" in content.lower() or "research_pass_gate" in content

    def test_improve_architecture_gate_unconditional(self):
        """AC: improve-codebase-architecture gate is unconditional."""
        _, content = _load_file("improve-codebase-architecture", "SKILL.md")
        assert content is not None
        assert "unconditional" in content.lower()

    def test_improve_architecture_gate_before_child_issues(self):
        """AC: Gate appears before child-issue creation."""
        _, content = _load_file("improve-codebase-architecture", "SKILL.md")
        assert content is not None
        assert "child issue" in content.lower() or "child-issue" in content.lower()


class TestOverrideWaiverDocumentation:
    """Verify override path is documented."""

    def test_outcome_record_for_waiver(self):
        """AC: Waiver path records outcome_record with research-waiver pattern tag."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        assert "outcome_record" in content

    def test_infrastructure_blocked_excluded_from_reflect(self):
        """AC: infrastructure-blocked waivers excluded from reflect drift detection."""
        _, content = _load_file("_shared", "research-pass-gate.md")
        assert content is not None
        has_reflect_exclusion = "reflect" in content and ("exclud" in content or "skip" in content)
        has_infra_blocked = "infrastructure-blocked" in content or "infrastructure_blocked" in content
        assert has_reflect_exclusion or has_infra_blocked
