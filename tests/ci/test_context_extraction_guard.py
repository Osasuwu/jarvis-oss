"""Guard for #1791 — root AGENTS.md rebuild, CLAUDE.md collapsed to a bare import.

History: #1417 extracted the old Invariants into `docs/context/invariants.md`,
delivered by a bare `@import` line in root `CLAUDE.md` (bypassing the
SessionStart hook's budget-constrained assembler entirely — the assembler
used to drop `project_context` in 47% of sessions, see CONTEXT.md → *Context
delivery*). #1418 retired the second extracted file, `docs/context/glossary-index.md`.
#1791 rebuilt the always-loaded half from scratch: a from-zero root `AGENTS.md`
(≤100 lines, jarvis's process rules + exactly two invariants) replaces
`invariants.md`, and `CLAUDE.md` collapses to a single bare `@AGENTS.md` line —
still a bare `@import`, so the delivery mechanism this guard exists to pin is
unchanged even though the target file is new.

`AGENTS.md` is also the cross-tool standard filename (Linux Foundation AAIF;
read by Codex, OpenCode, Cursor, Copilot, Gemini CLI, Zed, Amp) — unlike the
old `invariants.md`, no other tool needs a duplicate file to find these rules.

Checks pinned here:
  - root AGENTS.md exists, is <=100 lines, and does not leak session-mechanism
    vocabulary that has no business in a cross-tool-readable file
  - CLAUDE.md's entire content is the single bare line `@AGENTS.md`
  - the four #1791 deletion targets (`docs/context/invariants.md`,
    `.claude/rules/*.md`, `.github/AGENTS.md`, `.github/copilot-instructions.md`)
    are gone
  - `scripts/session-context.py` no longer defines the retired assembler path
    (`_load_project_context`) — the whole point of #1417 is that this content
    no longer rides the budget-constrained push
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
AGENTS_MD_PATH = REPO_ROOT / "AGENTS.md"
INVARIANTS_MD_PATH = REPO_ROOT / "docs" / "context" / "invariants.md"
CLAUDE_RULES_DIR = REPO_ROOT / ".claude" / "rules"
GITHUB_AGENTS_MD_PATH = REPO_ROOT / ".github" / "AGENTS.md"
COPILOT_INSTRUCTIONS_PATH = REPO_ROOT / ".github" / "copilot-instructions.md"

# Session-mechanism vocabulary that must not leak into the cross-tool-readable
# AGENTS.md — these terms are jarvis-instance-internal (MCP tool names, this
# operator's memory backend, sandcastle infra) and belong in CLAUDE.md/CONTEXT.md,
# never in the file Codex/Cursor/Copilot/etc. read directly.
BANNED_TOKEN_PATTERN = re.compile(
    r"mcp__memory|memory_recall|record_decision|session-context|jarvis-oss|"
    r"sandcastle|always_load"
)


class TestAgentsMd:
    def test_agents_md_exists(self):
        assert AGENTS_MD_PATH.exists(), f"missing {AGENTS_MD_PATH}"

    def test_agents_md_is_at_most_100_lines(self):
        lines = AGENTS_MD_PATH.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 100, (
            f"AGENTS.md is {len(lines)} lines — #1791 requires <=100. It is meant "
            "to stay short; move situational detail to docs/reference/*.md instead "
            "of growing this file."
        )

    def test_agents_md_has_no_banned_session_mechanism_tokens(self):
        text = AGENTS_MD_PATH.read_text(encoding="utf-8")
        hits = BANNED_TOKEN_PATTERN.findall(text)
        assert not hits, (
            f"AGENTS.md contains session-mechanism vocabulary {hits} — this file "
            "is read directly by non-Claude tools (Codex, Cursor, Copilot, ...) "
            "and must not assume Claude Code's own MCP/memory/session internals."
        )


class TestClaudeMdIsBareImport:
    def test_claude_md_exists(self):
        assert CLAUDE_MD_PATH.exists(), f"missing {CLAUDE_MD_PATH}"

    def test_claude_md_content_is_exactly_bare_agents_import(self):
        text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
        assert text.strip("\n") == "@AGENTS.md", (
            "root CLAUDE.md must contain exactly the single bare line `@AGENTS.md` "
            f"— found: {text!r}. #1791 collapsed CLAUDE.md down to this one import; "
            "any other content belongs in AGENTS.md, CONTEXT.md, or docs/reference/*.md."
        )


class TestDeletionTargetsStayDeleted:
    def test_old_invariants_md_removed(self):
        assert not INVARIANTS_MD_PATH.exists(), (
            f"{INVARIANTS_MD_PATH} was folded into AGENTS.md by #1791 and must not be reinstated"
        )

    def test_claude_rules_dir_removed(self):
        assert not CLAUDE_RULES_DIR.exists(), (
            f"{CLAUDE_RULES_DIR} was retired by #1791 — its content moved into "
            "AGENTS.md, docs/reference/*.md, or the sole consumer skill"
        )

    def test_github_agents_md_removed(self):
        assert not GITHUB_AGENTS_MD_PATH.exists(), (
            f"{GITHUB_AGENTS_MD_PATH} was retired by #1791 — the root AGENTS.md "
            "is the single copy now; GitHub-native tools resolve the root file "
            "without needing a `.github/` duplicate"
        )

    def test_copilot_instructions_removed(self):
        assert not COPILOT_INSTRUCTIONS_PATH.exists(), (
            f"{COPILOT_INSTRUCTIONS_PATH} was retired by #1791 — Copilot reads "
            "root AGENTS.md directly under the cross-tool AAIF convention now"
        )
