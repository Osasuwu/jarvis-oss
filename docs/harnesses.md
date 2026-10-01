---
applies_when: setup skill needs to resolve a harness's rules-file name, skills directory, or include support
applies_when_not: choosing which harness to use, or judging a harness's overall capability
signed_off:
kind: basics
hub: own-agent
---

# Harness table

Dated, pull-only reference: the setup skill reads this table rather than hard-coding harness
paths inline. Each row's `verified` date is when its facts were last read from the linked
documentation page. The drafting agent read those pages; nobody ran the harnesses. A cell reading
`unverified` was not checked at all, and the check to run for it is under
[A harness not in this table](#a-harness-not-in-this-table).

The "Include support" column names the mechanism's **shape**, where the harness has one, so a
reader doesn't have to infer it from prose:

- **in-file include line** — one file pulls another in with its own syntax (`@path`, `#include`, …)
- **config-level file list** — a separate config file names which rules files to combine
- **drop-in directory** — any file dropped in a directory is picked up, no per-file registration
  (the harness may still want frontmatter on it to decide when to load it; the row says)
- **directory-hierarchy concatenation** — not a per-file mechanism at all: the harness auto-combines
  same-named files found while walking a directory tree

Every Include support claim and every verified Skills dir value below has a source in
[Verification notes](#verification-notes); the cells marked `unverified` have none.

| Harness | Rules-file | Skills dir | Include support | Verified | Source |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` (reads `AGENTS.md` only when no `CLAUDE.md` exists, so creating one switches `AGENTS.md` off) | `.claude/skills/` (project), `~/.claude/skills/` (user) | Yes — **in-file include line**: `@path/to/file` syntax, expanded inline at session start, recursive up to four hops, relative/absolute/home-dir paths; the first time a project uses an external import Claude Code shows an approval dialog, and declining leaves it off, so an unattended run needs the file inside the repo or in `.claude/rules/`; also a **drop-in directory**: any `.md` file placed in `.claude/rules/` (recursive, subdirectories included) is auto-loaded, no per-file registration | 2026-09-17 (Skills dir 2026-10-01) | https://code.claude.com/docs/en/memory, https://code.claude.com/docs/en/skills |
| OpenCode | `AGENTS.md` | `.opencode/skills/`, `~/.config/opencode/skills/`, `.claude/skills/`, `~/.claude/skills/`, `.agents/skills/`, `~/.agents/skills/` | Yes — **config-level file list**: `instructions` array in `opencode.json`, supports glob patterns (e.g. `packages/*/AGENTS.md`) and remote URLs; combined with `AGENTS.md` | 2026-09-17 (Skills dir 2026-10-01) | https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/rules.mdx, https://opencode.ai/docs/skills/ |
| Codex CLI | `AGENTS.md` (or `AGENTS.override.md` if present) | `.agents/skills/` in every directory from cwd up to the repo root, `~/.agents/skills/` (user) | No in-file include line, config-level list, or drop-in directory — but **directory-hierarchy concatenation**: `AGENTS.md` files from repo root down to cwd are auto-joined (closer file wins on conflict), capped at `project_doc_max_bytes` (32 KiB default) | 2026-09-17 (Skills dir 2026-10-01) | https://learn.chatgpt.com/docs/agent-configuration/agents-md, https://learn.chatgpt.com/docs/build-skills |
| Gemini CLI | `GEMINI.md` | `.gemini/skills/` or the `.agents/skills/` alias (workspace), `~/.gemini/skills/` or `~/.agents/skills/` (user) | Yes — **in-file include line**: `@file.md` import syntax, relative/absolute paths (no documented `~` home-dir support), default max import depth 5 (configurable), nested imports, circular-import detection | 2026-09-17 (Skills dir 2026-10-01) | https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/memport.md, https://geminicli.com/docs/cli/skills/ |
| Cursor | `.cursor/rules/*.mdc` (also reads nested `AGENTS.md` per subdirectory) | `.cursor/skills/` or `.agents/skills/` (project), `~/.cursor/skills/` or `~/.agents/skills/` (user); also reads `.claude/skills/` and `.codex/skills/` | Yes — **drop-in directory**: every `.mdc` file in `.cursor/rules/` is auto-discovered, but a rule needs frontmatter (`description`, `globs`, `alwaysApply`) to load by itself, and one without any loads only when you `@`-mention it in chat (a plain `.md` file there is ignored for lacking frontmatter); `@filename.ts` is an in-rule context reference, not a cross-file include | 2026-09-17 (Skills dir 2026-10-01) | https://cursor.com/docs/rules, https://cursor.com/docs/skills |
| GitHub Copilot | `.github/copilot-instructions.md` (also reads `AGENTS.md`) | unverified | Yes — **drop-in directory**: `.github/instructions/*.instructions.md` files, scoped with an `applyTo` glob-pattern in frontmatter, combined with the global file; documented for VS Code, where the "Code Generation: Use Instruction Files" setting gates it, and not verified for other editors | 2026-09-17 | https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide, https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions |
| Zed | `AGENTS.md`, but only if no earlier name exists; the order is `.rules`, `.cursorrules`, `.windsurfrules`, `.clinerules`, `.github/copilot-instructions.md`, `AGENT.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, first match wins, so a repo that also carries any earlier name never loads its `AGENTS.md` | unverified | No — no in-file include/import syntax documented; project instructions use the **first matching file only**, not combined across files. Rules (the older feature) were deprecated in v1.4.0, replaced by Skills + Instructions | 2026-09-17 | https://github.com/zed-industries/zed/blob/main/docs/src/ai/instructions.md |
| Windsurf | `.devin/rules/*.md` (preferred) or `.windsurf/rules/*.md` (fallback); also still reads the legacy single-file `.windsurfrules` at the workspace root | unverified | Yes — **drop-in directory**: "One file per rule, each with its own activation mode" (`always_on`, `model_decision`, `glob`, `manual`); separate single always-on `global_rules.md` (6,000-char cap) vs. per-file workspace rules (12,000-char cap each) | 2026-09-17 | https://docs.windsurf.com/windsurf/cascade/memories |

## Verification notes

Verbatim excerpts backing each `Include support` claim above, fetched 2026-09-17 directly from
each harness's own primary documentation (not a mirror or a secondary summary). The Skills dir
values and the `AGENTS.md`, approval-dialog and frontmatter details were fetched the same way on
2026-10-01 and are listed in the last two entries below.

- **Claude Code** — https://code.claude.com/docs/en/memory — "Imported files can recursively
  import other files, with a maximum depth of four hops." "Both relative and absolute paths are
  allowed." A home-dir import is shown explicitly: `- @~/.claude/my-project-instructions.md`. This
  pass also found a second, separate mechanism not in the prior row: "Place markdown files in your
  project's `.claude/rules/` directory... All `.md` files are discovered recursively, so you can
  organize rules into subdirectories" — a genuine drop-in directory, distinct from `@import`.

- **OpenCode** — https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/rules.mdx —
  "All instruction files are combined with your `AGENTS.md` files." The `instructions` array in
  `opencode.json` accepts glob patterns (example given: `packages/*/AGENTS.md`) and remote URLs
  (5s fetch timeout). This corrects the
  [previous row](https://github.com/Osasuwu/jarvis-oss/blob/0397c401ea665c2fa6d39cf5e83b2dc8dd09f776/docs/harnesses.md) (checked 2026-10-01),
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

## Skills directory sources

Fetched 2026-10-01 from each harness's own documentation.

- **Claude Code** — https://code.claude.com/docs/en/skills — a personal skill sits at
  `~/.claude/skills/<skill-name>/SKILL.md` and a project skill at
  `.claude/skills/<skill-name>/SKILL.md`; the page says to commit the project one so the team gets it.
- **OpenCode** — https://opencode.ai/docs/skills/ — the six paths in the row.
- **Codex CLI** — https://learn.chatgpt.com/docs/build-skills — "For repositories, Codex scans
  `.agents/skills` in every directory from your current working directory up to the repository
  root." The user path is `$HOME/.agents/skills`; an admin path exists at `/etc/codex/skills`.
- **Gemini CLI** — https://geminicli.com/docs/cli/skills/ — "User skills: Located in
  `~/.gemini/skills/` or the `~/.agents/skills/` alias." "Workspace skills: Located in
  `.gemini/skills/` or the `.agents/skills/` alias."
- **Cursor** — https://cursor.com/docs/skills — "Skills are automatically loaded from these
  locations": `.agents/skills/` and `.cursor/skills/` (project), `~/.agents/skills/` and
  `~/.cursor/skills/` (user). "For compatibility, Cursor also loads skills from Claude and Codex
  directories: `.claude/skills/`, `.codex/skills/`, `~/.claude/skills/`, and `~/.codex/skills/`."
- **Copilot, Zed, Windsurf** — not checked; the cells say `unverified`.

## Other details checked on 2026-10-01

- **Claude Code approval dialog** — https://code.claude.com/docs/en/memory — "The first time Claude
  Code encounters external imports in a project, it shows an approval dialog... If you decline, the
  imports stay disabled."
- **Cursor frontmatter** — https://cursor.com/docs/rules — a rule with no frontmatter loads
  only when you `@`-mention it in chat.
- **Copilot path-specific files** — https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions —
  "These are specified in one or more NAME.instructions.md files within or below the
  .github/instructions directory in the repository."

## Notes

- Include-support mechanisms fall into four shapes, not three — see the column description above.
  Codex CLI's directory-hierarchy concatenation doesn't fit "include line", "config-level list", or
  "drop-in directory": nothing names which files to combine, and there's no single directory to
  drop a file into — the harness walks the directory tree itself.
- This table previously claimed Claude Code was the only harness with include-like
  support. That was true against the sources checked in the prior pass but not against each
  harness's own current primary docs — re-verification found five more harnesses (OpenCode, Gemini
  CLI, Cursor, GitHub Copilot, Windsurf) with a documented include-like mechanism, plus one more
  (Codex CLI) with a related-but-distinct hierarchy-concatenation behavior. Only Zed has none.
- Skills dir cells marked `unverified` (Copilot, Zed, Windsurf) were not checked against that
  harness's own documentation. The other rows were read from the pages under
  [Skills directory sources](#skills-directory-sources).
- AGENTS.md (the cross-tool standard, distinct from any single harness's own rules-file) is not a
  row here — this table is about harness-specific rules-file resolution, not the shared standard
  several harnesses also read.

## A harness not in this table

Ask the harness what it loaded instead of trusting a path: in Claude Code `/memory` and `/context`
list the files in play and the `InstructionsLoaded` hook fires when one loads; Gemini CLI has
`/memory show` and `gemini skills list --all`. To move to a new harness from Claude Code, `/init`
with `CLAUDE_CODE_NEW_INIT=1` reads `.cursor/rules/`, `.cursorrules`,
`.github/copilot-instructions.md`, `AGENTS.md`, `.devin/rules/`, `.windsurf/rules/` and
`.clinerules`. Both facts are from [How Claude remembers your project](https://code.claude.com/docs/en/memory) (checked 2026-10-01).
