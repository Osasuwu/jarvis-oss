# `/dispatch` Slice 2 ship gate — the documented SQL query (#1085 S2-7)

Pull-only. Read this when checking whether `status:owner-queue` can come off
issue #1085, or when re-running the ship-gate check at a later slice-2 PR
review. Not `@import`ed — the gate itself is human-checked, not CI-mechanized
(design decision `6fab7971-b782-4f4b-8203-51f9b72603a6`, grill session
`session:grill-1085`).

## The gate

Per #1085's body: Slice 2 keeps `status:owner-queue` on issue #1085 until **5
clean executor completions**, counted cumulatively from **2026-08-10**, are
observed. "Clean" = a `task_queue` row reached `done` **and** its PR merged
with a real closing keyword **and** zero manual interventions along the way.
Verified at slice-2 PR review by running the query below and cross-checking
each candidate against GitHub — no CI check enforces this; a human reads the
output and decides.

## Why the query can only surface candidates

`task_queue` (`supabase/schema.sql`) has no column for "manual
intervention" and no FK to the PR that closed the issue — it only knows its
own FSM state. The query below finds rows that are `done` *and* look like
they came from `/dispatch` (not a manually-enqueued row, not a Slice-1/pre-
`/dispatch` row); it cannot tell you the PR merged cleanly or that nobody
touched the row by hand. Those two checks are done per-candidate against
GitHub, by a human, per the AC's explicit "human-checked... No CI
mechanization."

## The query

```sql
select
  id,
  issue_number,
  idempotency_key,
  status,
  claimed_at,
  completed_at,
  escalated_reason
from task_queue
where status = 'done'
  and assignee = 'sandcastle'
  and idempotency_key like 'delegate:%'
  and created_at >= '2026-08-10'
order by completed_at asc;
```

- `assignee = 'sandcastle'` + `idempotency_key like 'delegate:%'` — the exact
  shape `/dispatch`'s enqueue call stamps (`SKILL.md` §3: `idempotency_key=
  f"delegate:{N}:r1"`, `assignee="sandcastle"`), so this excludes
  orchestrator-emitted rows, manually-enqueued rows, and anything pre-dating
  `/dispatch`'s existence.
- `created_at >= '2026-08-10'` — the AC's stated cumulative-count start date.
- `escalated_reason` is selected so a row that reached `done` only after a
  human nudge (escalation, manual re-drive) is visible in the output, not
  just rows that failed — a non-null value here is itself a manual-
  intervention flag and should disqualify that row from the "clean" count
  even though `status = 'done'`.

## Per-candidate human check

For each row the query returns:

1. `gh issue view <issue_number> --json state,closedBy` — confirm the issue
   is actually closed (not just the queue row marked `done`).
2. Find the PR that closed it and confirm the closing keyword is real
   (`Closes #N` / `Fixes #N` / etc. per
   [`pr-issue-linkage.md`](pr-issue-linkage.md)) — not a merge that happened
   to land near the issue with no linkage.
3. Check the issue/PR timeline for anything that isn't `/task-implement`
   running unattended: a comment from the principal redirecting it, a manual
   commit pushed to the branch, a manual label/relabel outside the skill's
   own steps. Any of these disqualifies the row from "clean."

Count the rows that pass all three. **5 clean → remove `status:owner-queue`
from #1085** (`gh issue edit 1085 --remove-label "status:owner-queue"`).
Fewer than 5 → leave the label, note the count in the slice-2 PR body so the
next review picks up the running total instead of re-deriving it from
scratch.
