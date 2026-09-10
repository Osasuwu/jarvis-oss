"""Golden-text regression test for the AFK-fit SKILL.md prose (#1708 AC7).

The Q2/Q3 -> afk:2-plan-without-automation-queue-label outcome is driven by LLM
judgement reading skill prose, not by executable Python logic —
classify_static_paths() only covers the static Q1 half (unit-tested directly in
test_to_tickets_afk_fit.py). This test is the runnable check for the
documented contract: it fails if the SKILL.md prose regresses to the old
binary AFK-yes/no framing, or drops the two-writer discipline sentence.

Post-#1794 the queue mechanism is referred to as "the automation-queue label"
rather than the retired `sandcastle` literal — these assertions were updated
to the new phrase while preserving the original structural checks.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TO_TICKETS_SKILL = _REPO_ROOT / ".claude-userlevel" / "skills" / "to-tickets" / "SKILL.md"
_TRIAGE_SKILL = _REPO_ROOT / ".claude-userlevel" / "skills" / "triage" / "SKILL.md"


def test_to_tickets_skill_documents_three_class_outcomes():
    text = _TO_TICKETS_SKILL.read_text(encoding="utf-8")
    assert "classify_static_paths" in text
    assert "afk:2-plan" in text
    assert "afk:3-human" in text
    assert "WITHOUT" in text and "automation-queue label" in text
    # #1794 AC1: the retired sandcastle literal must not reappear
    assert "sandcastle" not in text.lower()


def test_to_tickets_skill_ties_afk_2_plan_to_q2_and_q3():
    text = _TO_TICKETS_SKILL.read_text(encoding="utf-8")
    q2_idx = text.index("**Q2")
    q3_idx = text.index("**Q3")
    q4_idx = text.index("**Q4")
    between_q2_and_q4 = text[q2_idx:q4_idx]
    assert "afk:2-plan" in between_q2_and_q4
    assert "WITHOUT" in between_q2_and_q4 and "automation-queue label" in between_q2_and_q4
    assert q2_idx < q3_idx < q4_idx


def test_to_tickets_skill_ties_afk_3_human_to_hitl():
    text = _TO_TICKETS_SKILL.read_text(encoding="utf-8")
    q1_idx = text.index("**Q1")
    q2_idx = text.index("**Q2")
    between_q1_and_q2 = text[q1_idx:q2_idx]
    assert "afk:3-human" in between_q1_and_q2
    assert "hitl" in between_q1_and_q2


def test_to_tickets_skill_has_writer_discipline_sentence():
    text = _TO_TICKETS_SKILL.read_text(encoding="utf-8")
    assert "two writers" in text
    assert "/to-tickets" in text and "/triage" in text
    assert "plan:locked" in text
    assert "needs-plan" in text
    assert "#1691" in text


def test_triage_skill_references_classify_static_paths():
    text = _TRIAGE_SKILL.read_text(encoding="utf-8")
    assert "classify_static_paths" in text
    assert "to_tickets_afk_fit" in text


def test_triage_skill_has_writer_discipline_sentence():
    text = _TRIAGE_SKILL.read_text(encoding="utf-8")
    assert "two writers" in text
    assert "plan:locked" in text
    assert "needs-plan" in text
    assert "#1691" in text


def test_triage_skill_no_longer_uses_vague_shared_gate_prose():
    text = _TRIAGE_SKILL.read_text(encoding="utf-8")
    assert "same** AFK-fit gate" not in text
