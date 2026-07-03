---
name: curate
description: "Owner-invoked weekly memory hygiene. Surfaces stale/inflated/aging memory candidates deterministically, lets the owner mark each as stale (expired_at) or supersede with a named replacement (superseded_by). Use when owner says /curate, 'почисти память', 'memory hygiene', or after 2+ recall-quality complaints."
---

# Curate

Owner-invoked memory hygiene pass. Triggered by `/curate` or natural language like "почисти память". **Not autonomous** — there is no scheduled task, no hook, no LLM-driven surfacer (decision d5bfd444 supersedes the 2026-04-15 no-hygiene-tool bet). The owner is the only authority that can mark a memory stale.

## Why not autonomous

The 2026-04-15 `no_memory_hygiene_tool` decision (719fb533) predicted banner blindness: an autonomous surfacer would either become a nag and get muted, or become a silent reaper that picks the wrong rows. M45 S3 honors that prediction while still giving the owner a fast surface — `/curate` is invoked deliberately, runs ~30 seconds, one row at a time, and emits an `outcome_record` per action for `/reflect` to audit.

## When to invoke

- **Owner-typed `/curate`** — primary path.
- **Pain trigger**: 2+ recall complaints in a short window ("опять всплыло устаревшее", "почему recall выдаёт это"). The owner notices, Jarvis offers `/curate` once, owner decides.
- **Cadence target**: weekly-ish. Not on a clock — semantic.

Do NOT invoke `/curate` from another skill, from a hook, from a scheduled task, or as a "while we're at it" sweep. The skill is opt-in for the owner.

## Five-phase flow

### Phase 1 — Surface candidates (deterministic, NOT LLM)

Three deterministic signals; assemble the union, dedup by memory ID. No model in the loop here — surfacing is a SQL ranking.

1. **always_load + content drift** — memories tagged `always_load` whose `content_updated_at` (or `updated_at` if the former is null) is older than 30 days. Always-loaded rules that haven't been touched in a month are prime drift candidates.

   `memory_list` does NOT accept an `always_load` filter and its output doesn't include `tags`, so this signal is fetched via Supabase MCP (`execute_sql`) — host-only, which matches the rest of the skill's host-only posture:

   ```sql
   SELECT id, name, project, type, tags, description,
          content_updated_at, updated_at, last_accessed_at
   FROM memories
   WHERE 'always_load' = ANY(tags)
     AND expired_at IS NULL AND superseded_by IS NULL AND deleted_at IS NULL
     AND COALESCE(content_updated_at, updated_at) < now() - interval '30 days'
   ORDER BY COALESCE(content_updated_at, updated_at) ASC
   LIMIT 20;
   ```

   If extending `memory_list` with an `always_load` filter + tags-in-output is preferred over raw SQL, that's a separate slice — file a follow-up issue.

2. **Access-inflation** — memories with `last_accessed_at` within the last 14 days **AND** rank in the top-5% of recall-hit-count for their type. After M45 S2 lands, `always_load` rows no longer auto-bump `last_accessed_at`, so a high count here means real recall traffic — but disproportionate traffic relative to type peers is still a signal worth showing the owner.

   ```sql
   -- Surface only — owner judges relevance. Don't auto-act on this row.
   SELECT id, name, project, type, last_accessed_at, content_updated_at
   FROM memories
   WHERE expired_at IS NULL AND superseded_by IS NULL AND deleted_at IS NULL
     AND last_accessed_at > now() - interval '14 days'
   ORDER BY last_accessed_at DESC
   LIMIT 20;
   ```

3. **Prior user-flag** — outcomes tagged `user-flagged-stale` whose memory hasn't been marked stale yet.

   ```python
   outcome_list(pattern_tag="user-flagged-stale", limit=20)
   # Cross-reference outcome.memory_id against live memories
   ```

Present candidates as a numbered list with: name, project, type, age (days since `content_updated_at`), one-line description, signal that surfaced it (drift / inflation / user-flag).

### Phase 2 — Present one row at a time

Show ONE candidate fully. Cap presentation at the first ~20 rows — the owner can re-run `/curate` after pruning the top of the list.

Per row, display:

- `name` (slug) + `project`
- `type`
- `tags` (full list)
- `description` (one-line)
- `content` (truncated to ~500 chars; offer "show full" if the owner asks)
- Lifecycle fields: `created_at`, `content_updated_at`, `last_accessed_at`
- Signal: which Phase-1 query surfaced it
- Recall traffic context: in the last 14 days, this memory was a top-N recall hit for queries containing keywords X, Y. (Read from `events_canonical` if available, otherwise omit — never invent.)

### Phase 3 — Owner choice (4-way fork)

Present the owner with exactly four options. Use `AskUserQuestion` to keep the UX consistent and the answer auditable:

1. **Keep** — leave the memory alone. Skill records no outcome and moves to the next candidate.
2. **Mark stale (expired)** — the belief is wrong/outdated, no replacement. Asks for `reason` (free text, required).
3. **Supersede with named replacement** — the belief has been replaced by another memory. Asks for `successor_uuid` (memory name resolved to UUID via `memory_get`) and `reason`.
4. **Soft-delete (30-day recovery window)** — owner wants the row gone for 30 days, not just demoted. Use this when the memory is wrong AND there's no value in keeping it visible for chain walks. Routes to `memory_delete`, not `memory_mark_stale`.

Default-no — if the owner hesitates or skips, treat as Keep.

### Phase 4 — Apply via MCP tool

- **Keep** → no-op, move on.
- **Mark stale (expired)** → `memory_mark_stale(project=<row.project>, name=<row.name>, reason=<owner-input>)`. No `successor_uuid` → handler sets `expired_at=now()` and emits an `outcome_record` with `pattern_tags=['memory-hygiene','manual-curation']`.
- **Supersede** → `memory_mark_stale(project=<row.project>, name=<row.name>, reason=<owner-input>, successor_uuid=<resolved-uuid>)`. Handler sets `superseded_by` (NOT `expired_at`). Recall walks the chain.
- **Soft-delete** → `memory_delete(project=<row.project>, name=<row.name>)`.

If `memory_mark_stale` returns `Refused: ... host-only` — abort the skill. `/curate` must run on the host (service-role key), never in a sandcastle subagent. This refusal is the defense-in-depth gate; the actual RLS enforcement lives in Supabase per #542.

### Phase 5 — Summary + session outcome

After the owner finishes (or exits), emit ONE session-level summary:

- Reviewed: `<N>` candidates
- Kept: `<K>`
- Marked stale (expired): `<E>`
- Superseded: `<S>` (list `name → successor_name`)
- Soft-deleted: `<D>`

Write a session-level outcome:

```python
outcome_record(
    task_type="fix",
    task_description=f"/curate session — reviewed {N} candidates",
    outcome_status="success",
    outcome_summary=<summary above>,
    project=<scope or null>,
    pattern_tags=["memory-hygiene", "curate-session"],
    lessons=<anything surprising the owner saw>,
)
```

The per-row outcomes already written by `memory_mark_stale` carry the `manual-curation` tag; this session-level one carries `curate-session` so `/reflect` can compute cadence (sessions/month) and yield (rows marked / session).

## Safety rules

- **Host-only.** `memory_mark_stale` refuses when `SANDCASTLE_RUN_ID` is set. The skill should never be invoked from a sandcastle subagent; if it is, exit early with a clear message.
- **No autonomous calls.** No cron, no hook, no other skill calls `/curate`. The owner is the only invocation path.
- **One row at a time.** No batch-confirm UI. The whole point is that the owner reads each row before acting. If the queue is too long, ask the owner to re-run after the first pass — don't streamline the discipline away.
- **Default-no on hesitation.** If the owner's answer is ambiguous, treat as Keep. Marking stale must be deliberate.
- **`memory_unmark_stale` exists.** If the owner mis-clicks, the inverse is one MCP call away: `memory_unmark_stale(project=..., name=...)`. Mention this in the per-row UI as a footnote.

## Cross-references

- **Plan**: M45 milestone description (4-slice restructured plan, 2026-05-23 /grill).
- **Decisions**:
  - `cbaf47ce-9217-40da-923a-b8edce9f233d` — M45 plan adoption.
  - `d5bfd444-78f3-4fca-bcd8-f392f647504c` — supersedes prior `no_memory_hygiene_tool` (719fb533) — the bet that temporal decay alone would suffice. Lost.
- **Staleness pattern**: `memory_recall_staleness_pattern_2026_05_23` (c35177be) — the concrete cases motivating M45.
- **Related**: `/improve-codebase-architecture` (architecture sweep, milestone-close cadence), `/reflect` (cross-session behavioral audit; reads `curate-session` outcomes).

## Out of scope (filed when M45 closes)

- PreToolUse/UserPromptSubmit hook detector — dropped per /grill (hooks stateless, bilingual regex false-positive risk, banner blindness).
- Auto re-curation of `/reflect` patterns — deferred follow-up.
- Mutex vs `memory-consolidation-weekly` — deferred follow-up (race condition on cluster membership).
