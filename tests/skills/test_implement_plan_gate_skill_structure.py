"""Test suite for /implement's interactive plan-gate wiring (#1688).

Verifies the skill-contract prose actually references the trigger-evaluation
module (agents.implement_plan_gate.evaluate_trigger) rather than describing
a parallel, undocumented mechanism — the docs-side half of AC1/AC2/AC3/AC5.
The code-side behavior (classification, carve-out, requires_plan) was covered
by tests/reactive_core/test_implement_plan_gate.py, demolished with
reactive-core in #1802; this suite only checks that SKILL.md's prose is wired
to that module, not that the module's logic is correct.
"""

import re
from pathlib import Path


class TestImplementPlanGateSkillStructure:
    """Verify plan-gate wiring in /implement SKILL.md."""

    @classmethod
    def setup_class(cls):
        repo_candidates = [
            Path(__file__).parent.parent.parent
            / ".claude-userlevel"
            / "skills"
            / "implement"
            / "SKILL.md",
        ]
        mirror_candidates = [
            Path.home() / ".claude" / "skills" / "implement" / "SKILL.md",
        ]

        cls.skill_path = None
        cls.skill_content = None

        for candidate in repo_candidates:
            if candidate.exists():
                cls.skill_path = candidate
                with open(candidate, "r", encoding="utf-8") as f:
                    cls.skill_content = f.read()
                break

        if cls.skill_content is None:
            for candidate in mirror_candidates:
                if candidate.exists():
                    cls.skill_path = candidate
                    with open(candidate, "r", encoding="utf-8") as f:
                        cls.skill_content = f.read()
                    break

        if cls.skill_content is None:
            import os

            if "CLAUDE_SKILL_PATH" in os.environ:
                skill_path = Path(os.environ["CLAUDE_SKILL_PATH"])
                if skill_path.exists():
                    cls.skill_path = skill_path
                    with open(skill_path, "r", encoding="utf-8") as f:
                        cls.skill_content = f.read()

        assert cls.skill_content is not None, (
            f"Could not find /implement SKILL.md. Checked {repo_candidates} and {mirror_candidates}"
        )

    # ── AC1: trigger evaluation is wired via the shared module, not reinvented ──

    def test_references_evaluate_trigger(self):
        assert "evaluate_trigger" in self.skill_content, (
            "SKILL.md must reference agents.implement_plan_gate.evaluate_trigger "
            "— the shared trigger-evaluation entry point, not a parallel mechanism"
        )

    def test_references_implement_plan_gate_module(self):
        assert "agents.implement_plan_gate" in self.skill_content or re.search(
            r"agents/implement_plan_gate", self.skill_content
        ), "SKILL.md must name the agents.implement_plan_gate module it calls"

    def test_runs_before_implementation_section(self):
        """The plan-gate step must appear before '### 4. Implement' in the file
        — AC2's ordering requirement (plan written before the first edit)."""
        gate_pos = self.skill_content.find("evaluate_trigger")
        implement_pos = self.skill_content.find("### 4. Implement")
        assert gate_pos != -1 and implement_pos != -1
        assert gate_pos < implement_pos, (
            "the plan-gate step must be wired before '### 4. Implement' so the "
            "locked plan is written before any code edit"
        )

    # ── AC2: planner invocation + plan written to issue body before first edit ──

    def test_references_planner_subagent(self):
        assert re.search(r'subagent_type[:=]\s*"?planner"?', self.skill_content), (
            "SKILL.md must document invoking the Agent tool with "
            'subagent_type: "planner" when a plan is required'
        )

    def test_documents_writing_plan_before_first_edit(self):
        assert re.search(
            r"before.{0,40}(first|any).{0,20}edit", self.skill_content, re.IGNORECASE
        ), "SKILL.md must state the locked plan is written before the first/any edit"

    # ── AC3: priority:critical carve-out is documented ──────────────────────────

    def test_documents_priority_critical_carve_out(self):
        assert "priority:critical" in self.skill_content
        assert re.search(
            r"priority:critical.{0,400}(skip|carve-out|carve out)",
            self.skill_content,
            re.IGNORECASE | re.DOTALL,
        ), "SKILL.md must document that priority:critical skips only the plan requirement"

    # ── AC5: operator decides on unresolved blocking objection ─────────────────

    def test_documents_operator_decides_on_unresolved_objection(self):
        assert re.search(r"unresolved.{0,80}object", self.skill_content, re.IGNORECASE), (
            "SKILL.md must describe the unresolved-blocking-objection case"
        )
        assert re.search(
            r"operator decides|principal.{0,40}decide", self.skill_content, re.IGNORECASE
        ), "SKILL.md must state the operator/principal decides on an unresolved objection"
