# Compression Resilience

**Status:** Sprint 1 SHIPPED — milestone #25 closed 2026-04-21 (all three phases: #278 PreCompact hook, #279 session-context resume injection, #280 /end reads Supabase). Parent: #277 (CLOSED). This doc was the PRD; live behaviour reflects the shipped Phase 1/2/3 below. Inline `Status (#N): shipped` markers annotate per-phase ship state.
**Owner directive (2026-04-21):** "Jarvis needs to run autonomously for many
hours and still produce a high-quality session close after one or more
compactions."

## Problem

Claude Code auto-compacts long sessions. Compaction replaces raw conversation
history with an LLM-generated summary. The summary is good at capturing
narrative but lossy on the artefacts `/end` depends on:

- **Decisions** — rationale, alternatives considered, reversibility
- **Behavioural observations** — mid-session feedback ("stop doing X")
- **Exact action log** — which files were edited, which PRs opened, which
  memories written

Current state (pre-sprint):

- `SessionStart:compact` hook re-injects `working_state_*` after compaction
  — forward recall works.
- Nothing captures pre-compact state. Backward recall is lossy.
- `/end` scans the **post-compact** conversation. It operates on the summary,
  not on what actually happened.

Net effect: a session that survives auto-compaction produces a degraded
`/end` output — decisions get lost, behavioural lessons get paraphrased
into vague "the owner gave feedback on X".

## Solution — three phases

### Phase 1 — capture pre-compact snapshot (issue #278)

**Status (#278): shipped** (closed 2026-04-21).

Register a `PreCompact` hook that runs **before** compaction.

1. Read hook input from stdin (`session_id`, `transcript_path`, `trigger`).
2. Parse the full session JSONL transcript at `transcript_path`.
3. Extract structured artefacts:
   - User messages (deduped, skipping command-messages / skill bootstraps)
   - Tool calls (Bash, Edit/Write targets, memory ops, GitHub actions, chapters)
   - Last TodoWrite state — the canonical "open loops" view
   - Last assistant text block
   - git branch (from most recent entry)
4. Compose a markdown snapshot under 30KB. Truncate head if transcript
   >10K lines (keep last 8K with a drop counter).
5. Upsert to Supabase `memories`:
   - `name = session_snapshot_<session_id>`
   - `type = project`, `project = <detected from cwd>`
   - `tags = ["session-snapshot", "compression-resilience", <trigger>]`
   - `source_provenance = "hook:pre-compact"`
6. **Never block compaction.** On Supabase failure, write a local fallback
   file at `.claude/session-snapshots/<session_id>.md` and exit 0.

Sized to ship without schema changes — snapshots live in the existing
`memories` table.

### Phase 2 — inject snapshot on resume (issue #279)

**Status (#279): shipped** (closed 2026-04-21).

Extend `scripts/session-context.py` (the `SessionStart:compact` hook
loader): when running in the `compact` matcher, look up
`session_snapshot_<session_id>` and emit it under a
`## Pre-Compact Recovery` section.

This gives the post-compact Claude immediate access to the structured
artefacts the LLM-summary smoothed over. Purely additive — the existing
working-state re-injection keeps working.

Settings.json caveat: the existing SessionStart hook was a single
`&&`-chained shell command (`device-info && cat SOUL && session-context`).
Claude Code pipes hook-input JSON to each subprocess's stdin, but in a
shell chain only the first process in the pipeline reliably receives
that stdin — subsequent commands see EOF. Phase 2 splits the `hooks`
array into two entries (prelude + `session-context.py`) so each gets
hook-input stdin from Claude Code directly. Output order is preserved.

### Phase 3 — /end reads from Supabase (issue #280)

**Status (#280): shipped** (closed 2026-04-21).

Rework `.claude-userlevel/skills/end/SKILL.md` (installs to `~/.claude/skills/end/`) so the primary source of truth for
decisions, behavioural notes, and action log is Supabase (via
`session_snapshot_*` and `record_decision` episodes), **not** the live
conversation. The conversation is a hint; Supabase is authoritative.

This makes `/end` behave identically before and after compaction.

## Acceptance (sprint-level)

- PreCompact hook fires on both `auto` and `manual` triggers without
  blocking compaction.
- `/compact` in a medium session persists `session_snapshot_<session_id>`
  to Supabase within 10s.
- After compaction, session-context emits a `## Pre-Compact Recovery`
  section.
- `/end` quality is indistinguishable across 0 vs 1+ compactions on a
  benchmark session.

## Non-goals

- Restoring the full raw transcript inline (too large, defeats compaction).
- Retroactive snapshots for sessions compacted before this lands.
- Cross-device snapshot replication (Supabase already handles it).

## Risk register

- **Hook failure blocking compaction** — mitigated by top-level try/except
  and exit 0 on every path (see `scripts/pre-compact-backup.py`).
- **Snapshot bloats the memories table** — 30KB × N sessions. Add a
  consolidation job if it exceeds a month of retention (not in Sprint 1).
- **Secrets in transcript** — `Bash` inputs may contain tokens. The
  existing `secret-scanner.py` PreToolUse hook already catches these at
  source; snapshots inherit whatever slipped through. Revisit if we
  discover leaks.
