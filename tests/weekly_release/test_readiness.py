"""#1572 AC4 - release-worthiness detector on real historical windows:
a substantive week -> release, a dep-bump/CI-only week -> no release.

Fixtures are real merged PRs from Osasuwu/jarvis history, pulled via
`gh pr list --state merged` on 2026-08-20 (not synthesized):

- Substantive window (2026-08-17/18, the exact range the issue itself
  names as an example): #1597, #1600, #1601, #1602, #1604, #1612.
- Dep-bump/CI-only window (2026-08-18/19, all dependabot chore(deps*) PRs
  plus one ci(deps) automation PR): #1585, #1584, #1631, #1632, #1633,
  #1634.
"""

from __future__ import annotations

from scripts.weekly_release_engine import is_release_worthy

SUBSTANTIVE_WINDOW = [
    {
        "number": 1597,
        "title": "feat(morning): schema v2 + gather + engine for daily digest (#1586)",
    },
    {
        "number": 1601,
        "title": "fix(memory): surface merge_section conflict/error detail on write failure (#1582)",
    },
    {"number": 1600, "title": "fix(memory): require project on record_decision (#1587)"},
    {"number": 1604, "title": "feat(morning): render + MCP morning_digest + skill morning (#1588)"},
    {"number": 1602, "title": "fix(ci): add retry to project-sync for transient GitHub API 503s"},
    {"number": 1612, "title": "fix(mcp-morning): set isError=True on call_tool error paths"},
]

DEP_BUMP_CI_ONLY_WINDOW = [
    {
        "number": 1585,
        "title": "chore(deps-dev): update apprise requirement from <2,>=1.9 to >=1.12.0,<2",
        "is_dependabot": True,
    },
    {
        "number": 1584,
        "title": "chore(deps-dev): update ruff requirement from <1,>=0.16.1 to >=0.16.3,<1",
        "is_dependabot": True,
    },
    {"number": 1631, "title": "ci(deps): auto-regenerate uv.lock on dependabot pip PRs [no-issue]"},
    {
        "number": 1632,
        "title": "chore(deps): update python-json-logger requirement from <5,>=4.1.0 to >=4.2.0,<5",
        "is_dependabot": True,
    },
    {
        "number": 1634,
        "title": "chore(deps-dev): update psycopg requirement from <4,>=3.1 to >=3.3.4,<4",
        "is_dependabot": True,
    },
    {
        "number": 1633,
        "title": "chore(deps-dev): update apprise requirement from <2,>=1.9 to >=1.12.0,<2",
        "is_dependabot": True,
    },
]


def test_substantive_window_is_release_worthy():
    assert is_release_worthy(SUBSTANTIVE_WINDOW) is True


def test_dep_bump_and_ci_only_window_is_not_release_worthy():
    assert is_release_worthy(DEP_BUMP_CI_ONLY_WINDOW) is False


def test_empty_window_is_not_release_worthy():
    assert is_release_worthy([]) is False
