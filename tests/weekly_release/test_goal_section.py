"""#1572 AC8 - goal-section format selection: 0/1 active (moved) goal ->
narrative prose, >=2 -> a (target)-anchored split; goals with no movement
in the window are omitted from either format.
"""

from __future__ import annotations

from scripts.weekly_release_engine import format_goal_section


def test_zero_moved_goals_yields_empty_section():
    goals = [{"slug": "g1", "title": "Goal one", "no_movement": True}]
    assert format_goal_section(goals) == ""


def test_one_moved_goal_yields_narrative_prose():
    goals = [
        {"slug": "g1", "title": "Weekly releases", "progress_note": "shipped S1 vertical slice"},
    ]
    text = format_goal_section(goals)
    assert "Weekly releases" in text
    assert "shipped S1 vertical slice" in text
    assert "\U0001f3af" not in text  # no goal-split marker in narrative mode


def test_two_or_more_moved_goals_yields_split_section():
    goals = [
        {"slug": "g1", "title": "Weekly releases", "progress_note": "shipped S1"},
        {"slug": "g2", "title": "Memory hygiene", "progress_note": "curated 40 stale rows"},
    ]
    text = format_goal_section(goals)
    assert text.count("\U0001f3af") >= 2  # header + one bullet marker per goal
    assert "Weekly releases" in text
    assert "Memory hygiene" in text


def test_no_movement_goals_are_omitted_even_in_split_mode():
    goals = [
        {"slug": "g1", "title": "Weekly releases", "progress_note": "shipped S1"},
        {"slug": "g2", "title": "Memory hygiene", "progress_note": "curated rows"},
        {"slug": "g3", "title": "Stale personal goal", "no_movement": True},
    ]
    text = format_goal_section(goals)
    assert "Stale personal goal" not in text
