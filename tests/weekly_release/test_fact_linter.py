"""#1572 AC6 - fact-anchoring linter: every note line must cite >=1 PR/issue
number pulled from the collected window; a quantitative claim with no such
citation is rejected. Language-independent (checks citation presence, not
banned words), so the same rule covers the skill's ru and en <details> body.
"""

from __future__ import annotations

from scripts.weekly_release_engine import lint_release_notes

WINDOW_REFS = {"1597", "1600", "1601", "1602", "1604", "1612"}


def test_line_with_valid_window_citation_passes():
    lines = ["- Daily digest gained a schema v2 (#1597)."]
    assert lint_release_notes(lines, WINDOW_REFS) == []


def test_line_with_no_citation_at_all_is_rejected():
    lines = ["- Assorted cleanup and polish."]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1
    assert "missing PR/issue citation" in violations[0]


def test_line_citing_a_number_outside_the_window_is_rejected():
    # #9999 is not in this window's ref set - a stale/foreign citation must
    # not pass just because it looks like a citation.
    lines = ["- Fixed a bug (#9999)."]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1
    assert "missing PR/issue citation" in violations[0]


def test_quantitative_claim_without_citation_gets_specific_message():
    lines = ["- Retries went from 3 attempts to 5."]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1
    assert "quantitative claim without a source citation" in violations[0]


def test_quantitative_claim_with_valid_citation_passes():
    lines = ["- Retries went from 3 attempts to 5 (#1602)."]
    assert lint_release_notes(lines, WINDOW_REFS) == []


def test_language_independent_ru_line_same_rule():
    lines_pass = ["- Добавлена схема v2 для дайджеста (#1597)."]
    lines_fail = ["- Добавлена схема v2 для дайджеста."]
    assert lint_release_notes(lines_pass, WINDOW_REFS) == []
    assert len(lint_release_notes(lines_fail, WINDOW_REFS)) == 1


def test_multiple_lines_report_one_violation_each():
    lines = [
        "- Cited fix (#1600).",
        "- Uncited claim.",
        "- Another cited fix (#1601).",
    ]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1
    assert "line 1" in violations[0]


# -- #1668: structural lines are exempt from the citation check --------------


def test_blank_line_is_not_flagged():
    assert lint_release_notes([""], WINDOW_REFS) == []


def test_atx_header_line_is_not_flagged():
    assert lint_release_notes(["## Осталось"], WINDOW_REFS) == []


def test_bare_details_and_summary_tags_are_not_flagged():
    lines = ["<details>", "<summary>", "</summary>", "</details>"]
    assert lint_release_notes(lines, WINDOW_REFS) == []


def test_summary_tag_with_inline_content_is_still_linted():
    # Only a *bare* <summary>/<details> tag is exempt - one carrying its own
    # prose content is still a content line and must cite a window ref.
    lines = ["<summary>3 issues left uncited</summary>"]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1


def test_structural_lines_mixed_with_content_only_flag_content():
    lines = [
        "## Осталось",
        "",
        "- Cited fix (#1600).",
        "- Uncited claim.",
    ]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1
    assert "line 3" in violations[0]


# -- remaining_refs: remaining_section cites open-issue numbers, which are
# categorically outside window_refs (merged/closed only) - found via e2e test
# against redrobot, where every remaining_section line failed lint by
# construction. SKILL.md Step 3 lints remaining_section against
# window_refs | repo_result.remaining_refs, never window_refs alone.


def test_open_issue_citation_fails_against_window_refs_alone():
    # #2014 is an open milestone issue, not a merged/closed window entry -
    # it can never be in window_refs, so citing it there is rejected.
    lines = ["- Restore probe still returns a hardcoded slot (#2014)."]
    violations = lint_release_notes(lines, WINDOW_REFS)
    assert len(violations) == 1


def test_open_issue_citation_passes_against_window_refs_union_remaining_refs():
    remaining_refs = {"2014"}
    lines = ["- Restore probe still returns a hardcoded slot (#2014)."]
    assert lint_release_notes(lines, WINDOW_REFS | remaining_refs) == []
