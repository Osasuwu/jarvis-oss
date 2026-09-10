# Perception → task_queue ingest

Modules: `agents/perception_*.py` (one per source). Federation & Delegation Sprint 4
(milestone #32). Architecture-doc issue: #386. Implementation issues:
#388 (GitHub), #389 (self-perception via morning_check). Telegram (#387)
deferred — see "Future sources" below.

The task-dispatch path (S2-3, `agents/task_dispatch.py` — the reactive-core successor to the retired dispatcher, itself demolished with reactive-core in #1802) consumes
`task_queue` rows. Sprint 1–3 wired the **consume** side. Sprint 4 wires
the **produce** side: external signals → rows → dispatcher's existing FSM.

## Why this doc lands first

Three implementations are about to be written. Without a single source of
truth for `approved_by` namespace, `auto_dispatch` policy, and
`idempotency_key` shape, three subagents will pick three conventions and
Sprint 5 gets spent unifying them. This doc settles the conventions
**before** code lands.

Principal decisions encoded here (resolved 2026-04-25, [#386
comment](https://github.com/Osasuwu/jarvis/issues/386)):

1. Safety gate is **always** in the FSM. Strictness is a gradient over
   `(source, tier, executor model)` — never on/off.
2. Telegram ingest deferred — covered by Claude Code Channels (live
   session) + GitHub ingest (autonomous).
3. GitHub `source:1-auto` label **is** the human nod — no second approval
   gate.

## Vocabulary — the three "tiers"

The word "tier" appears in multiple distinct registers in this codebase.
Mixing them is the single biggest source of confusion when wiring
perception. Keep them separate.

**Label note (#1707):** the board labels `tier:1-auto` / `tier:2-review`
/ `tier:3-human` were retired repo-wide by #1707 — they had become dead
plan-review vocabulary and were replaced there by an ordinal
`classify() -> {1,2,3}` plus `label_for() -> "afk:2-plan"/"afk:3-human"/
None`. That `afk:*` vocabulary answers a different question ("does this
change need a plan review before merge?", a complexity/shared-surface
judgment) than Source tier answers here ("how much do we trust this
*incoming signal* to auto-dispatch?"). Reusing `afk:2-plan`/`afk:3-human`
for Source tier would conflate two orthogonal axes and recreate the
exact ambiguity #1707 exists to resolve. Source tier therefore gets its
own label prefix, `source:*`, never `tier:*` or `afk:*` — see decision
record cited at the end of this section.

| Concept | Where it lives | Values | Purpose |
|---------|---------------|--------|---------|
| **Source tier** | Perception modules + GitHub issue labels (`source:1-auto`, `source:2-review`, `source:3-human`) | 1, 2, 3 | Classifies an *incoming signal*: how autonomous can dispatch be? |
| **`safety.Tier`** | `agents/safety.py` (IntEnum: `AUTO`, `OWNER_QUEUE`, `BLOCKED`) | 0, 1, 2 | Classifies an *outgoing action* in `gate(tool_name, action, target, area)`. |
| **`task_queue.auto_dispatch`** | DB column (boolean) | true / false | Filters what dispatcher's `poll_queue_node` picks up. |
| **Plan-review class** (`agents/plan_classifier.py`, #1685/#1707) | GitHub issue labels (`afk:2-plan`, `afk:3-human`) | 1, 2, 3 | Classifies a *change-set's complexity*: does it need a locked plan / owner review before merge? Unrelated to perception's ingest-trust question. |

Source tier and `safety.Tier` are independent dimensions: source tier is
"how trusted is the input," `safety.Tier` is "how dangerous is the
output." A `source:1-auto` GitHub issue can still produce a Tier-2 BLOCKED
action (e.g. an issue body that asks the agent to delete `.env`).

The bridge: perception modules translate **source tier → `auto_dispatch`
boolean** at ingest time. The safety gate runs later, on the actual
mutation, regardless of source tier.

## Mapping table — source tier → row shape

| Source tier | `auto_dispatch` | Dispatcher behaviour |
|-------------|-----------------|----------------------|
| `source:1-auto` | `true` | Picked up on next tick. Escalation triggers still apply. |
| `source:2-review` | `false` | Row sits in `pending`. Principal flips `auto_dispatch=true` after review (or runs `/implement` manually). |
| `source:3-human` | `false` | Row exists for tracking only. Dispatcher never touches it. Principal-driven from start to finish. |

`auto_dispatch=true` is the *only* signal `dispatcher.poll_queue_node`
honours. The source tier label is metadata for humans and for
post-hoc reporting; it does not change dispatcher behaviour beyond the
boolean.

## Sources

Every perception source produces a `task_queue` row with the same
columns. The columns differ in *content* (where the values come from),
not in *shape* (every row has all the columns).

### GitHub ingest (#388, Sprint 4)

| Column | Value |
|--------|-------|
| `goal` | Issue title + body, capped at N chars |
| `scope_files[]` | Files referenced in issue body via fenced code or backtick paths; empty if none parsed |
| `approved_by` | `github:issue:<owner>/<repo>#<N>` |
| `approved_at` | Timestamp the `status:ready` label was applied (best-effort: poll-tick time if API doesn't give it cheaply) |
| `approved_scope_hash` | sha256 of sorted `scope_files[]` (matches dispatcher's `_hash_scope_files`) |
| `auto_dispatch` | `true` iff `source:1-auto` label present; `false` for `source:2-review` and `source:3-human` |
| `idempotency_key` | `sha256(repo \| issue_number \| sorted_label_set)` |

Trigger: poll-tick scans `gh issue list --label status:ready --label source:*`
on each repo in the per-repo allowlist. Webhook is a stretch goal.

Allowlist: `Osasuwu/jarvis` initially. `SergazyNarynov/redrobot` after
the GitHub-ingest path soaks for a sprint. Cross-repo writes stay Tier
2 BLOCKED in `safety.gate` regardless — perception just ingests, the
gate decides whether the resulting action fires.

Loop closure: when row transitions to `done`, perception module posts a
comment on the source issue with the PR link. (Implementation note for
#388: hook on `task_queue` UPDATE where status=done and approved_by
prefix=`github:issue:`.)

Caveat: as of Sprint 4, dispatch is fire-and-forget — it sets
`dispatched_at` and never flips status to `done` itself (the fire-and-forget
spawn now lives in `agents/executor.py`, salvaged
from the retired dispatcher — itself demolished with reactive-core in #1802). The done-watcher in #388
will idle until a result-collection path lands (future sprint) or until
the principal flips status manually via `/verify`. Implementers: write the
watcher, but expect zero firings until that upstream change.

Open in flight: what happens if labels change post-ingest — re-tier the
existing row, or freeze at ingest-time? **Decision: freeze at ingest.**
Re-tier introduces a race where the dispatcher reads source:1-auto but the row
silently became source:3-human. Principal can close + re-open the issue if a re-tier
is needed; idempotency_key includes the label set, so re-applying labels
produces a fresh key and a fresh row.

### Self-perception via morning_check (#389, Sprint 4)

| Column | Value |
|--------|-------|
| `goal` | Human-readable alarm description from `morning_check.py` |
| `scope_files[]` | `[]` — alarms describe symptoms, not file scope |
| `approved_by` | `cron:morning_check` |
| `approved_at` | Cron tick time |
| `approved_scope_hash` | sha256 of empty list (so drift checks pass trivially — there's no scope to drift from) |
| `auto_dispatch` | `false` always — every self-perception row is `source:3-human` |
| `idempotency_key` | `sha256(YYYY-MM-DD \| alarm_category \| sha256(details_summary))` |

Trigger: `morning_check.py --enqueue-on-alarm` (default off interactive,
on under cron). Each distinct alarm category produces one row per day.
Same alarm next day → new key (date is in the formula) → new row.

Why all source:3: an agent that auto-fixes its own observability alarms
is one config bug away from making things worse silently. Self-modify
is human-only this sprint. (A future "self-heal Tier 1" surface needs
its own design issue and its own audit trail.)

### Future sources (slot reserved, not implemented)

Listed so conventions don't shift when these land:

| Source | `approved_by` prefix | `idempotency_key` formula | Default `auto_dispatch` |
|--------|----------------------|----------------------------|--------------------------|
| Telegram (deferred from #387) | `telegram:<principal_id>` | `sha256(chat_id \| message_id)` | `false` (principal classifies inline via bot) |
| Email | `email:<from_address>` | `sha256(message_id)` | `false` |
| Calendar | `calendar:<event_uid>` | `sha256(event_uid \| occurrence_start)` | varies — recurring events default `false` |
| Voice | `voice:<principal>` | `sha256(audio_sha256)` | `false` (transcription happens before ingest) |

Rule for adding a new source: extend this table in the same PR that adds
the perception module. The `approved_by` prefix MUST be unique across
sources — that's how downstream tooling (escalation, reporting, audit)
filters by source.

## FSM integration

Perception is a producer of `pending` rows. It does not introduce new
states; the existing FSM is sufficient.

```
                    ┌──────────────────┐
                    │  Source signal   │  (Telegram message, GitHub label,
                    │                  │   morning_check alarm, …)
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Perception module│  classify(source) → source_tier
                    │ agents/perception_*│ build row, sha256 idempotency_key
                    └────────┬─────────┘
                             │ INSERT (ON CONFLICT idempotency_key DO NOTHING)
                             ▼
                ┌────────────────────────┐
                │ task_queue (pending)   │
                │ auto_dispatch=…        │
                └────────────┬───────────┘
                             │
              ┌──────────────┴──────────────┐
              │ auto_dispatch=true           │ auto_dispatch=false
              ▼                              ▼
    ┌──────────────────┐         ┌──────────────────────┐
    │ dispatcher       │         │ Principal / /implement│
    │ poll_queue_node  │         │ (manual)             │
    └────────┬─────────┘         └──────────┬───────────┘
             │                              │
             ▼                              ▼
    ┌──────────────────┐         ┌──────────────────────┐
    │ evaluate_node    │         │ /implement skill     │
    │ + escalate (S2-4)│         │ (PR pipeline)        │
    └────────┬─────────┘         └──────────┬───────────┘
             │                              │
       (existing dispatcher FSM →           │
        dispatched / escalated /            │
        rejected / done)                    │
                                            ▼
                                  (status: done | rejected
                                   via /implement + merge)
```

Existing FSM transitions (from `task_queue` migration, unchanged):

```
pending    → dispatched | escalated | rejected
dispatched → done | escalated
escalated  → pending           (principal re-approves)
done       → (terminal)
rejected   → (terminal)
```

Perception adds *no* new state. Every transition still happens in the
dispatcher / principal-driven path; perception's job ends the moment the row
is INSERTed.

### Idempotency under retry

Perception modules are expected to crash, restart, run twice in
parallel during NSSM service swap, and otherwise misbehave. The
DB-level UNIQUE constraint on `idempotency_key` is the only thing
guaranteeing at-most-once enqueue.

Every perception module MUST:

1. Compute the key before the INSERT (deterministic from source payload
   alone — no timestamps, no random salt).
2. Use the supabase-py upsert idiom — never raw insert that lets the
   unique-constraint violation propagate as an exception:
   ```python
   client.table("task_queue").upsert(
       row,
       on_conflict="idempotency_key",
       ignore_duplicates=True,
   ).execute()
   ```
   PostgREST translates this to `INSERT ... ON CONFLICT
   (idempotency_key) DO NOTHING` server-side; a retry sees zero rows
   affected and exits the tick clean. (Precedent in-repo:
   `scripts/pre-compact-backup.py` uses the same pattern for memories.)
3. Log the key prefix on every tick (first 12 chars) so the morning
   check can grep for re-enqueue patterns.

### Crash recovery

Perception modules are stateless across ticks. State lives in the
upstream source (GitHub issue labels, Telegram message ids, cron run
timestamps). A perception module that crashes mid-tick and restarts
re-reads the source, recomputes keys, and re-attempts the INSERT — the
unique constraint absorbs the duplicate.

## Safety gate policy

The gate is **always in the FSM**. It does not skip for any source.
Strictness varies along three axes:

```
gate_strictness = f(source, source_tier, executor_model)
```

This is not a binary "on/off" — it's a parameterisation of the existing
`safety.gate()` rules. Sprint 4 does not extend the gate's call
signature (`agents/safety.py`, demolished with reactive-core in #1802). What changes is
*which actions* a perception-spawned dispatch is allowed to attempt:

| Axis | Effect on strictness | Example |
|------|---------------------|---------|
| **Source** (`approved_by` prefix) | Trusted prefixes (`github:issue:` from allowlisted repo, `cron:morning_check` for own tasks) get the standard whitelist. Untrusted prefixes (`external:*` — future) get a narrower whitelist. | A `cron:` source can `audit_log:insert` (Tier 0); an `external:` source cannot. |
| **Source tier** | Higher tier (1 → 2 → 3) → more checks. Tier 1 fires through the existing gate. Tier 2/3 don't reach the gate at all because `auto_dispatch=false`. | Tier 3 is principal-driven by definition; gate checks the principal's actions, not the tier. |
| **Executor model** | Stronger model (e.g. Claude Opus) gets looser scope-file diffing tolerance; weaker (Haiku) gets tighter. *Implementation lives in `agents/dispatcher.py` when model selection is wired in — not Sprint 4.* | Future: dispatcher reads model hint from row metadata, picks gate config. |

What this means concretely for Sprint 4 implementation:

- **#388 (GitHub ingest)**: no gate changes. `source:1-auto` rows go through the existing gate. The label is the human nod; the gate is the technical guardrail.
- **#389 (self-perception)**: every row is source:3 → `auto_dispatch=false` → never reaches dispatch path → gate not invoked. Principal triggers the actual fix manually.
- **Future sources**: extend the `_TIER0_*` / `_TIER2_*` constants in `agents/safety.py` with source-aware classifications (or add a new `_TIER0_PERCEPTION_*` set). Don't touch the `gate()` signature.

### Why source tier ≠ skip-gate

Principal-stated principle: gate is always there. The intuition that pushed
back on "gate skip for cron-internal tasks" was correct — once you allow
*any* source to skip the gate, the audit trail breaks and the
single-bottleneck property goes away. The gate's job is to be the last
line; perception does not get a back door.

## Conventions checklist (for #388 / #389 implementers)

When writing `agents/perception_<source>.py`, hit every one:

- [ ] Module-level constant `SOURCE = "<source>"` (matches `approved_by` prefix without the colon)
- [ ] Pure `_build_row(payload) -> dict` function — no DB, no I/O. Unit-testable.
- [ ] Pure `_idempotency_key(payload) -> str` — sha256 hex of the formula in this doc's table. `agents.safety.idempotency_key` (demolished with reactive-core in #1802) previously covered the `(agent_id, action, target, scope_hash)` shape; compute directly with `hashlib.sha256(...).hexdigest()` instead.
- [ ] `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` — never raw INSERT
- [ ] Per-source allowlist as a module constant (chat ids, repos, etc.); reject unknown principals before computing the key
- [ ] Source tier classification (`source:1-auto` / `source:2-review` / `source:3-human`) — translate to `auto_dispatch` boolean per the mapping table above
- [ ] Tests: idempotency (running the tick twice produces zero new rows), allowlist rejection, row shape correctness
- [ ] Doc cross-link: extend the relevant "Sources" subsection in this file with anything source-specific

## Cross-references

- `agents/task_dispatch.py` — what consumes the rows perception produces (reactive-core successor to the retired dispatcher; demolished with reactive-core in #1802)
- `safety.py` — `safety.Tier` (the *other* tier vocabulary) and the gate model (demolished with reactive-core in #1802)
- escalation — what fires after perception INSERTs and dispatcher picks up (demolished with reactive-core in #1802)
- [`supabase/schema.sql`](../../supabase/schema.sql) + [`supabase/migrations/20260422134442_create_task_queue.sql`](../../supabase/migrations/20260422134442_create_task_queue.sql) — `task_queue` columns and FSM check constraint
- [`agents/README.md`](../../agents/README.md) — agent module index

## Smoke test trace

End-to-end smoke iterations of #390. Each row is one autonomous run:
issue → perception tick → task_queue → dispatcher → claude -p → PR.

All three runs were dispatched on workshop PC (routine-host machine) on
2026-04-25. The dispatcher path was the same; the body of each issue
differed deliberately to exercise three task shapes (doc append / test
add / docstring edit).

Historical note: at the time of this trace the Source-tier label was
still named `tier:1-auto` (pre-#1707 vocabulary). The trace below is
left as-recorded; read `tier:1-auto` here as `source:1-auto` per the
rename above.

### Iteration 1 — doc append

| t | UTC | Event |
|---|-----|-------|
| t0 | 10:17:20 | Issue #399 created (`tier:1-auto` + `status:ready`) |
| t1 | 10:17:24 | `perception_github.poll_tick()` → row inserted (`auto_dispatch=true`, key `b5ca4064…`) |
| t2 | 12:28:09 | Dispatcher tick → audit `dispatch:success`, row → `dispatched`, `claude -p` spawned |
| t3 | 12:29:00 | Branch `smoke/390-iter-1-seed-trace-section` pushed; PR #400 opened |
| t4 | 12:29:30 | Orchestrator merged PR #400; row → `done` |

Notes:
- A first dispatch attempt at 10:18:31 ran on the NSSM service (which
  starts as `LocalSystem`). The spawned `claude -p` exited silently —
  `LocalSystem` cannot read the Claude Max session credentials in
  `%USERPROFILE%\.claude\.credentials.json`. The row was reset to
  `pending` and re-dispatched from a user-context shell (the t2/t3 row
  above). Pipeline up to t2 was identical in both attempts; only the
  spawned subprocess differed in auth context.
- `auto_dispatch` was honoured per perception.md mapping (`tier:1-auto`
  → `true`).

### Iteration 2 — test add

| t | UTC | Event |
|---|-----|-------|
| t0 | 12:33:09 | Issue #401 created |
| t1 | 12:33:14 | Perception tick → row inserted (key `ea5def04…`) |
| t2_v1 | 12:33:19 | Dispatcher tick → audit `dispatch:success`, `claude -p` spawned — but no follow-up activity (flake) |
| reset | 12:40:41 | Row reset to `pending`, redispatched |
| t2_v2 | 12:40:47 | Second dispatch → spawn |
| t3 | 12:41:57 | PR #402 opened |
| t4 | 12:42:00 | Orchestrator merged PR #402; row → `done` |

### Iteration 3 — docstring edit

| t | UTC | Event |
|---|-----|-------|
| t0 | 12:43:25 | Issue #403 created |
| t1 | 12:43:30 | Perception tick → row inserted (key `6d76819e…`) |
| t2_v1 | 12:43:36 | Dispatcher tick → spawn → no follow-up activity (flake) |
| reset | 13:12:22 | Row reset, redispatched |
| t2_v2 | 13:12:25 | Second dispatch → spawn |
| t3 | 13:14:10 | PR #404 opened |
| t4 | 13:14:30 | Orchestrator merged PR #404; row → `done` |

### Findings

- **Producer → FSM → consumer pipeline works end-to-end.** Issue label
  → `task_queue` row → dispatcher pick-up → `claude -p` spawn → branch
  → PR — every transition observable in `audit_log` and `task_queue`.
- **Service-account auth gap.** NSSM service runs as `LocalSystem`,
  which has no access to the user profile where `claude` stores Max
  session creds. Spawned `claude -p` exits silently
  (`stdout`/`stderr` are `DEVNULL` per dispatcher fire-and-forget).
  Workaround for now: dispatch from a user-context shell. Long-term
  fix: change service `ObjectName` to `.\PC4_v` (requires logon-as-
  service right) or migrate to Task Scheduler with "run only when user
  is logged on". Tracked separately.
- **Back-to-back spawn flake.** Both iter-2 and iter-3 first attempts
  produced no `claude.exe` process and no follow-up activity even
  though `Popen` returned success. Retry of the same row ~5 minutes
  later succeeded both times. Hypothesis: a brief lock or session
  conflict in `~/.claude/` after a previous spawn completes.
  Tracked separately.
- **Done-watcher remains idle.** Per perception.md caveat, the
  dispatcher is fire-and-forget and never sets `status=done`. The
  orchestrator manually flipped each row to `done` after merging the
  resulting PR. No `notify_completed_issues` firings yet, as
  designed.
