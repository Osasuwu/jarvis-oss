# C17 — Events Substrate (Design 1-pager)

**Status:** design-locked 2026-04-29 (Sprint #35, [#475](https://github.com/Osasuwu/jarvis/issues/475)). Migration → [#476](https://github.com/Osasuwu/jarvis/issues/476). First writer → [#477](https://github.com/Osasuwu/jarvis/issues/477).
**Parent:** [`jarvis-v2-redesign.md` § C17](jarvis-v2-redesign.md#c17--observability--audit) (L2), with L3 leans at [§C17 L3](jarvis-v2-redesign.md#c17--observability--audit-1).
**Migration order:** position #1 — nothing else has somewhere to write until this ships ([§Bootstrap protocol & migration order](jarvis-v2-redesign.md#bootstrap-protocol--migration-order)).

This doc locks the schema, column naming, propagation rules, write semantics, and initial action vocabulary so #476 (SQL) and #477 (first writer) can land without re-design. SQL is **not** in this doc.

---

## 1. Schema (logical)

Single canonical `events_canonical` table. All observability writes go through it. Other tables (`task_outcomes`, `episodes`, `audit_log`, `known_unknowns`, `*_last_run` memories) become views over it during the cutover wave (post-Sprint 35).

| Column | Type | Notes |
|---|---|---|
| `event_id` | `uuid PK` | Generated server-side (`gen_random_uuid()`). |
| `trace_id` | `uuid NOT NULL` | Groups all events from one initiating context (owner message, scheduled fire, subagent dispatch). |
| `parent_event_id` | `uuid NULL` | Nesting — subagent's events point to spawning event in parent trace. |
| `ts` | `timestamptz NOT NULL DEFAULT now()` | Insert time. |
| `actor` | `text NOT NULL` | `jarvis-main`, `jarvis-subagent-<id>`, `hook-<name>`, `task-<name>`, `skill:<name>`, `user`. Open vocabulary. |
| `action` | `text NOT NULL` | Reserved values listed in §5. Open vocabulary; new values added via PR. |
| `payload` | `jsonb NOT NULL DEFAULT '{}'` | Type-specific. OTel keys when applicable (§2). |
| `outcome` | `event_outcome enum NULL` | `success` \| `failure` \| `timeout` \| `partial`. NULL allowed for events without a binary outcome (e.g., `episode_started`). |
| `cost_tokens` | `int NULL` | Populated for token-incurring events only. |
| `cost_usd` | `numeric(12,6) NULL` | Populated for cost-incurring events only. |
| `redacted` | `bool NOT NULL DEFAULT false` | True when secret-redaction was applied to `payload`. |
| `degraded` | `bool NOT NULL DEFAULT false` | True for events replayed from in-memory buffer after a transient pg outage (§4). |

**Why one table not many:** views are cheap; tables drift. Past pain (`audit_log` declared in code but not in schema; `*_last_run` memories abusing `memories` as task-heartbeat store) was the cost of denormalizing observability. Per [`jarvis-v2-redesign.md` §substrate](jarvis-v2-redesign.md#substrate).

**Why `degraded` is a column not just a payload key:** it must be queryable as a first-class filter (e.g., "exclude degraded replays from cost reconciliation").

---

## 2. OTel column naming convention

When `payload` carries GenAI attributes, use **OpenTelemetry GenAI semantic conventions verbatim** — no abbreviations, no rename to camelCase. Per [`jarvis-v2-redesign.md` §C17 L3](jarvis-v2-redesign.md#c17--observability--audit-1).

| OTel key | Type | When |
|---|---|---|
| `gen_ai.usage.input_tokens` | int | Any LLM call. |
| `gen_ai.usage.output_tokens` | int | Any LLM call. |
| `gen_ai.usage.cost_usd` | numeric | Locally coined (no canonical OTel cost key as of 2026-04). Mirrors top-level `cost_usd` for in-payload use. |
| `gen_ai.request.model` | text | Requested model id (e.g., `claude-haiku-4-5-20251001`). |
| `gen_ai.response.model` | text | Server-reported model id. |
| `gen_ai.response.id` | text | Provider response id (Anthropic `msg_*`). |
| `gen_ai.response.finish_reasons` | text[] | `["end_turn"]`, `["max_tokens"]`, etc. |
| `gen_ai.provider.name` | text | `anthropic`, `openai`, `voyageai`, etc. |
| `gen_ai.agent.id` | text | For subagent calls — agent identifier. |
| `gen_ai.tool.name` | text | When event records a tool call. |
| `gen_ai.conversation.id` | text | Claude Code session id when available. |
| `gen_ai.operation.name` | text | `chat`, `tool_call`, `embedding`, `generate_content`. |
| `gen_ai.operation.duration_ms` | int | Wall-clock duration; from `PostToolUse` hook `duration_ms` field. |

**Why verbatim:** future-proofs against OTel ecosystem tooling (Honeycomb, Grafana Tempo, OpenLLMetry exporters) reading our events table without column-rename middleware. Per [§C17 L3](jarvis-v2-redesign.md#c17--observability--audit-1) — `traceloop-sdk` with custom Supabase exporter is the deferred-but-planned path; it expects OTel-shaped attributes.

---

## 3. Trace propagation contract

Every initiating context creates a `trace_id`; downstream actors inherit. Implementation: `contextvars.ContextVar[str]` + `uuid.uuid4().hex`. Per [`jarvis-v2-redesign.md` §C17 L3](jarvis-v2-redesign.md#c17--observability--audit-1).

### Four rules

1. **Owner message → new `trace_id`.** Each turn starts fresh. (Hook implementation lands in substrate-consumer wave; record_decision in #477 synthesizes a fresh trace_id when caller didn't set one.)
2. **Scheduled task fire → new `trace_id`.** Each cron tick is its own trace.
3. **Subagent spawn → inherits parent's `trace_id`.** `parent_event_id` = the `event_id` of the spawning event (typically a `tool_call` action with `gen_ai.tool.name='Agent'`).
4. **Hook fire → inherits `trace_id` of the session/task it fires in.** Hooks do not start new traces.

### Cross-agent handoff (mailbox-style, future)

When `trace_id` flows with the handoff payload (e.g., `task_queue` rows in 1.x), the consumer reads the trace_id from the payload, NOT from its own ContextVar. Out of scope for Sprint 35 (no consumers wired); contract is documented here so 1.x doesn't reinvent.

### Caller-omitted trace context — graceful default

If a caller of `record_decision` (or any future writer) doesn't set the ContextVar, the writer **synthesizes a fresh trace_id** and proceeds. No crash. The synthesized id has no `parent_event_id`, which makes it visibly orphaned during trace replay — that's the signal that propagation is missing somewhere upstream (queryable with `WHERE parent_event_id IS NULL AND actor NOT IN ('user', 'task-*')`).

---

## 4. Write semantics

Per [`jarvis-v2-redesign.md` §C3](jarvis-v2-redesign.md#c3--memory) — **observability MUST NOT block C3**.

### Happy path

1. Caller invokes a writer (e.g., `record_decision`).
2. Writer constructs the event row.
3. Writer executes `INSERT INTO events_canonical ... RETURNING event_id`.
4. After-insert trigger fires `pg_notify('events_canonical', json_build_object('event_id', ..., 'trace_id', ..., 'action', ..., 'actor', ...))`.
5. Writer returns success to caller.

### Degraded path (transient pg failure)

1. INSERT fails (connection drop, deadlock retry exceeded, RLS violation under transient session-role flap).
2. Writer logs warning to stderr (visible in MCP server logs).
3. Writer pushes the failed event payload onto an in-memory bounded ring buffer (~100 events; oldest evicted on overflow with `signal_dropped` log line).
4. Writer returns success to caller. **The original action does not fail.**
5. On the **next successful** INSERT, writer drains the buffer with `degraded=true` set on each replayed row, restoring chronology approximately (replayed `ts` reflects original event time, not drain time).

### Why `degraded` not a separate "shadow" table

A separate replay table would itself need degraded handling. Self-referential infinite regress. One column on the canonical table — queryable, joinable, exportable — closes the loop.

### What MUST NOT trigger degraded mode

- RLS denies due to misconfiguration (a real bug; surface it loudly, don't paper over).
- Schema mismatch (column missing because migration didn't run on the target env — this is a deploy bug).

The buffer is for *transient* infra failures, not for hiding misconfiguration. Implementation in #477 distinguishes by Postgres error code class (transient: `08*` connection, `40001` serialization, `57P*` operator intervention; non-transient: `42*` syntax/structural, `2300*` integrity).

---

## 5. Action vocabulary (initial)

Open vocabulary; new values added by PR. Reserved values for the first wave (Sprint 35–37):

| `action` | Emitted by | Initial wave |
|---|---|---|
| `decision_made` | `record_decision` | Sprint 35 (#477) |
| `memory_recall` | `_handle_recall` in `mcp-memory/handlers/memory.py` | Existing emit migrates to substrate post-#477 |
| `memory_write` | `memory_store` | Substrate-consumer wave |
| `tool_call` | PreToolUse hook | Substrate-consumer wave |
| `tool_returned_empty` | tool wrappers | Substrate-consumer wave |
| `error` | exception handlers | Substrate-consumer wave |
| `compaction` | PreCompact hook | Substrate-consumer wave |
| `cost_charge` | LLM call wrappers | C13 sprint (Sprint 36) |
| `recall_failed` | FoK pipeline | Existing fok-batch.py post-#477 |
| `hallucination_suspected` | PostToolUse diff-coherence (C16) | Sprint 38 |
| `episode_started` / `episode_ended` | session lifecycle | Folds existing `episodes` table |
| `signal_dropped` | substrate buffer overflow | Sprint 35 (#477) |

**Why explicit reserved values:** owner-facing dashboards and reflection (C5) query failure modes by category, not text-search payloads. Per [`jarvis-v2-redesign.md` §self-detection](jarvis-v2-redesign.md#self-detection).

---

## 6. What dies / becomes a view (forward reference)

Not implemented in Sprint 35. Listed here so #476 implementer doesn't accidentally drop them, and so the C5/C13 sprint planners know the cutover targets.

### 6.1 New table, legacy parallel during cutover

Sprint 35 creates a **new** table named `events_canonical` (NOT an `ALTER` of the existing `events` table). Reasons:

1. The existing `events` table (current [`mcp-memory/schema.sql`](../../mcp-memory/schema.sql)) is GH-Actions-perception-shaped: `event_type` enum, `severity` check constraint, `repo`, `source`, `processed`/`processed_at`/`processed_by`/`action_taken` workflow fields. None of these fit the canonical actor/action/trace_id shape, and the columns have NOT-NULL constraints that an in-place ALTER cannot retrofit safely.
2. `fok_judgments.recall_event_id` references `events(id) ON DELETE CASCADE` (Sprint #34 [#443](https://github.com/Osasuwu/jarvis/issues/443)). An in-place rename or schema-rewrite breaks the FK.
3. Two-mode coexistence per [`jarvis-v2-redesign.md` §Bootstrap protocol & migration order](jarvis-v2-redesign.md#bootstrap-protocol--migration-order) explicitly calls for new and legacy paths running in parallel until C3 path-parity proves cutover safety.

**Final state (cutover wave, post-Sprint 35):** legacy `events` rows migrate into `events_canonical`; FK on `fok_judgments` rewires; legacy table drops; canonical renames to `events`. Single canonical name restored.

### 6.2 Cutover targets

| Existing | Fate | When |
|---|---|---|
| `events` (legacy, GH-Actions perception, FoK FK source) | **Kept verbatim during Sprint 35.** Migrated row-by-row into `events_canonical` and dropped (`fok_judgments` FK rewires) during cutover. | Cutover wave (post-Sprint 35) |
| `events_canonical` (Sprint 35 new) | **Renames to `events`** at end of cutover. Single canonical name restored. | Cutover wave |
| `task_outcomes` | Becomes a view over canonical events `WHERE action IN ('outcome_recorded', ...)`. | C5 sprint |
| `episodes` | Folded — events with `action='episode_*'`. Standalone table dropped post-cutover. | Cutover wave |
| `audit_log` (declared in code but never schematized — exact #326 class of drift) | Eliminated as a class. View over canonical events. | Cutover wave |
| `known_unknowns` | View over canonical events `WHERE action IN ('recall_failed', 'tool_returned_empty')`. | C5 sprint |
| `*_last_run` memories abusing C3 | **Dropped.** Replaced by view `last_run_by_actor` (`MAX(ts) FILTER (WHERE outcome='success') BY (actor, action)`). Ends abuse of memory store as task-heartbeat. | Sprint 36 |
| `memory_review_queue` | **Kept** as a queue (workflow state, not log). Queue *operations* emit events to substrate. | n/a |
| Device-local session jsonl | **Kept** for raw fidelity. Cross-device replay reads canonical events, not synced jsonl. | n/a |

**Migration directory:** `supabase/migrations/` (existing convention — see Sprint #34 FoK migrations on main: `20260429065042_add_fok_judgments_table.sql` etc.). NOT `mcp-memory/migrations/`. The #476 issue body had this wrong; correction recorded here.

---

## 7. What this doc does NOT lock

- **Materialized view definitions** (`events_cost_by_day_mv`, `events_last_run_by_actor_mv`) — covered by #476.
- **Index strategy** beyond "indexed for trace replay + actor/action filters" — covered by #476.
- **RLS policies** — jarvis r/w + redrobot r/o is the L1 stance; precise policy SQL in #476.
- **`pg_cron` refresh schedule** — covered by #476.
- **Subagent / hook propagation implementation** — substrate-consumer wave (Sprint 38+).
- **Other writers beyond `record_decision`** — substrate-consumer wave.
- **OTel exporter library** (`traceloop-sdk` per [§C17 L3](jarvis-v2-redesign.md#c17--observability--audit-1)) — deferred until volume justifies; bespoke `events_insert()` is fine for cold-start.

---

## 8. Acceptance check (this issue)

- [x] Schema fields locked in §1 with types and rationale.
- [x] OTel verbatim convention listed in §2.
- [x] Trace propagation rules unambiguous in §3 (graceful default for caller-omitted context defined).
- [x] Write semantics resolve degraded handling in §4 (buffer policy, replay flag, what MUST NOT degrade).
- [x] Action vocabulary listed in §5 with first-wave attribution.
- [x] Cutover targets enumerated in §6.
- [x] Forward-deferrals listed in §7 so #476/#477 don't expand scope.
- [x] Cross-link from `jarvis-v2-redesign.md` C17 section (added in same PR).

---

## References

- [`jarvis-v2-redesign.md` §C17](jarvis-v2-redesign.md#c17--observability--audit) — full design rationale.
- [`jarvis-v2-redesign.md` §C17 L3 leans](jarvis-v2-redesign.md#c17--observability--audit-1) — OTel verbatim, materialized views from day one, contextvars + uuid, traceloop-sdk deferred.
- [`jarvis-v2-redesign.md` §C3 write semantics](jarvis-v2-redesign.md#c3--memory) — `degraded=true` fallback.
- [`jarvis-v2-redesign.md` §Bootstrap protocol & migration order](jarvis-v2-redesign.md#bootstrap-protocol--migration-order) — substrate ships first; two-mode coexistence gate is C3-defined path-parity test.
- [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) — column-name source of truth.
