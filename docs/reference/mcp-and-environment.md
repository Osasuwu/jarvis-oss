# MCP servers & environment health — pull-only reference

Evicted from the always-loaded [`docs/context/invariants.md`](../context/invariants.md) by
[#1418](https://github.com/Osasuwu/jarvis/issues/1418). Every fact here is load-bearing while
authoring or debugging an MCP bootstrap and dead weight otherwise — the definition of situational.
Pull this file when an MCP server misbehaves, when touching `mcp-memory/`, or when editing anything
under `.github/workflows/`.

These were the strongest `.claude/rules/` + `paths:` candidates in the milestone; that carrier is
blocked on [#1274](https://github.com/Osasuwu/jarvis/issues/1274), so they land at carrier 5 for now.
Re-home them once `paths:` resolution is settled.

## An MCP bootstrap's stdout IS the JSON-RPC transport

Anything a bootstrap prints — pip progress, diagnostics, a friendly banner — corrupts the handshake
and produces the silent-tools-missing symptom. It also runs under Claude Code's startup timeout, so
long work there is killed mid-flight. **Healing belongs in the SessionStart hook, never in a
bootstrap** ([#1312](https://github.com/Osasuwu/jarvis/issues/1312)).

## Manifest hash ≠ environment health

A hash-only stamp certifies a broken venv as healthy whenever code imports deps the manifest never
declared (`nest_asyncio`, `pythonjsonlogger`). The check must also **import-probe**. Even then it
guarantees only *satisfies the range*, not *reproduces CI's resolution*
([#1312](https://github.com/Osasuwu/jarvis/issues/1312); remaining gap tracked in
[#1313](https://github.com/Osasuwu/jarvis/issues/1313)).

## MCP servers are registered per-device by absolute path into the MAIN checkout

Registration lives in `~/.claude.json` and points at the main checkout, so every session in every
worktree shares exactly one long-lived `.venv`. **Worktrees are therefore never in the causal path of
an MCP failure** — [#1307](https://github.com/Osasuwu/jarvis/issues/1307) was misdiagnosed on that
assumption before [#1312](https://github.com/Osasuwu/jarvis/issues/1312) corrected it.

## `mcp-memory/schema.sql` is aspirational, not a bootstrap

No migration builds `memories` from zero. Reading the file as a from-scratch provisioning script will
mislead; treat it as the intended target shape.

## App permissions are installation-wide

GitHub App permissions apply across the whole installation, so a grant made for jarvis also hits
redrobot. Scope tokens per-workflow via `create-github-app-token` rather than widening the app.

## Post-compact context replay is harness-level, not jarvis-influenceable

Claude Code re-injects invoked skill bodies (capped 5,000 tok/skill, 25,000 total, truncated from
the file start) and the recently-read tool-result tail after a compaction, by design, for
behavioural continuity. Neither of jarvis's own compaction hooks can touch it: `pre-compact-backup.py`
(PreCompact) is a pure side-effect snapshot backup, and `session-context.py`'s `matcher:compact` path
is strictly additive (it prepends a memory pointer). No settings key, hook, or env var filters or
suppresses the replay — the only upstream lever, a `PostCompact` hook, is an open unimplemented
feature request (`anthropics/claude-code#14258`).

**Don't re-litigate this as a jarvis bug**
([#1451](https://github.com/Osasuwu/jarvis/issues/1451), decision `75e9aabd`).

Added to the always-loaded set by [#1453](https://github.com/Osasuwu/jarvis/pull/1453) while #1418
was in review, and re-homed here on rebase. It is a *don't chase this* rule: it fires when someone
notices the replay and starts investigating, not on an ordinary turn — the same situational shape as
the rest of this file. The pointer in `CONTEXT.md` → *Context delivery* is the route in.

## `config/SOUL.md` is this instance's identity

Shared by interactive and autonomous runs alike; the orchestrator runs routing-policy only. Stated
here for completeness — once SOUL.md itself loads, the file speaks for itself.
