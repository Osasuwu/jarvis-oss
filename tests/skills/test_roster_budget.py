"""Tests for the skill-roster budget (issue #1268 / milestone #63 AC3, AC1, AC2).

The roster is the model-visible listing assembled at session start: one
"- <name>: <description>" line per skill that does NOT carry
`disable-model-invocation: true`. This suite pins:

1. AC1 — every owner-invoked-only skill is suppressed via frontmatter.
2. AC2 — the device-local `skillOverrides` suppression duplicate is gone
   from the canonical user-level settings source.
3. AC3 — the roster stays meaningfully below its pre-#1268 baseline
   (~7,884 chars / 27 skills), guarding against regression back toward it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from measure_skill_roster import load_skills, total_roster_chars  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS_PATH = REPO_ROOT / ".claude-userlevel" / "settings.json"

# Pre-#1268 baseline was ~7,884 chars across 27 visible skills. "Meaningfully
# below" is interpreted as a hard ceiling with headroom for new skills to add
# a short description before tripping the regression guard.
ROSTER_CHAR_BUDGET = 6500

OWNER_INVOKED_ONLY_SKILLS = {
    "caveman",
    "last-work-report",
    "setup-tasks",
    "status-record",
    "zoom-out",
}


def test_roster_stays_under_budget():
    entries = load_skills()
    total = total_roster_chars(entries)
    assert total < ROSTER_CHAR_BUDGET, (
        f"Roster grew to {total} chars, at/above the {ROSTER_CHAR_BUDGET}-char "
        "regression budget set by #1268. Trim a description or suppress a "
        "skill that shouldn't be model-invocable."
    )


def test_owner_invoked_only_skills_are_suppressed():
    entries = {e.name: e for e in load_skills()}
    missing = OWNER_INVOKED_ONLY_SKILLS - entries.keys()
    assert not missing, f"Expected owner-only skills not found on disk: {missing}"

    not_suppressed = [name for name in OWNER_INVOKED_ONLY_SKILLS if not entries[name].disabled]
    assert not not_suppressed, (
        f"These owner-invoked-only skills are missing "
        f"`disable-model-invocation: true`: {not_suppressed}"
    )


def test_settings_json_has_no_skill_overrides_duplicate():
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    assert "skillOverrides" not in settings, (
        "skillOverrides must be removed from the canonical user-level "
        "settings.json — suppression now lives solely in SKILL.md "
        "frontmatter (disable-model-invocation)."
    )
