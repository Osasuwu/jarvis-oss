"""Contract test for the review-doc skill (#59).

The skill is only worth trusting if its reviewers are independent and its verdicts rest on
fetched evidence. These are the lines that make it so; an edit that drops one fails the build.
"""

from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent / ".agents" / "skills" / "review-doc"


def _skill_text() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def test_skill_names_itself():
    text = _skill_text()
    assert text.startswith("---\n")
    assert "name: review-doc" in text


def test_writer_session_may_not_review():
    assert "**Not the writer.**" in _skill_text()


def test_verdicts_rest_on_fetched_evidence():
    assert "**Fetch, never recall.**" in _skill_text()


def test_completeness_pass_is_blind():
    text = _skill_text()
    assert "## Pass 2 — completeness, blind" in text
    assert "It must not see the option list" in text


def test_report_puts_human_reading_list_first():
    text = _skill_text()
    report = text.split("## The report", 1)[1]
    assert report.index("**Read these closely") < report.index("**Mismatches")


def test_calibration_record_exists():
    assert (SKILL_DIR / "CALIBRATION.md").is_file()


def test_how_to_choose_may_leave_several_options():
    # #64: a pass that asks setups to land on one option drove six rounds of tie-breaks.
    text = " ".join(_skill_text().split())
    assert "More than one option fitting a setup is intended, not a defect." in text
    assert "the step that decides" not in text
