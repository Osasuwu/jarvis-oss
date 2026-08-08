# Ralph-loop — retired (historical reference only)

> **The driver (`scripts/ralph-loop.ps1`) has been deleted. Do not attempt to run it —
> this doc is kept only so its findings survive; nothing described below is operational.**
> Retired 2026-08-08 in favor of the reactive-core orchestrator (M44,
> `agents/wake_driver.py` + task queue): once #1455 (Path B producer) ships, the same
> park→event→resume shape this driver approximated by hand (owner answers a question in
> a GitHub issue, re-runs the script) is native — no separate mechanism to maintain.
> Decision: jarvis `record_decision` episode for jarvis#1461 (pivot away from a
> standalone loop once the overlap with reactive core's own closing-the-loop machinery
> was noticed).

## What it was

`scripts/ralph-loop.ps1` ran a task through repeated, genuinely fresh `claude -p`
invocations instead of one long session — no `--continue`/`--resume`/`--session-id`, so
there was nothing for any single iteration to compact. Progress persisted externally in
jarvis `working_state` (Supabase memory via `mcp__memory` tools), not in any LLM's
context window. This is the "Ralph Wiggum" technique (Geoffrey Huntley, 2025): a dumb
outer loop feeding the same task prompt to a fresh agent every round, relying on
external state rather than in-context memory.

Chosen over pre-built ralph frameworks (Linear-backed, `PRD.md`-backed,
`IMPLEMENTATION_PLAN.md`-backed) because every one of them owns its own state
file/service that would have duplicated `working_state` instead of reusing jarvis's
existing handoff surface.

## Why it was retired, not kept running

The driver's HITL handling — stop cleanly when a phase needs a human, resume when the
human answers — is structurally the same problem reactive core's Path B (park a task on
`blocked_by_task_id`, an event wakes it when unblocked) exists to solve generally, for
every task in the system, not just ones run through this one script. Running both would
mean maintaining two closing-the-loop mechanisms with the same shape; the standalone
script would only ever be a manual imitation of what M44 delivers natively once #1455
ships. Deleting it removes the risk of someone reaching for the imitation out of habit
once the real thing exists.

## Findings worth keeping

These held up under one real validation run (task: jarvis#1455, needs-grill,
design-heavy; `bypassPermissions`, sonnet, $6/iteration cap) and are likely to matter
again wherever reactive core ends up driving multi-phase work:

- **Per-iteration/session startup floor.** Measured 2026-08-08 on Main PC with a
  trivial no-op prompt: **~66k tokens and ~$0.21** against a ~136k auto-compact trigger
  — roughly 70k of usable window per fresh context. Any driver that re-spawns fresh
  sessions needs to budget against this floor.
- **The slim MCP profile did not help headless sessions.** A/B on identical no-op
  prompts, same cwd/model: `mcp-slim.json` + `--strict-mcp-config` measured 66 815
  startup tokens vs 65 555 on the full registered surface — no gain, within noise. This
  headless finding held up as the general one: the profile was later measured at ~0
  token benefit overall and removed outright (jarvis#1471, decision `3e742a0e`) —
  `config/mcp-slim.json` and `docs/reference/mcp-slim-profile.md` no longer exist.
- **One phase per fresh context, not "make progress."** A prompt that just says
  "implement this issue" replays a no-op exit every round when the issue needs a grill
  artifact first (SOUL's grill trigger checkbox fires, `/implement` exits
  `grill_required`) — burning the startup floor above for zero progress each time.
  Naming the routing signal explicitly (which phase of `/reason`→`/grill`→`/to-spec`→
  `/to-tickets`→`/implement`→`/rework` is earliest-not-done) and doing exactly one per
  iteration avoided that deadlock.
- **HITL phases need a distinct "stop and wait" signal, not "keep going."** A binary
  done/not-done signal makes a human-blocked iteration (owner hasn't answered a grill
  question yet) indistinguishable from one that should retry — re-spawning a full-price
  iteration every round just to rediscover the same missing answer. A three-valued
  outcome (done / blocked-on-human / retry) is the minimum needed to avoid that.
- **Compaction counts should be measured, not assumed.** Locating a spawned session's
  own transcript by session_id and counting
  `"compactMetadata":{"trigger":"auto"` records turned "this avoids autocompaction" from
  a claim into a checkable number — 0 across both validation iterations.
- **Host process hazard (PowerShell-specific, keep if any future driver shells out from
  PS 5.1):** a native command's stderr lines get wrapped in terminating ErrorRecords
  under `2>&1` when the parent host runs `$ErrorActionPreference = 'Stop'` (as the
  Claude Code PowerShell tool does) — a single benign hook-failure warning killed a full
  iteration's already-completed result. Not reproducible in a plain console
  (`$ErrorActionPreference` defaults to `Continue` there), which is why smoke-testing in
  an ordinary terminal missed it.
- **Host-managed session auth does not propagate to spawned children.** Children of a
  `claude -p` subprocess read `~/.claude/.credentials.json` directly; a host-managed
  session's (desktop app, VS Code extension) OAuth refresh happens out-of-band and does
  not write back to that file, so it goes stale and every child fails auth in ~30s with
  zero token usage. Fix was a plain `claude auth login` run in an ordinary terminal
  outside any host-managed session, rewriting the file with independently-refreshable
  tokens. Full mechanism in memory `claude_p_subprocess_auth_fails_host_managed_session`.

## State (while it ran)

`working_state_jarvis` in Supabase memory was the loop's only state — same surface
`/end`, `/grill`, and `/implement` already read/write. This surface is unaffected by the
retirement; the concurrency bug the loop's fast iteration cadence exposed in it
(concurrent sessions silently dropping each other's writes) is tracked independently as
jarvis#1473 and is not specific to ralph-loop.
