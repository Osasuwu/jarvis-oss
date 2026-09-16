---
applies_when: setup skill needs to resolve a harness's rules-file name, skills directory, or include support
applies_when_not: choosing which harness to use, or judging a harness's overall capability
signed_off: 2026-09-16
---

# Harness table

Dated, pull-only reference (D3): the setup skill reads this table rather than hard-coding harness
paths inline. Each row's `verified` date is when its facts were last checked against a source —
not a claim that the author has run every harness personally. Unverified rows are marked as such
per D30 and should be confirmed (or corrected) before being relied on for anything but a first
guess.

| Harness | Rules-file | Skills dir | Include support | Verified | Source |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` (project), `~/.claude/skills/` (user) | Yes — `@path/to/file` syntax, expanded inline at session start, recursive up to 5 hops, relative/absolute/home-dir paths | 2026-09-16 | https://code.claude.com/docs/en/memory |
| OpenCode | `AGENTS.md` | `.opencode/skills/`, `~/.config/opencode/skills/`, `.claude/skills/`, `~/.claude/skills/`, `.agents/skills/`, `~/.agents/skills/` | No documented import/include mechanism | 2026-09-16 | https://opencode.ai/docs/skills/ |
| Codex CLI | `AGENTS.md` | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |
| Gemini CLI | `GEMINI.md` | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |
| Cursor | `.cursor/rules/*.mdc` (also reads `AGENTS.md`) | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |
| GitHub Copilot | `.github/copilot-instructions.md` (also reads `AGENTS.md`) | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |
| Zed | `AGENTS.md` | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |
| Windsurf | `.windsurfrules` | unverified | No documented include mechanism found | 2026-09-16 (unverified) | not independently confirmed against primary docs |

## Notes

- Per AC (issue #25) and D23: Claude Code is the only harness marked with `@import` include
  support. No other row's source demonstrates an equivalent mechanism as of the `verified` date
  above — if one is found, update that row with a source link rather than changing this note.
- Rows marked `unverified` were not checked against each harness's own primary documentation at
  write time; they carry a plausible rules-file name from secondary sources only and should not
  be treated as confirmed until re-verified with a direct source link.
- AGENTS.md (the cross-tool standard, distinct from any single harness's own rules-file) is not a
  row here — this table is about harness-specific rules-file resolution, not the shared standard
  several harnesses also read.
