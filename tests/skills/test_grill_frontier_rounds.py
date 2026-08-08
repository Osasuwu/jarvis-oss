"""Test suite for /grill Phase 1/2 reshape (issue #1413).

Reverses the one-question-at-a-time cadence adopted in #1148 (decision `30f52b3a`)
with dependency-gated frontier rounds, and reshapes Phase 1 from an
assumption-verbalization confirmation gate into a short session-parameter gate.
Design resolved by decision episode `47bfe29d-21db-46af-b665-d9685b2b20b7`.

Phase 3/4 CRITIC machinery is explicitly out of scope — see
test_grill_critic_subagent.py for that coverage. Third-person reviewer framing and
decision UUID 316c5911-9f06-44de-8f99-20fe3e9fa448 (anti-sycophancy, #689) are
unaffected by this issue and remain covered by test_grill_skill_structure.py.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
SKILL_MD = REPO_ROOT / ".claude-userlevel" / "skills" / "grill" / "SKILL.md"
CREDIT_MD = REPO_ROOT / ".claude-userlevel" / "skills" / "AIHERO_CREDIT.md"


def _read(path: Path) -> str:
    assert path.exists(), f"Required file not found: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_md() -> str:
    return _read(SKILL_MD)


@pytest.fixture(scope="module")
def credit_md() -> str:
    return _read(CREDIT_MD)


@pytest.fixture(scope="module")
def phase1_section(skill_md: str) -> str:
    """Isolate Phase 1's body: from its heading up to the next '### Phase' heading."""
    match = re.search(
        r"###\s*Phase\s*1[^\n]*\n(.*?)(?=\n###\s*Phase\s*2|\Z)",
        skill_md,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "Phase 1 section must exist in SKILL.md"
    return match.group(1)


@pytest.fixture(scope="module")
def phase2_section(skill_md: str) -> str:
    """Isolate Phase 2's body: from its heading up to the next '### Phase 3' heading."""
    match = re.search(
        r"###\s*Phase\s*2[^\n]*\n(.*?)(?=\n###\s*Phase\s*3|\Z)",
        skill_md,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "Phase 2 section must exist in SKILL.md"
    return match.group(1)


class TestPhase1SessionParameterGate:
    """Phase 1 reshaped from assumption-verbalization into a session-parameter gate."""

    def test_phase1_heading_is_session_parameter_gate(self, skill_md: str):
        assert re.search(r"###\s*Phase\s*1[^\n]*session[- ]param", skill_md, re.IGNORECASE), \
            "Phase 1 heading must name it a session-parameter gate"

    def test_phase1_no_longer_named_assumption_verbalization(self, skill_md: str):
        assert not re.search(r"###\s*Phase\s*1[^\n]*assumption", skill_md, re.IGNORECASE), \
            "Phase 1 heading must no longer be 'Assumption Verbalization'"

    def test_phase1_sources_expertise_from_memory_not_restated(self, phase1_section: str):
        assert re.search(r"owner_competence_profile", phase1_section), \
            "Phase 1 must cite owner_competence_profile as the source of expertise/context, not restate it"
        assert re.search(r"not restat|no longer restat|without restat", phase1_section, re.IGNORECASE), \
            "Phase 1 must explicitly say expertise/context familiarity are not restated"

    def test_phase1_no_confirmation_gate_language(self, phase1_section: str):
        assert not re.search(r"confirm|off base|wrong about", phase1_section, re.IGNORECASE), \
            "Phase 1 must not run a confirm/correct assumption-verbalization gate"

    def test_phase1_asks_time_budget(self, phase1_section: str):
        assert re.search(r"time budget", phase1_section, re.IGNORECASE), \
            "Phase 1 must ask for time budget"

    def test_phase1_asks_decision_stage(self, phase1_section: str):
        assert re.search(r"decision stage", phase1_section, re.IGNORECASE), \
            "Phase 1 must ask for decision stage"

    def test_phase1_asks_cadence_with_frontier_default_and_single_question_fallback(self, phase1_section: str):
        assert re.search(r"cadence", phase1_section, re.IGNORECASE), \
            "Phase 1 must ask for cadence"
        assert re.search(r"frontier.*default|default.*frontier", phase1_section, re.IGNORECASE), \
            "Phase 1 must state frontier rounds is the default cadence"
        assert re.search(r"single[- ]question", phase1_section, re.IGNORECASE), \
            "Phase 1 must offer single-question cadence for irreversible branches"
        assert re.search(r"irreversible", phase1_section, re.IGNORECASE), \
            "Phase 1 must scope single-question cadence to irreversible branches"


class TestPhase2FrontierRounds:
    """Phase 2's one-question-at-a-time rule replaced by dependency-gated frontier rounds."""

    def test_one_question_at_a_time_rule_removed(self, phase2_section: str):
        assert not re.search(r"one question at a time", phase2_section, re.IGNORECASE), \
            "Phase 2 must no longer state the one-question-at-a-time rule"

    def test_frontier_rounds_named(self, phase2_section: str):
        assert re.search(r"frontier round", phase2_section, re.IGNORECASE), \
            "Phase 2 must name dependency-gated frontier rounds"

    def test_frontier_recomputed_as_answers_land(self, phase2_section: str):
        assert re.search(r"frontier.*recomput|recomput.*frontier", phase2_section, re.IGNORECASE), \
            "Phase 2 must state the frontier is recomputed as answers land"

    def test_questions_asked_together_in_numbered_round(self, phase2_section: str):
        assert re.search(r"numbered round|together in one round|one numbered round", phase2_section, re.IGNORECASE), \
            "Phase 2 must state prerequisite-settled questions are asked together in one numbered round"

    def test_questions_tagged_design_forming_or_refining(self, phase2_section: str):
        assert "design-forming" in phase2_section, \
            "Phase 2 must tag questions 'design-forming'"
        assert "refining" in phase2_section, \
            "Phase 2 must tag questions 'refining'"

    def test_why_is_prerequisite_of_how_within_branch(self, phase2_section: str):
        assert re.search(r"WHY.*prerequisite.*HOW", phase2_section, re.IGNORECASE), \
            "Phase 2 must declare WHY a prerequisite of HOW within a branch"
        assert re.search(r"branch", phase2_section, re.IGNORECASE), \
            "Phase 2 must scope the WHY/HOW prerequisite rule to a branch"

    def test_thin_design_forming_answer_triggers_followup(self, phase2_section: str):
        assert re.search(r"thin.*design-forming|design-forming.*thin", phase2_section, re.IGNORECASE), \
            "Phase 2 must state a thin/ambiguous design-forming answer triggers a follow-up"
        assert re.search(r"individual follow-?up", phase2_section, re.IGNORECASE), \
            "Phase 2 must state the follow-up is individual, before the round closes"

    def test_terse_refining_answer_accepted(self, phase2_section: str):
        assert re.search(r"terse.*refining|refining.*terse", phase2_section, re.IGNORECASE), \
            "Phase 2 must state a terse refining answer is accepted as-is"

    def test_bare_agreement_not_a_trigger(self, phase2_section: str):
        assert re.search(r"bare agreement", phase2_section, re.IGNORECASE), \
            "Phase 2 must state bare agreement with a recommended answer is legitimate"
        assert re.search(r"not.*(itself\s+)?a\s+trigger|never.*trigger", phase2_section, re.IGNORECASE), \
            "Phase 2 must state bare agreement is not itself a follow-up trigger"

    def test_no_numeric_cap_design_forming_ordered_first(self, phase2_section: str):
        assert re.search(r"no numeric cap", phase2_section, re.IGNORECASE), \
            "Phase 2 must state there is no numeric cap on round size"
        assert re.search(r"design-forming.*(ordered|first)", phase2_section, re.IGNORECASE), \
            "Phase 2 must state design-forming questions are ordered first when the frontier is large"

    def test_recommended_answer_still_required(self, phase2_section: str):
        assert re.search(r"recommended answer", phase2_section, re.IGNORECASE), \
            "Phase 2 must still require each question to carry a recommended answer"

    def test_round_summary_two_line_ceiling(self, phase2_section: str):
        assert re.search(r"two lines?", phase2_section, re.IGNORECASE), \
            "Phase 2 must cap the round-closing summary at two lines"
        assert re.search(r"hard ceiling", phase2_section, re.IGNORECASE), \
            "Phase 2 must state the two-line cap is a hard ceiling"
        assert re.search(r"settl", phase2_section, re.IGNORECASE) and re.search(r"next frontier", phase2_section, re.IGNORECASE), \
            "Phase 2 round summary must cover settled + next frontier"

    def test_two_line_ceiling_is_display_only(self, phase2_section: str):
        assert re.search(r"display", phase2_section, re.IGNORECASE), \
            "Phase 2 must state the two-line ceiling is a display-only ceiling"
        assert re.search(r"record_decision", phase2_section), \
            "Phase 2 must state record_decision emission is uncapped despite the display ceiling"
        assert re.search(r"CONTEXT\.md", phase2_section), \
            "Phase 2 must state inline CONTEXT.md capture is uncapped despite the display ceiling"
        assert re.search(r"uncapped|not capped", phase2_section, re.IGNORECASE), \
            "Phase 2 must explicitly state record_decision/CONTEXT.md capture stay uncapped"

    def test_answer_conflict_reopens_earlier_question(self, phase2_section: str):
        assert re.search(r"contradict", phase2_section, re.IGNORECASE), \
            "Phase 2 must state a contradicting later answer triggers reopening"
        assert re.search(r"reopen", phase2_section, re.IGNORECASE), \
            "Phase 2 must use the word 'reopen' for the answer-vs-answer conflict rule"
        assert re.search(r"glossary", phase2_section, re.IGNORECASE) and re.search(r"code", phase2_section, re.IGNORECASE), \
            "Phase 2 must tie the conflict rule to the existing glossary/code contradiction reflexes"

    def test_co_presence_addresses_late_interdependence(self, phase2_section: str):
        assert re.search(r"co-presence", phase2_section, re.IGNORECASE), \
            "Phase 2 must name co-presence of answers in one message"
        assert re.search(r"late interdependence", phase2_section, re.IGNORECASE), \
            "Phase 2 must name late interdependence as the problem co-presence addresses"
        assert re.search(r"context-switch", phase2_section, re.IGNORECASE), \
            "Phase 2 must distinguish co-presence (late interdependence) from rounds (context-switching tax)"

    def test_confirmation_gate_stays_per_phase_not_per_round(self, phase2_section: str):
        assert re.search(r"per[- ]phase", phase2_section, re.IGNORECASE), \
            "Phase 2 must state the confirmation gate is per-phase"
        assert re.search(r"not per[- ]round|never per[- ]round", phase2_section, re.IGNORECASE), \
            "Phase 2 must explicitly state the confirmation gate is not per-round"

    def test_facts_vs_decisions_split_present(self, phase2_section: str):
        assert re.search(r"[Ff]acts vs decisions split", phase2_section), \
            "Phase 2 must keep the facts-vs-decisions split"

    def test_lookup_question_wastes_round_slot(self, phase2_section: str):
        assert re.search(r"wastes? a round slot|round slot", phase2_section, re.IGNORECASE), \
            "Phase 2 must note a lookup question wastes a round slot"

    def test_decision_uuid_1413_referenced(self, skill_md: str):
        assert "47bfe29d-21db-46af-b665-d9685b2b20b7" in skill_md, \
            "SKILL.md must reference decision UUID 47bfe29d-21db-46af-b665-d9685b2b20b7 (issue #1413)"


class TestExperimentDisciplineChecklistWording:
    """'One at a time' wording replaced by 'carried verbatim into the round'."""

    def test_one_at_a_time_wording_removed_from_checklist(self, skill_md: str):
        checklist = re.search(
            r"###\s*Experiment-discipline checklist(.*?)(?=\n###|\Z)",
            skill_md,
            re.IGNORECASE | re.DOTALL,
        )
        assert checklist is not None, "Experiment-discipline checklist section must exist"
        assert not re.search(r"one at a time", checklist.group(1), re.IGNORECASE), \
            "Experiment-discipline checklist must no longer say 'one at a time'"
        assert re.search(r"carried verbatim into the round", checklist.group(1), re.IGNORECASE), \
            "Experiment-discipline checklist must say 'carried verbatim into the round'"

    def test_three_questions_stay_verbatim(self, skill_md: str):
        assert "какие оси неопределённости у этой задачи" in skill_md
        assert "в каких ячейках уже были непонятные результаты" in skill_md
        assert "какой порог выборки считается достаточным" in skill_md


class TestPhase3Phase4Unchanged:
    """Phase 3/4 CRITIC machinery must remain untouched by this issue."""

    def test_phase3_heading_present(self, skill_md: str):
        assert re.search(r"###\s*Phase\s*3", skill_md, re.IGNORECASE), \
            "Phase 3 heading must still be present"

    def test_phase4_heading_present(self, skill_md: str):
        assert re.search(r"###\s*Phase\s*4", skill_md, re.IGNORECASE), \
            "Phase 4 heading must still be present"

    def test_phase3_trigger_decision_uuid_still_present(self, skill_md: str):
        assert "c29c2b00-e9e1-43d1-93ff-ada5820c434c" in skill_md

    def test_phase4_trigger_decision_uuid_still_present(self, skill_md: str):
        assert "a5e76208-8636-4c80-9423-98e63981c903" in skill_md


class TestAiheroCreditUpdated:
    """AIHERO_CREDIT.md reflects the frontier-round adoption."""

    def test_grill_row_no_longer_says_one_question_at_a_time(self, credit_md: str):
        grill_row = re.search(r"\|\s*`grill`\s*\|.*\n", credit_md)
        assert grill_row is not None, "grill row must exist in AIHERO_CREDIT.md"
        assert "one-question-at-a-time" not in grill_row.group(0), \
            "grill row must no longer describe one-question-at-a-time as adopted behavior"

    def test_grilling_row_moved_out_of_not_adopted(self, credit_md: str):
        not_adopted = re.search(
            r"## Deliberately not adopted(.*?)(?=\n## |\Z)",
            credit_md,
            re.DOTALL,
        )
        assert not_adopted is not None, "Deliberately not adopted section must exist"
        assert "productivity/grilling" not in not_adopted.group(1), \
            "productivity/grilling row must no longer sit in 'Deliberately not adopted'"

    def test_grilling_partial_adoption_noted(self, credit_md: str):
        assert re.search(r"frontier-round engine", credit_md, re.IGNORECASE), \
            "AIHERO_CREDIT.md must note the frontier-round engine was adopted"
        assert re.search(r"skill-split", credit_md, re.IGNORECASE), \
            "AIHERO_CREDIT.md must note the skill-split itself was not adopted"
