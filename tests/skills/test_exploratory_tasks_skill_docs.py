"""Golden-text regression test for the "Exploratory tasks" SKILL.md section (#1752).

/implement dispatches exploratory work (hypothesis + pre-registered acceptance
criterion) by task type, never by repo path (decision b760edd2). AFK-eligibility
for such slices is gated on an objective machine-checkable oracle drawn from a
named vocabulary (decision b9c78373, SLR arxiv:1804.01954), and a negative
result must leave a journal entry (arxiv:2506.16051). This is the runnable
check for that prose: it fails if the section regresses to a repo-path
dispatch, drops the oracle vocabulary, or drops the negative-result rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENT_SKILL = REPO_ROOT / ".claude-userlevel" / "skills" / "implement" / "SKILL.md"

HEADING = "## Exploratory tasks"

# Oracle vocabulary per SLR arxiv:1804.01954, memory 36b3d117 §5.
ORACLE_TERMS = (
    "pseudo-oracle",
    "analytical solution",
    "metamorphic relation",
    "property invariant",
    "golden run",
)

# Repo-specific path fragments that must never appear in the new section —
# decision b760edd2: skills stay issue-agnostic, dispatch is by task type.
REPO_SPECIFIC_PATH_TOKENS = (
    "docs/research/",
    "driver/",
    "planning/",
    "mujoco/",
    "redrobot",
)


def _normalise(text: str) -> str:
    """Collapse whitespace so markdown line-wrapping cannot break a match."""
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    """Return the body of `heading` up to the next same-or-higher-level heading."""
    start = text.find(heading)
    assert start != -1, f"section missing: {heading}"
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[start + len(heading) :]
    nxt = re.search(rf"^#{{1,{level}}} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert IMPLEMENT_SKILL.exists(), f"canonical /implement source missing: {IMPLEMENT_SKILL}"
    return IMPLEMENT_SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exploratory_section(skill_text: str) -> str:
    return _section(skill_text, HEADING)


class TestExploratoryTasksSectionExists:
    """AC1 — exactly one section, dispatched by task type, zero repo-specific paths."""

    def test_heading_present_exactly_once(self, skill_text: str) -> None:
        assert skill_text.count(HEADING) == 1, (
            "SKILL.md must have exactly one '## Exploratory tasks' section"
        )

    def test_dispatch_is_by_task_type_not_path(self, exploratory_section: str) -> None:
        normalised = _normalise(exploratory_section).lower()
        assert "task type" in normalised or "type of task" in normalised, (
            "dispatch trigger must be stated as task type"
        )

    def test_hypothesis_and_preregistered_criterion_named(self, exploratory_section: str) -> None:
        normalised = _normalise(exploratory_section).lower()
        assert "hypothesis" in normalised, "section must name the hypothesis framing"
        assert re.search(r"pre-?registered", normalised), (
            "section must require the acceptance criterion be pre-registered before the run"
        )

    def test_no_repo_specific_paths(self, exploratory_section: str) -> None:
        lowered = exploratory_section.lower()
        for token in REPO_SPECIFIC_PATH_TOKENS:
            assert token.lower() not in lowered, (
                f"section must stay issue-agnostic (decision b760edd2); "
                f"found repo-specific token: {token!r}"
            )


class TestAfkFitOracleCriterion:
    """AC2 — AFK-eligible ⇔ objective machine-checkable oracle; vocabulary listed."""

    def test_afk_eligible_iff_objective_oracle_statement(self, exploratory_section: str) -> None:
        normalised = _normalise(exploratory_section).lower()
        assert re.search(
            r"afk[- ]eligible.{0,80}(objective|machine[- ]check)",
            normalised,
        ), "section must state AFK-eligible ⇔ objective machine-checkable oracle"

    @pytest.mark.parametrize("term", ORACLE_TERMS)
    def test_oracle_vocabulary_term_present(self, exploratory_section: str, term: str) -> None:
        assert term.lower() in exploratory_section.lower(), (
            f"oracle vocabulary must include: {term}"
        )

    def test_no_oracle_from_vocabulary_means_interactive(self, exploratory_section: str) -> None:
        normalised = _normalise(exploratory_section).lower()
        assert "interactive" in normalised, (
            "section must state that a slice with no oracle from the vocabulary is interactive"
        )


class TestNegativeResultJournalRule:
    """AC3 — negative result → journal entry is mandatory."""

    def test_negative_result_journal_rule_stated(self, exploratory_section: str) -> None:
        normalised = _normalise(exploratory_section).lower()
        assert re.search(
            r"negative result.{0,120}(must|mandatory|required).{0,60}(journal|log|record)",
            normalised,
        ) or re.search(
            r"(must|mandatory|required).{0,60}(journal|log|record).{0,120}negative result",
            normalised,
        ), "section must mandate a journal/log entry for a negative experimental result"


class TestExistingSkillBranchesUnmodified:
    """AC4 — existing non-exploratory branches of the skill are untouched."""

    def test_dispatch_contract_table_unchanged(self, skill_text: str) -> None:
        assert "mechanical-mode" in skill_text
        assert "grill_required" in skill_text
        assert "TDD-mode" in skill_text

    def test_pipeline_numbered_sections_still_present(self, skill_text: str) -> None:
        for heading in (
            "### 1. Pre-flight checks",
            "### 2. Fetch & analyze",
            "### 3. Claim, branch, log the decision",
            "### 4. Implement",
            "### 5. Commit & PR",
            "### 6. Log the outcome",
        ):
            assert heading in skill_text, f"existing pipeline section must survive: {heading}"
