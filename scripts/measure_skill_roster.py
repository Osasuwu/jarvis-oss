"""Measure the always-loaded skill-roster character budget.

The roster is the model-visible listing of skills assembled at session start:
one line per skill that does NOT carry `disable-model-invocation: true` in its
SKILL.md frontmatter, formatted as "- <name>: <description>". This script
re-derives that listing from the canonical skill sources so the roster size
can be tracked as a repo floor (see issue #1268 / milestone #63 AC3).

Usage: python scripts/measure_skill_roster.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude-userlevel" / "skills"

_KEY_RE = re.compile(r"^([A-Za-z][\w-]*):\s*(.*)$")


@dataclass
class SkillEntry:
    name: str
    description: str
    disabled: bool

    @property
    def roster_line(self) -> str:
        return f"- {self.name}: {self.description}"

    @property
    def roster_chars(self) -> int:
        return len(self.roster_line) if not self.disabled else 0


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_frontmatter(skill_md: Path) -> dict:
    """Line-based frontmatter parser, tolerant of the loosely-YAML values
    some SKILL.md files carry (unquoted colons in plain scalars). Handles
    single-line values, quoted single-line values, and `>` folded block
    scalars — the three shapes actually used across .claude-userlevel/skills.
    """
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _rest = text.split("---", 2)
    lines = fm.split("\n")

    result: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _KEY_RE.match(line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2)
        if rest.strip() in (">", "|", ">-", "|-"):
            # Folded/literal block scalar: consume subsequent indented lines.
            block_lines = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block_lines.append(lines[i].strip())
                i += 1
            result[key] = " ".join(part for part in block_lines if part).strip()
            continue
        result[key] = _strip_quotes(rest.strip())
        i += 1
    return result


def load_skills(skills_dir: Path = SKILLS_DIR) -> list[SkillEntry]:
    entries = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        fm = _read_frontmatter(skill_md)
        name = fm.get("name", skill_md.parent.name)
        description = (fm.get("description") or "").strip()
        disabled = str(fm.get("disable-model-invocation", "false")).strip().lower() == "true"
        entries.append(SkillEntry(name=name, description=description, disabled=disabled))
    return entries


def total_roster_chars(entries: list[SkillEntry]) -> int:
    return sum(e.roster_chars for e in entries)


def main() -> int:
    entries = load_skills()
    visible = [e for e in entries if not e.disabled]
    hidden = [e for e in entries if e.disabled]

    print(
        f"Skills total: {len(entries)}  visible: {len(visible)}  hidden (disable-model-invocation): {len(hidden)}"
    )
    print(f"Roster chars (visible only): {total_roster_chars(entries)}")
    print()
    for e in sorted(visible, key=lambda e: e.roster_chars, reverse=True):
        print(f"{e.roster_chars:5d}  {e.name}")
    if hidden:
        print()
        print("Hidden from roster (disable-model-invocation: true):")
        for e in hidden:
            print(f"  - {e.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
