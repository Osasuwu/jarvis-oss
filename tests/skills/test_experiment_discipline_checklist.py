"""Experiment discipline carried into /grill and /diagnose (issue #1308).

The three /grill questions are pinned **verbatim** on purpose: the issue body
warns that a fresh session will otherwise rewrite them into generic wording and
they stop catching what they were introduced for. Prose alone does not resist
that; a test does.

Source: research #1248 §AC4, revision #1298, decision
`ed1f5dc9-8e21-4fed-abe2-ceac735182ba`.

Canonical source is `.claude-userlevel/skills/`, never the `~/.claude/skills/`
mirror — the mirror is overwritten by `install.ps1 -Apply`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GRILL_SKILL = REPO_ROOT / ".claude-userlevel" / "skills" / "grill" / "SKILL.md"
DIAGNOSE_SKILL = REPO_ROOT / ".claude-userlevel" / "skills" / "diagnose" / "SKILL.md"

# Both paths point at the canonical source with no mirror fallback: an edit that
# lands only in `~/.claude/skills/` leaves these tests red (AC5).

# Verbatim question texts, exactly as the issue #1308 acceptance criteria carry them.
QUESTION_UNCERTAINTY_AXES = (
    "какие оси неопределённости у этой задачи, какие из них прогон фиксирует, а какие варьирует"
)
QUESTION_UNEXPLAINED_CELLS = "в каких ячейках уже были непонятные результаты и что по ним известно"
QUESTION_SAMPLE_THRESHOLD = (
    "какой порог выборки считается достаточным для вывода — и почему именно он"
)

VERBATIM_QUESTIONS = (
    QUESTION_UNCERTAINTY_AXES,
    QUESTION_UNEXPLAINED_CELLS,
    QUESTION_SAMPLE_THRESHOLD,
)

# The heuristic is one-directional on purpose. Baker Panel (BP Texas City) and
# Dillon & Tinsley: a near-miss is systematically coded as a success and risk
# appetite rises afterwards, so the counter is biased by observer behaviour. A
# run with zero unexplained results is suspicious — that is all this claims.
HEURISTIC_SUSPICIOUS_CLEAN_RUN = "прогон без единого непонятного результата подозрителен"

CHECKLIST_HEADING = "### Experiment-discipline checklist"
PHASE_ONE_HEADING = "## Phase 1 — Build a feedback loop"
NO_LOOP_HEADING = "### When you genuinely cannot build a loop"

NEAR_MISS_TOKENS = ("near-miss", "near miss")
SAFETY_TOKENS = ("безопасн", "safety", "аварийн")


def _normalise(text: str) -> str:
    """Collapse whitespace so markdown line-wrapping cannot break a match."""
    return re.sub(r"\s+", " ", text)


def _sentence_with(text: str, *pattern_groups: tuple[str, ...]) -> bool:
    """True when one single sentence matches every pattern across all groups.

    Sentence scoping is what stops an accidental pass: two claims that happen to
    live in the same section say nothing about each other.
    """
    patterns = [p for group in pattern_groups for p in group]
    return any(
        all(re.search(p, sentence, re.IGNORECASE) for p in patterns)
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text)
    )


def _section(text: str, heading: str) -> str:
    """Return the body of `heading` up to the next same-or-higher-level heading."""
    start = text.find(heading)
    assert start != -1, f"section missing: {heading}"
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[start + len(heading) :]
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


@pytest.fixture(scope="module")
def grill_text() -> str:
    assert GRILL_SKILL.exists(), f"canonical /grill source missing: {GRILL_SKILL}"
    return GRILL_SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def checklist_section(grill_text: str) -> str:
    return _section(grill_text, CHECKLIST_HEADING)


@pytest.fixture(scope="module")
def diagnose_text() -> str:
    assert DIAGNOSE_SKILL.exists(), f"canonical /diagnose source missing: {DIAGNOSE_SKILL}"
    return DIAGNOSE_SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def phase_one_section(diagnose_text: str) -> str:
    return _section(diagnose_text, PHASE_ONE_HEADING)


class TestGrillExperimentDisciplineChecklist:
    """AC1 — three experiment-discipline questions, carried verbatim."""

    @pytest.mark.parametrize("question", VERBATIM_QUESTIONS)
    def test_question_present_verbatim(self, grill_text: str, question: str) -> None:
        assert _normalise(question) in _normalise(grill_text), (
            f"/grill SKILL.md must carry this question verbatim, not paraphrased:\n  {question}"
        )


class TestUnexplainedResultsHeuristic:
    """AC2 — the heuristic ships with question 2, and stays one-directional."""

    def test_heuristic_accompanies_the_question(self, checklist_section: str) -> None:
        normalised = _normalise(checklist_section)
        assert _normalise(HEURISTIC_SUSPICIOUS_CLEAN_RUN) in normalised, (
            "question 2 must be accompanied by the heuristic, verbatim:\n"
            f"  {HEURISTIC_SUSPICIOUS_CLEAN_RUN}"
        )
        assert _normalise(QUESTION_UNEXPLAINED_CELLS) in normalised, (
            "the heuristic must sit in the same section as question 2"
        )

    def test_no_near_miss_to_safety_claim(self, checklist_section: str) -> None:
        """No sentence may tie a near-miss counter to a level of safety.

        Baker Panel and Dillon & Tinsley: the counter is biased by observer
        behaviour, so 'more near-misses reported means safer' does not hold.
        The heuristic survives; the safety claim does not.
        """
        for sentence in re.split(r"(?<=[.!?])\s+|\n", checklist_section):
            lowered = sentence.lower()
            if any(t in lowered for t in NEAR_MISS_TOKENS) and any(
                t in lowered for t in SAFETY_TOKENS
            ):
                pytest.fail(
                    "checklist section must not link near-miss counts to safety "
                    f"level; offending sentence:\n  {sentence.strip()}"
                )


class TestDiagnosePhaseOneExit:
    """AC3 — Phase 1 closes on a reproducible make-it-fail, not on a hunch."""

    def test_make_it_fail_on_demand_is_the_exit(self, phase_one_section: str) -> None:
        assert re.search(r"make it fail on demand", phase_one_section, re.IGNORECASE), (
            "/diagnose Phase 1 must name 'make it fail on demand' as its exit"
        )

    def test_single_command_gates_phase_one_closure(self, phase_one_section: str) -> None:
        """Until one command produces the failure, Phase 1 is not closed."""
        normalised = _normalise(phase_one_section)
        assert re.search(r"single command", normalised, re.IGNORECASE), (
            "Phase 1 exit must be stated as a single command that produces the failure"
        )
        assert re.search(
            r"single command.{0,200}?Phase 1 is not closed",
            normalised,
            re.IGNORECASE,
        ), (
            "Phase 1 must state explicitly that it is not closed until a single "
            "command produces the failure"
        )


class TestDiagnoseUnreproducibleIsAFinding:
    """AC4 — no repro is a finding to record, not a licence to skip ahead."""

    @pytest.fixture(scope="class")
    def no_loop_section(self, diagnose_text: str) -> str:
        return _section(diagnose_text, NO_LOOP_HEADING)

    def test_named_as_a_finding(self, no_loop_section: str) -> None:
        assert re.search(r"\bfinding\b", no_loop_section, re.IGNORECASE), (
            "failure to reproduce must be named a finding, not just an obstacle"
        )

    def test_finding_is_recorded(self, no_loop_section: str) -> None:
        """Recording must attach to the finding — 'screen recording' is not it."""
        assert _sentence_with(
            no_loop_section,
            (r"\brecord\b", r"\bfinding\b"),
        ), "the section must say the finding is recorded, not merely that it exists"

    def test_not_a_licence_to_enter_phase_two(self, no_loop_section: str) -> None:
        """Tied to non-reproduction, not the generic 'need a loop first' gate."""
        assert _sentence_with(
            no_loop_section,
            (r"Phase 2", r"\breproduc|\bfinding\b"),
            (r"\bnot\b|\bnever\b",),
        ), "the section must state that failure to reproduce is not a reason to move on to Phase 2"
