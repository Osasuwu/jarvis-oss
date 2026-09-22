"""Contract test for the review-doc skill (#59).

The skill is only worth trusting if its reviewers are independent and its verdicts rest on
fetched evidence. These are the lines that make it so; an edit that drops one fails the build.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent / ".agents" / "skills" / "review-doc"
RULES = SKILL_DIR / "calibration" / "RULES.md"


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


def test_how_to_choose_findings_carry_a_severity():
    # #87: unranked pass 3 findings gave each doc 10-15 fixes and no point where it was done.
    # #102: the severity rule itself lives in the rules file; the skill only points there.
    text = " ".join(_skill_text().split())
    assert "Mark each finding `blocking` or `follow-up` by the rules file's" in text


def _pass(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _anchor(heading: str) -> str:
    # GitHub's heading anchors: lower case, punctuation other than '-' and ' ' dropped,
    # spaces to '-'.
    slug = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return slug.replace(" ", "-")


def test_labels_point_at_the_rules_file_and_resolve():
    # #102: `blocking` and `unverifiable` have one definition, in RULES.md.
    text = _skill_text()
    rules = RULES.read_text(encoding="utf-8")
    anchors = {_anchor(h) for h in re.findall(r"^## (.+)$", rules, re.M)}
    links = re.findall(r"\]\(calibration/RULES\.md#([^)]+)\)", text)
    assert "blocking-or-follow-up" in links
    assert "unverifiable" in links
    for link in links:
        assert link in anchors, f"SKILL.md links RULES.md#{link}, which has no such heading"


def test_skill_carries_no_second_definition_of_the_labels():
    # #102: wording the rules file replaced. If it comes back, the two files can diverge again.
    text = " ".join(_skill_text().split())
    for phrase in (
        "source unreachable, paywalled, or the claim names no source",
        "one left in that it cannot build, nothing left with no pointer",
        "If you cannot name the setup, or quote two sides that cannot both be true",
        "`follow-up` — everything else",
        "Report `fails value test`; it is `blocking`.",
    ):
        assert phrase not in text, phrase


def test_pass_3_writes_reasoning_before_verdict():
    # #102: a label written first is then argued for.
    pass3 = " ".join(_pass(_skill_text(), "## Pass 3").split())
    assert pass3.index("**Reasoning, then verdict.**") < pass3.index("**Severity.**")
    assert "Write each finding's reasoning before its label" in pass3


def test_report_template_puts_reasoning_before_verdict():
    report = _skill_text().split("## The report", 1)[1]
    template = report.split("```", 2)[1]
    lines = {line.split(":**", 1)[0]: line for line in template.splitlines() if ":**" in line}
    how = lines["**How to choose (N blocking, N follow-up)"]
    assert how.index("<setup and what goes wrong for it") < how.index("blocking | follow-up")
    value = lines["**Value test"]
    assert value.index("<setup 1") < value.index("passes | fails")
    for key in ("**Missing options (N blocking, N follow-up)",
                "**Unverifiable (N blocking, N follow-up)"):
        line = lines[key]
        assert line.rstrip().endswith("blocking | follow-up"), key


def test_report_has_a_fix_induced_line():
    # #102: /doc-loop reads it for its stop message.
    report = _skill_text().split("## The report", 1)[1]
    template = report.split("```", 2)[1]
    assert "**Fix-induced (N):** <finding IDs>" in template
    assert "**Fix-induced (0):** none" in report


def test_pass_3_walks_the_four_reader_types_and_the_attended_axis():
    pass3 = " ".join(_pass(_skill_text(), "## Pass 3").split())
    for reader in (
        "solo, without money;",
        "solo, with money;",
        "a team of up to three, without money;",
        "a team of up to three, with money.",
    ):
        assert reader in pass3, reader
    assert "**attended**" in pass3 and "**unattended**" in pass3


def test_calibration_record_keeps_old_runs_under_history_as_the_old_scheme():
    # #102 changed SKILL.md, whose hash is in the drift key; #106 recalibrated on the corpus and
    # moved the seeded runs under "History", marked as measured under the old scheme.
    text = (SKILL_DIR / "CALIBRATION.md").read_text(encoding="utf-8")
    current, history = text.split("\n## History\n", 1)
    assert "## Calibration 1" in current and "(#106)" in current
    assert "### Run 1 — 2026-09-17" in history and "## Run 1" not in current
    assert "measured under the old scheme" in " ".join(history.split())


def test_fix_commits_get_a_delta_pass():
    # #87: a fix is new text; the delta pass reviews it, including files the diff did not touch.
    text = _skill_text()
    delta = text.split("## Delta pass — a fix commit", 1)[1].split("\n## ", 1)[0]
    assert "not the session that made the fix" in delta
    assert "`git diff <reviewed commit>..<fix commit>`" in delta
    assert "search the whole repo for its old wording" in delta
