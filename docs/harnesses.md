---
applies_when: setup skill needs to resolve a harness's rules-file name, skills directory, or include support
applies_when_not: choosing which harness to use, or judging a harness's overall capability
signed_off:
---

# Harness table

Dated, pull-only reference (D3): the setup skill reads this table rather than hard-coding harness
paths inline. Each row's `verified` date is when its facts were last checked against a source —
not a claim that the author has run every harness personally. Unverified rows are marked as such
per D30 and should be confirmed (or corrected) before being relied on for anything but a first
guess.

The "Include support" column names the mechanism's **shape**, where the harness has one, so a
reader doesn't have to infer it from prose:

- **in-file include line** — one file pulls another in with its own syntax (`@path`, `#include`, …)
- **config-level file list** — a separate config file names which rules files to combine
- **drop-in directory** — any file dropped in a directory is picked up automatically, no per-file
  registration
- **directory-hierarchy concatenation** — not a per-file mechanism at all: the harness auto-combines
  same-named files found while walking a directory tree

Every claim below has a verbatim-quote source in [Verification notes](#verification-notes).

| Harness | Rules-file | Skills dir | Include support | Verified | Source |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` (project), `~/.claude/skills/` (user) | Yes — **in-file include line**: `@path/to/file` syntax, expanded inline at session start, recursive up to four hops, relative/absolute/home-dir paths; also a **drop-in directory**: any `.md` file placed in `.claude/rules/` (recursive, subdirectories included) is auto-loaded, no per-file registration | 2026-09-17 | https://code.claude.com/docs/en/memory |
| OpenCode | `AGENTS.md` | `.opencode/skills/`, `~/.config/opencode/skills/`, `.claude/skills/`, `~/.claude/skills/`, `.agents/skills/`, `~/.agents/skills/` | Yes — **config-level file list**: `instructions` array in `opencode.json`, supports glob patterns (e.g. `packages/*/AGENTS.md`) and remote URLs; combined with `AGENTS.md` | 2026-09-17 | https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/rules.mdx |
| Codex CLI | `AGENTS.md` (or `AGENTS.override.md` if present) | unverified | No in-file include line, config-level list, or drop-in directory — but **directory-hierarchy concatenation**: `AGENTS.md` files from repo root down to cwd are auto-joined (closer file wins on conflict), capped at `project_doc_max_bytes` (32 KiB default) | 2026-09-17 | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| Gemini CLI | `GEMINI.md` | unverified | Yes — **in-file include line**: `@file.md` import syntax, relative/absolute paths (no documented `~` home-dir support), default max import depth 5 (configurable), nested imports, circular-import detection | 2026-09-17 | https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/memport.md |
| Cursor | `.cursor/rules/*.mdc` (also reads nested `AGENTS.md` per subdirectory) | unverified | Yes — **drop-in directory**: every `.mdc` file in `.cursor/rules/` is auto-discovered (a plain `.md` file there is ignored for lacking frontmatter); `@filename.ts` is an in-rule context reference, not a cross-file include | 2026-09-17 | https://cursor.com/docs/rules |
| GitHub Copilot | `.github/copilot-instructions.md` (also reads `AGENTS.md`) | unverified | Yes — **drop-in directory**: `.github/instructions/*.instructions.md` files, scoped with an `applyTo` glob-pattern in frontmatter, combined with the global file | 2026-09-17 | https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide |
| Zed | `AGENTS.md` (one of several names tried, first match wins — see notes) | unverified | No — no in-file include/import syntax documented; project instructions use the **first matching file only**, not combined across files. Rules (the older feature) were deprecated in v1.4.0, replaced by Skills + Instructions | 2026-09-17 | https://github.com/zed-industries/zed/blob/main/docs/src/ai/instructions.md |
| Windsurf | `.windsurf/rules/*.md` (also still reads the legacy single-file `.windsurfrules` at the workspace root) | unverified | Yes — **drop-in directory**: "One file per rule, each with its own activation mode" (`always_on`, `model_decision`, `glob`, `manual`); separate single always-on `global_rules.md` (6,000-char cap) vs. per-file workspace rules (12,000-char cap each) | 2026-09-17 | https://docs.windsurf.com/windsurf/cascade/memories |

## Verification notes

Verbatim excerpts backing each `Include support` claim above, fetched 2026-09-17 directly from
each harness's own primary documentation (not a mirror or a secondary summary).

- **Claude Code** — https://code.claude.com/docs/en/memory — "Imported files can recursively
  import other files, with a maximum depth of eight hops." "Both relative and absolute paths are
  allowed." A home-dir import is shown explicitly: `- @~/.claude/my-project-instructions.md`. This
  pass also found a second, separate mechanism not in the prior row: "Place markdown files in your
  project's `.claude/rules/` directory... All `.md` files are discovered recursively, so you can
  organize rules into subdirectories" — a genuine drop-in directory, distinct from `@import`.

- **OpenCode** — https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/rules.mdx —
  "All instruction files are combined with your `AGENTS.md` files." The `instructions` array in
  `opencode.json` accepts glob patterns (example given: `packages/*/AGENTS.md`) and remote URLs
  (5s fetch timeout). This corrects the
  [previous row](https://github.com/Osasuwu/jarvis-oss/blob/0397c401ea665c2fa6d39cf5e83b2dc8dd09f776/docs/harnesses.md),
  which claimed "No documented import/include mechanism" and cited the skills doc rather than the
  rules doc.

- **Codex CLI** — https://learn.chatgpt.com/docs/agent-configuration/agents-md — "Codex reads
  `AGENTS.override.md` if it exists. Otherwise, Codex reads `AGENTS.md`" and "Codex concatenates
  files from the root down, joining them with blank lines. Files closer to your current directory
  override earlier guidance because they appear later" and "Codex skips empty files and stops
  adding files once the combined size reaches the limit defined by `project_doc_max_bytes` (32 KiB
  by default)." The page makes no mention of `@import`, `#include`, or similar in-file syntax —
  this is a directory-hierarchy mechanism, not an include line, config list, or drop-in directory.

- **Gemini CLI** — https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/memport.md —
  documents `@file.md` import syntax with a default max import depth of 5 levels (configurable),
  nested imports, circular-import detection, and code-block-aware parsing so `@` inside a fenced
  code block is not treated as an import.

- **Cursor** — https://cursor.com/docs/rules — `.cursor/rules/*.mdc` files are auto-discovered;
  Cursor's own docs note a plain `.md` file dropped in that directory is ignored because it lacks
  the `.mdc` frontmatter the rules system requires. Cursor also now reads nested `AGENTS.md` files
  per subdirectory, combined with parent directories (more specific wins). `@filename.ts` is
  documented as an in-rule context reference for pulling a file into the model's context, not a
  cross-file include mechanism for rules content.

- **GitHub Copilot** — https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide —
  documents `.github/instructions/*.instructions.md` files with an `applyTo` glob-pattern in
  frontmatter, combined with `.github/copilot-instructions.md`.

- **Zed** — https://github.com/zed-industries/zed/blob/main/docs/src/ai/instructions.md (and the
  migration note in `docs/src/ai/rules.md`) — project instruction file resolution is first match
  from a priority list (`.rules`, `.cursorrules`, `.windsurfrules`, `.clinerules`,
  `.github/copilot-instructions.md`, `AGENT.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) — exclusive,
  not combined. Personal instructions live at `~/.config/zed/AGENTS.md`. No in-file include/import
  syntax is documented. The Rules feature itself is deprecated as of v1.4.0 and replaced by Skills
  + Instructions.

- **Windsurf** — https://docs.windsurf.com/windsurf/cascade/memories (redirects to
  `docs.devin.ai/desktop/cascade/memories` — Windsurf's docs are now hosted under Cognition's Devin
  Desktop domain; the product itself is being rebranded but the harness is still referred to as
  Windsurf here since that is the name this table's readers look for) — "Workspace | `.devin/rules/*.md`
  (preferred) or `.windsurf/rules/*.md` (fallback) | One file per rule, each with its own
  activation mode... Limited to 12,000 characters per file. The legacy single-file `.windsurfrules`
  at the workspace root is also still read." Activation modes: `always_on`, `model_decision`,
  `glob`, `manual`. Global rules: single `global_rules.md`, always on, 6,000-char cap.

## Notes

- Include-support mechanisms fall into four shapes, not three — see the column description above.
  Codex CLI's directory-hierarchy concatenation doesn't fit "include line", "config-level list", or
  "drop-in directory": nothing names which files to combine, and there's no single directory to
  drop a file into — the harness walks the directory tree itself.
- Per D23, this table previously claimed Claude Code was the only harness with include-like
  support. That was true against the sources checked in the prior pass but not against each
  harness's own current primary docs — re-verification found five more harnesses (OpenCode, Gemini
  CLI, Cursor, GitHub Copilot, Windsurf) with a documented include-like mechanism, plus one more
  (Codex CLI) with a related-but-distinct hierarchy-concatenation behavior. Only Zed has none.
- Rows marked `unverified` in the Skills dir column were not checked against each harness's own
  primary documentation for that column specifically — this pass's scope was the Include support
  column (issue #63); Skills dir remains open for a future verification pass.
- AGENTS.md (the cross-tool standard, distinct from any single harness's own rules-file) is not a
  row here — this table is about harness-specific rules-file resolution, not the shared standard
  several harnesses also read.
