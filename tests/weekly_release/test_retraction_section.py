"""#1659 - "Отозвано" (retracted) release-notes section: discloses when a
previously-released change was later reverted. Structurally assembled from
`repo_result.retractions` (Reverts #N marker, extracted by
weekly_release_gather.py) - citation-correct by construction, so it is never
routed through lint_release_notes, mirroring format_goal_section's exemption
(same reasoning: a heading line with no digit citation would otherwise be
flagged as a violation).
"""

from __future__ import annotations

from scripts.weekly_release_engine import assemble_release_body, format_retraction_section


def test_empty_retractions_yields_empty_section():
    assert format_retraction_section([]) == ""


def test_non_empty_retractions_yields_section_citing_both_refs():
    retractions = [
        {"original_ref": "40", "revert_ref": "50", "title": "revert: undo risky rollout"},
    ]
    text = format_retraction_section(retractions)
    assert "Отозвано" in text
    assert "#40" in text
    assert "#50" in text
    assert "revert: undo risky rollout" in text


def test_multiple_retractions_each_get_a_bullet():
    retractions = [
        {"original_ref": "40", "revert_ref": "50", "title": "revert: undo risky rollout"},
        {"original_ref": "41", "revert_ref": "51", "title": "revert: undo bad default"},
    ]
    text = format_retraction_section(retractions)
    assert text.count("#40") == 1
    assert text.count("#41") == 1
    assert text.count("#50") == 1
    assert text.count("#51") == 1


def test_retraction_section_included_when_non_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
        retraction_section="## ⏪ Отозвано\n- #40 отозвано в #50: revert: undo risky rollout",
    )
    assert "## ⏪ Отозвано" in body
    assert "#40 отозвано в #50" in body


def test_retraction_section_omitted_when_empty():
    body = assemble_release_body(
        notes_body="- Added schema v2 (#1597).",
        remaining_section="",
        full_changelog_url="https://github.com/x/y/compare/a...b",
    )
    assert "Отозвано" not in body
