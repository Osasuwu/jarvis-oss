# Agent Sandbox Boundaries

Date: 2026-04-15 (revised 2026-04-23 for Federation & Delegation Phase 0 — #341; revised 2026-04-26 for principal-aware permissions — #426; isatty fallback removed in #429)
Scope: Permission rules for **all principals** running Claude — interactive principal (`live`), autonomous loop, /delegate subagent, and the future dispatcher.

## Principal model (#426, #429)

Permissions depend on **who is running Claude**. Four principals — detection formerly lived in `scripts/principal.py`, retired in #1800 along with the rest of the custom install/harness scaffolding; the principal model below stays as documented intent, not a currently-enforced code path:

| Principal | Signal | Trust |
|---|---|---|
| `live` | Default when no `JARVIS_PRINCIPAL` and no headless env | Highest — principal watches, can correct in seconds |
| `autonomous` | `JARVIS_PRINCIPAL=autonomous` (set by scheduler/cron launchers) **or** `CLAUDE_CODE_NON_INTERACTIVE`/`CLAUDE_CODE_HEADLESS` | Low — corrections take hours |
| `subagent` | `JARVIS_PRINCIPAL=subagent` (auto-injection in /delegate is future work) | Medium — isolated worktree, parent reviews diff |
| `supervised` | `JARVIS_PRINCIPAL=supervised` (future dispatcher launcher will set this) | Delegated — permissions ⊆ supervisor's grant |

Detection chain (#429):
1. Explicit env `JARVIS_PRINCIPAL` — primary
2. Claude Code headless env vars → `autonomous`
3. Default → `live`

**Contract for autonomous entry points**: launchers that run Claude headless (the reactive-core executor, any cron/task wrapper) MUST set `JARVIS_PRINCIPAL` explicitly. The resident scheduler service that formerly demonstrated this via NSSM `AppEnvironmentExtra=JARVIS_PRINCIPAL=autonomous` was retired in #743 (replaced by the event-driven `agents/wake_driver.py`); when wake_driver gets a service launcher it carries the same contract. wake_driver itself owns no decisions and spawns nothing directly — the executor that spawns `claude -p` is the headless launcher bound by this rule.

The earlier "default-safe to autonomous" design (#426) was reverted in #429 because hook subprocesses always have piped stdin, so an `isatty()` fallback would mis-classify every interactive session as autonomous. Today's autonomous launchers explicitly set the env; future ones must do the same.

### Permission matrix (action × principal)

Action tier model is shared with `agents/safety.py` (T0 = AUTO, T1 = OWNER_QUEUE, T2 = BLOCKED).

| Action ↓ × Principal → | **live** | **autonomous** | **subagent** | **supervised** |
|---|---|---|---|---|
| **T0** narrow GitHub labels (`priority:*`, `area:*`, `needs-*`, `status:ready`); status updates; memory_store with `tag=auto-generated`; comment own PR; close issue with evidence; open jarvis tracking issue | ✅ act | ✅ act | ✅ act | ✅ act |
| **T1** code edit own repo; open PR; merge LOW-risk own PR per skill policy; `/implement` work; workflow files; drive-by fixes | ✅ act | ⚠️ enqueue `task_queue` *(future, lands with dispatcher)* | ✅ in worktree, no merge | ✅ within dispatcher grant *(future)* |
| **T2-canonical** repo-side sources of truth — see "Repo-level" table below | ⚠️ harness asks (hook exits 0) | ❌ block | ❌ block, escalate to `/implement` | ❌ block |
| **T2-mirror** `~/.claude/*` files kept in sync by hand from this repo — see "User-level" table below | ❌ block (edit the repo source, then copy by hand) | ❌ block | ❌ block | ❌ block |
| **T2-secret** `.env*` values; force push to main/master; impersonation; outbound to other humans (PR comments to others, Telegram, email) | ❌ always block | ❌ block | ❌ block | ❌ block |

Currently enforced in code: only **T2** rows, via `.claude/hooks/protected-files.py` — a standalone, non-principal-aware, fail-closed successor to the retired `scripts/protected-files.py` (#1800). T1 routing (autonomous-enqueue, supervised-grant) lands when the dispatcher ships; until then T1 work is principal-driven through `/implement` and `/delegate`.

## Protected Files

This table is the **single source of truth** — skills reference it rather than redefining their own lists.

### Policy

All code changes go through PRs with CI + code review — that is the primary safety gate. File-level blocking is reserved for the narrow surface where a subagent edit could leak secrets into git history *before* review sees it (i.e. weakening the scanners themselves), plus the enforcement scripts themselves (a non-live principal that can modify them can bypass the rest). Repo-level copies of everything else — `config/SOUL.md`, `CLAUDE.md` — may be edited in feature branches; the review process rejects anything wrong. Note: user-level mirrors under `~/.claude/` are still blocked for all principals (no PR process there; see "User-level" table below).

Redrobot follows the same policy: no file-level protection; CI + PR review is sufficient.

### Repo-level (jarvis working copy)

| File | Why |
|------|-----|
| `.gitleaks.toml` | Secret scanning config — weakening this allows secrets into git history before review |
| `.pre-commit-config.yaml` | Pre-commit hooks — same class as .gitleaks.toml |
| `scripts/secret-scanner.py` | The scanner itself — same blast radius |

### User-level (kept under `~/.claude/` in sync by hand — no installer, per #1800)

Editing these changes behaviour for **every Claude Code session on the device**, across all projects — strictly broader blast radius than the repo-level copies.

| File | Why |
|------|-----|
| `~/.claude/settings.json` | User-level hooks — run in every session on this device |
| `~/.claude/SOUL.md` | User-level identity — loaded via a **bare, line-start** `@SOUL.md` import in `~/.claude/CLAUDE.md` (#1328; the import only actually resolved from #1426 — before that it sat mid-prose and delivered nothing) |
| `~/.claude/skills/*/SKILL.md` | User-level skill definitions — available in every project |

The source of truth for these files lives in the jarvis repo (`config/SOUL.md`, `.claude-userlevel/skills/*/SKILL.md`; `~/.claude/settings.json` has no repo-side source and is edited directly). There is no installer — a change lands in the repo via PR, then gets copied by hand into `~/.claude/` on each device. Direct edits to `~/.claude/` without updating the repo source drift silently, since nothing re-syncs them.

Enforced via PreToolUse hook: `.claude/hooks/protected-files.py` (covers both surfaces; user-level paths anchored to `Path.home() / ".claude"` or `$JARVIS_CLAUDE_HOME` override). Unlike the retired `scripts/protected-files.py` (#426's principal-aware bypass for the `live` principal), this standalone successor always blocks — it carries no principal-detection seam, so both canonical and mirror files block for every principal (#1800).

## Branch Rules

- Subagents work in feature branches (`feat/<N>-<slug>`), never commit directly to main
- One branch per issue — no multi-issue branches from agents
- Agent must push before reporting "done" — unpushed work is unverifiable

## Memory Rules

- Agents CAN: store project/decision memories, record outcomes
- Agents CANNOT: delete memories without principal confirmation (soft delete provides safety net)
- Secret scanner blocks credential values in memory_store

## Scope Rules

- Agent should only modify files relevant to its assigned issue
- If an agent needs to change a protected file, it must document the needed change in the PR description and leave it for the principal
- Cross-project changes (jarvis ↔ redrobot) require explicit mention in the issue

## Escalation

Agent must STOP and report (not attempt) when:
1. It needs to modify a protected file
2. It encounters a merge conflict it can't resolve
3. Tests fail and the fix requires architectural changes
4. The issue requirements are ambiguous and implementation could go multiple ways
5. The change would affect another project's behavior
