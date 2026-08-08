# Memory subsystem — pull-only reference

Evicted from the always-loaded [`docs/context/invariants.md`](../context/invariants.md) by
[#1418](https://github.com/Osasuwu/jarvis/issues/1418). These facts are read at the instant of an
`mcp__memory__*` call or while working inside `mcp-memory/` — they decide nothing in a session that
never touches memory internals, so they are not worth a byte in every window. Pull this file when
the answer turns on how the memory server behaves.

The two memory rules that *do* bind every session stay in `invariants.md`: Supabase is cross-device
truth, and secrets never land in any persistent surface.

## `memory_store` contract

Needs `source_provenance`. Idempotent on `(project, name)` and **never** similarity-blocked — the
tool response is the signal, so read it rather than assuming a store was deduplicated away.
`memories_used` carries UUIDs, never names (user-level `CLAUDE.md` §2 is the canonical statement of
that half; it is restated here only so this file reads whole).

## Recall internals

- FoK `unknown` / `skipped` are terminal states.
- HNSW is used only for a bare `<=> anchor limit K` query; adding predicates forces an exact scan.
- Clusters are disjoint per `(type, project_key)` and capped above 10.

Server-side implementation detail of `mcp-memory/`. It explains recall behaviour when a query
returns something surprising; it does not gate any tool call.

## Sandcastle provenance RLS

Row-level security covers **every** anon INSERT/UPDATE/DELETE on `memories`, `task_outcomes`,
`episodes`, and `events_canonical` — each requires `source_provenance` / `actor` matching
`LIKE 'sandcastle:%'`.

This is a database constraint: the DB rejects a non-conforming write. Recorded here so the rejection
is diagnosable, not as a rule an agent must remember to obey.

## Memory hygiene is owner-invoked only

`/curate` runs on command. There is no auto-demote hook — nothing prunes memory on a schedule. The
skill states its own trigger model; this line exists so "why did nothing get curated?" has an answer
without reading the skill.
