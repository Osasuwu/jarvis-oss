"""#1572 AC5 — semver classifier: fixes-only window -> patch, window with a
new capability -> minor, major never emitted under any input.

Fixture windows are real merged PRs from Osasuwu/jarvis history (not
synthesized), pulled via `gh pr list --state merged` on 2026-08-20:

- Fixes-only window (2026-08-19/20, all `fix(...)` titles, no `feat`):
  #1650, #1656, #1661.
- New-capability window (2026-08-18/19, contains `feat(morning)` entries
  alongside fixes/refactors): #1637, #1638, #1640, #1641, #1642, #1643, #1644.
"""

from __future__ import annotations

from scripts.weekly_release_engine import classify_bump, parse_conventional_type

FIXES_ONLY_WINDOW = [
    {
        "number": 1650,
        "title": "fix(mcp): swallow Windows errno-22 OSError on stdio_server teardown",
    },
    {"number": 1656, "title": 'fix(ci): treat explicit "none" as no risk in auto-merge carve-out'},
    {"number": 1661, "title": "fix(ci): commit-free clean re-review override for verify-verdict"},
]

NEW_CAPABILITY_WINDOW = [
    {"number": 1637, "title": "feat(morning): section provenance + degradation disclosure"},
    {"number": 1638, "title": "feat(morning): goals/milestones section + arch-sweep reminder"},
    {"number": 1640, "title": "feat(morning): obligations registry + evaluate() pure function"},
    {"number": 1641, "title": "feat(morning): escalations two-channel dedup by id"},
    {"number": 1642, "title": "feat(morning): obligations evidence-probe with mandatory citation"},
    {"number": 1643, "title": "feat(morning): focus_signal v1 - decision drift vs goal priorities"},
    {
        "number": 1644,
        "title": "feat(morning): detector gap journal - memory-first, promote on second (#1595)",
    },
]


def test_parse_conventional_type_extracts_type_scope_breaking():
    c = parse_conventional_type("fix(ci): add retry to project-sync for transient GitHub API 503s")
    assert c.type == "fix"
    assert c.scope == "ci"
    assert c.breaking is False


def test_parse_conventional_type_flags_bang_breaking():
    c = parse_conventional_type("feat(api)!: drop legacy v1 endpoint")
    assert c.type == "feat"
    assert c.breaking is True


def test_fixes_only_window_classifies_as_patch():
    assert classify_bump(FIXES_ONLY_WINDOW) == "patch"


def test_new_capability_window_classifies_as_minor():
    assert classify_bump(NEW_CAPABILITY_WINDOW) == "minor"


def test_breaking_marker_never_escalates_to_major():
    # decision 5b811a25: no breaking-change signal in any form —
    # the skill never emits major, trust ramp is the only human gate.
    window = [{"number": 9001, "title": "feat(api)!: drop legacy v1 endpoint"}]
    assert classify_bump(window) == "minor"

    window_with_body = [
        {
            "number": 9002,
            "title": "feat(api): new v2 endpoint",
            "body": "BREAKING CHANGE: v1 clients must migrate.",
        }
    ]
    assert classify_bump(window_with_body) == "minor"


def test_empty_window_classifies_as_patch():
    assert classify_bump([]) == "patch"
