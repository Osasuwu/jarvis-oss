"""#1669 - extract_goal_movements(): goal_list() returns rendered markdown
(a `# Goals (N)` preamble, then one `## <title>` block per goal with a
`Slug: \\`<slug>\\`` line and a `**Progress (N%):**` checklist, completed
items dated `(YYYY-MM-DD)`) - not the structured list[dict] format_goal_section()
expects. This bridges the two: only checked, dated bullets that fall inside
the release window count as movement.
"""

from __future__ import annotations

from scripts.weekly_release_engine import extract_goal_movements

GOALS_MARKDOWN = """# Goals (2)

## Weekly releases
Slug: `weekly-releases`
**Progress (60%):**
- [x] S1 vertical slice shipped (2026-08-05)
- [x] S2 routine host gate landed (2026-08-12)
- [ ] S3 retraction markers

## Memory hygiene
Slug: `memory-hygiene`
**Progress (100%):**
- [x] Curated 40 stale rows (2026-07-01)
"""

WINDOW_START = "2026-08-01T00:00:00+00:00"
WINDOW_END = "2026-08-20T00:00:00+00:00"


def test_goal_with_dated_bullets_in_window_reports_progress_note():
    movements = extract_goal_movements(GOALS_MARKDOWN, WINDOW_START, WINDOW_END)
    weekly = next(m for m in movements if m["slug"] == "weekly-releases")
    assert "no_movement" not in weekly
    assert "S1 vertical slice shipped" in weekly["progress_note"]
    assert "S2 routine host gate landed" in weekly["progress_note"]


def test_goal_with_only_out_of_window_bullets_reports_no_movement():
    movements = extract_goal_movements(GOALS_MARKDOWN, WINDOW_START, WINDOW_END)
    memory = next(m for m in movements if m["slug"] == "memory-hygiene")
    assert memory.get("no_movement") is True


def test_unchecked_bullet_does_not_count_as_movement():
    markdown = """# Goals (1)

## Solo goal
Slug: `solo-goal`
**Progress (0%):**
- [ ] not done yet (2026-08-10)
"""
    movements = extract_goal_movements(markdown, WINDOW_START, WINDOW_END)
    assert movements[0].get("no_movement") is True


def test_checked_bullet_without_date_does_not_count_as_movement():
    markdown = """# Goals (1)

## Solo goal
Slug: `solo-goal`
**Progress (100%):**
- [x] done, no date recorded
"""
    movements = extract_goal_movements(markdown, WINDOW_START, WINDOW_END)
    assert movements[0].get("no_movement") is True


def test_block_without_slug_line_is_skipped():
    markdown = """# Goals (1)

## Malformed block
**Progress (0%):**
- [x] something (2026-08-10)
"""
    movements = extract_goal_movements(markdown, WINDOW_START, WINDOW_END)
    assert movements == []


def test_empty_goal_list_yields_no_movements():
    assert extract_goal_movements("# Goals (0)\n", WINDOW_START, WINDOW_END) == []
