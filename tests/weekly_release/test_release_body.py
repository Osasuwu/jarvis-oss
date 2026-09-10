"""#1572 AC9 - release body assembly: no issue links added by the engine
itself, **Full Changelog** line present whenever a compare URL is given,
footer always present. notes_body/remaining_section are already
agent-authored prose (facts sourced from the collected window / milestone
per the fact-anchoring linter) - this function only appends the mechanical
Full-Changelog line and footer.
"""

from __future__ import annotations

from scripts.weekly_release_engine import assemble_release_body


def test_full_changelog_line_is_present():
    body = assemble_release_body(
        notes_body="- Added schema v2 for the daily digest (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/Osasuwu/jarvis/compare/v0.4.2...v0.5.0",
    )
    assert "**Full Changelog**: https://github.com/Osasuwu/jarvis/compare/v0.4.2...v0.5.0" in body


def test_footer_is_present_on_every_release():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
    )
    assert "Опубликовано ботом" in body


def test_engine_adds_no_issue_links_of_its_own():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="- Migration cleanup still pending (#1601).",
        full_changelog_url="https://github.com/x/y/compare/a...b",
    )
    assert "github.com/x/y/issues/" not in body


def test_remaining_section_included_when_non_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="## Осталось\n- Migration cleanup pending (#1601).",
        full_changelog_url="https://github.com/x/y/compare/a...b",
    )
    assert "## Осталось" in body
    assert "Migration cleanup pending (#1601)" in body


def test_remaining_section_omitted_when_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
    )
    assert "## Осталось" not in body


def test_full_changelog_line_omitted_for_first_release():
    """A repo's first-ever release has no prior tag to diff against - the
    skill passes full_changelog_url=None rather than invent a compare URL."""
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url=None,
    )
    assert "Full Changelog" not in body
    assert "None" not in body


# -- #1668 disclosure_section / #1669 goal_section ---------------------------


def test_disclosure_section_included_when_non_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        disclosure_section="_Покрывает период с 2026-07-21 по 2026-08-20._",
    )
    assert "Покрывает период" in body


def test_disclosure_section_omitted_when_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        disclosure_section="",
    )
    assert "Покрывает период" not in body


def test_goal_section_included_when_non_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        goal_section="## \U0001f3af Goals\n- Weekly releases: shipped S1",
    )
    assert "Goals" in body
    assert "shipped S1" in body


def test_goal_section_omitted_when_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        goal_section="",
    )
    assert "\U0001f3af" not in body


def test_section_ordering_notes_disclosure_retraction_goal_remaining():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="## Осталось\n- pending item (#1601).",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        retraction_section="## ⏪ Отозвано\n- #40 отозвано в #50",
        disclosure_section="_disclosure text_",
        goal_section="## \U0001f3af Goals\n- goal note",
    )
    order = [
        body.index("Added schema v2"),
        body.index("disclosure text"),
        body.index("Отозвано"),
        body.index("Goals"),
        body.index("## Осталось"),
    ]
    assert order == sorted(order)
