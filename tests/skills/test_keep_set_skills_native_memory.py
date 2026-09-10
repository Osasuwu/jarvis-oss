"""Regression test for the keep-set skills' native-auto-memory rewrite (#1793).

Six keep-set skills (`implement`, `end`, `file-issue`, `dispatch`, `triage`,
`weekly-release`) were rewritten to run on native auto memory instead of the
retired Supabase memory MCP: no `mcp__memory__*` calls, no `record_decision`/
UUID gates, no `sandcastle`/`task_queue` dispatch — decisions are appended as
plain lines to `decisions.md` under native auto memory instead. This is the
mechanical check for that rewrite (issue #1793 AC1/AC3/AC4): zero banned-token
matches across the six files, `decisions.md`/`handoff.md`/`agent:dispatch`
named where the plan requires them, no `_shared/` references left behind, and
every relative markdown link in the six files resolves to a real file on disk.

`to-tickets/SKILL.md` was genericized onto the same native-memory conventions
(#1827, follow-up from #1815) but is covered by a separate class below rather
than folded into `SKILL_NAMES`: unlike the six files above, it legitimately
retains `task_queue` mentions — disclosed, historical/orchestrator-glossary
references (the label's pre-#1793 wiring, and the still-live reactive-core
`emit_task` row) rather than leftover Supabase-memory vocabulary — so it can't
share the six files' zero-tolerance `task_queue` check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude-userlevel" / "skills"

SKILL_NAMES = ("implement", "end", "file-issue", "dispatch", "triage", "weekly-release")

SKILL_PATHS = {name: SKILLS_DIR / name / "SKILL.md" for name in SKILL_NAMES}

# AC1 of #1793: literal zero matches for these tokens across all six files —
# no allowance for "historical/descriptive" uses of the banned vocabulary.
BANNED_PATTERN = re.compile(
    r"mcp__memory|memory_recall|memory_store|record_decision"
    r"|memories_used|decision_uuids|sandcastle|always_load"
)

# Extended purge list beyond the issue's own grep recipe — retired
# queue-and-claim vocabulary that has no place in the rewritten skills either.
EXTENDED_BANNED_PATTERN = re.compile(r"task_queue")

_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _relative_link_targets(text: str) -> list[str]:
    targets = []
    for target in _LINK_RE.findall(text):
        if re.match(r"^[a-zA-Z]+://", target) or target.startswith("#"):
            continue  # external URL or in-page anchor
        targets.append(target)
    return targets


@pytest.fixture(scope="module", params=SKILL_NAMES)
def skill_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def skill_text(skill_name: str) -> str:
    path = SKILL_PATHS[skill_name]
    assert path.exists(), f"keep-set skill missing: {path}"
    return path.read_text(encoding="utf-8")


class TestNoLegacyMemoryOrDispatchVocabulary:
    """AC1 — zero matches for the banned-token list, literal grep semantics."""

    def test_no_banned_tokens(self, skill_name: str, skill_text: str) -> None:
        matches = BANNED_PATTERN.findall(skill_text)
        assert not matches, (
            f"{skill_name}/SKILL.md still contains banned legacy-memory/dispatch tokens: {matches}"
        )

    def test_no_extended_banned_tokens(self, skill_name: str, skill_text: str) -> None:
        matches = EXTENDED_BANNED_PATTERN.findall(skill_text)
        assert not matches, (
            f"{skill_name}/SKILL.md still contains retired queue vocabulary: {matches}"
        )


class TestNoSharedDirReferences:
    """AC4 — references to `_shared/` from these skills are removed or inlined."""

    def test_no_shared_dir_reference(self, skill_name: str, skill_text: str) -> None:
        assert "_shared/" not in skill_text, (
            f"{skill_name}/SKILL.md still references _shared/ — should have been removed or inlined"
        )


class TestDecisionsAndHandoffNamedWhereRequired:
    """Native auto-memory files must be named in the skills that write them."""

    @pytest.mark.parametrize("name", ("implement", "end", "dispatch"))
    def test_decisions_md_named(self, name: str) -> None:
        text = SKILL_PATHS[name].read_text(encoding="utf-8")
        assert "decisions.md" in text, (
            f"{name}/SKILL.md must name decisions.md — it's the native-memory "
            "replacement for record_decision"
        )

    def test_handoff_md_named_in_end_step5(self) -> None:
        text = SKILL_PATHS["end"].read_text(encoding="utf-8")
        assert "handoff.md" in text, "end/SKILL.md Step 5 must name handoff.md"


class TestDispatchLabelNamedWhereRequired:
    """`agent:dispatch` is the GitHub-native trigger label replacing task_queue."""

    @pytest.mark.parametrize("name", ("dispatch", "triage"))
    def test_agent_dispatch_label_named(self, name: str) -> None:
        text = SKILL_PATHS[name].read_text(encoding="utf-8")
        assert "agent:dispatch" in text, (
            f"{name}/SKILL.md must name the agent:dispatch trigger label"
        )


class TestWeeklyReleaseRoutineRegistration:
    """weekly-release gets a Routine-registration instruction in its own SKILL.md."""

    def test_routine_registration_present(self) -> None:
        text = SKILL_PATHS["weekly-release"].read_text(encoding="utf-8")
        normalised = re.sub(r"\s+", " ", text).lower()
        assert "routine" in normalised, (
            "weekly-release/SKILL.md must contain a Routine-registration instruction"
        )
        assert re.search(r"scheduled[_-]task", normalised), (
            "weekly-release/SKILL.md's Routine-registration step must reference "
            "the scheduled-tasks MCP tools"
        )


class TestRelativeMarkdownLinksResolve:
    """Every relative markdown link target in the six files must exist on disk."""

    def test_all_relative_links_resolve(self, skill_name: str, skill_text: str) -> None:
        skill_dir = SKILL_PATHS[skill_name].parent
        for target in _relative_link_targets(skill_text):
            resolved = (skill_dir / target).resolve()
            assert resolved.exists(), (
                f"{skill_name}/SKILL.md has a dead relative link: {target!r} "
                f"(resolved to {resolved})"
            )


TO_TICKETS_PATH = SKILLS_DIR / "to-tickets" / "SKILL.md"


@pytest.fixture(scope="module")
def to_tickets_text() -> str:
    assert TO_TICKETS_PATH.exists(), f"to-tickets skill missing: {TO_TICKETS_PATH}"
    return TO_TICKETS_PATH.read_text(encoding="utf-8")


class TestToTicketsNativeMemoryVocabulary:
    """#1827 — extend #1793's coverage to `to-tickets/SKILL.md`.

    A parallel class rather than folding into `SKILL_NAMES`/`SKILL_PATHS`
    above: `to-tickets/SKILL.md`'s structure differs from the six keep-set
    files in one load-bearing way. `task_queue` is deliberately excluded from
    the zero-tolerance check here — the file discloses it as stale/historical
    vocabulary (the automation-queue label's pre-#1793 wiring) and as the
    reactive-core orchestrator's still-live `emit_task` row concept, not as
    leftover Supabase-memory-MCP vocabulary. Banning it outright would fail
    AC4 (no source changes required) against the already-genericized file.

    `test_all_relative_links_resolve` below resolves links relative to
    `TO_TICKETS_PATH` — the `.claude-userlevel/` mirror inside *this* repo,
    which is self-contained and still resolves today. It does NOT exercise
    the canonical copy in `Osasuwu/jarvis-private` (moved there by #1798),
    where the equivalent parent-directory-style links were dead on arrival —
    fixed separately in #1838 by naming those targets as plain paths or
    permanent GitHub URLs instead. Retargeting this test at a local clone of
    jarvis-private was considered and rejected: it would depend on a
    device-specific filesystem path with no guarantee of existing in CI or on
    another operator's machine (#1838 decision). Instead this coverage rides
    with `.claude-userlevel/` and retires alongside it in #1800, when the
    mirror is deleted and jarvis-private becomes the sole copy.
    """

    def test_no_banned_tokens(self, to_tickets_text: str) -> None:
        matches = BANNED_PATTERN.findall(to_tickets_text)
        assert not matches, (
            f"to-tickets/SKILL.md still contains banned legacy-memory tokens: {matches}"
        )

    def test_no_shared_dir_reference(self, to_tickets_text: str) -> None:
        assert "_shared/" not in to_tickets_text, (
            "to-tickets/SKILL.md still references _shared/ — should have been removed or inlined"
        )

    def test_all_relative_links_resolve(self, to_tickets_text: str) -> None:
        skill_dir = TO_TICKETS_PATH.parent
        for target in _relative_link_targets(to_tickets_text):
            resolved = (skill_dir / target).resolve()
            assert resolved.exists(), (
                f"to-tickets/SKILL.md has a dead relative link: {target!r} (resolved to {resolved})"
            )
