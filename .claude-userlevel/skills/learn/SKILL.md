---
name: learn
description: "Drain the memory review queue: review pending classifier decisions, candidates, and merge proposals one-by-one. Use when the owner says /learn, or on the weekly cron."
model: opus
effort: high
---

# /learn — memory review queue drain

Drains `memory_review_list` in f2 order (merges → candidates → classifier).
This slice exercises only the **classifier** branch; merges and candidates
are counted but rendered as no-op.

## Quick start

```
/learn                     # drain pending classifier queue (cap 20)
/learn --status            # peek at pending counts, no mutations
```

## How it works

1. **Fetch** — call `memory_review_list(queue='classifier')` to get up to
   20 pending classifier-provenance rows from `memories`.

2. **Render** — for each row, call the renderer module to produce a
   display block. Classifier UPDATE rows show a before/after diff of the
   target memory (context fetched by name). ADD / DELETE / NOOP show a
   compact card.

3. **Present + act** — show each row to the owner, prompt for action:

   - `approve` — calls `memory_review_decide(action='approve')`,
     removes the row from the review queue.
   - `reject` — calls `memory_review_decide(action='reject_classifier')`
     with the owner's free-text reject reason.
   - `skip` — leave row pending, move to next.

4. **Finalize** — in order:

   a. **Emit `learn_run` event** into the `events` table to close the
      debounce loop (prevents watcher re-firing within 24 h):
      ```sql
      INSERT INTO events (event_type, severity, repo, source, title, payload)
      VALUES ('learn_run', 'info', 'your-username/jarvis', 'learn_skill',
              '/learn session complete', '{}');
      ```
      Use the `execute_sql` MCP tool or the Supabase client. **Always
      emitted** — even for zero-row runs — so the watcher debounce is
      set after every `/learn` invocation, not only watcher-triggered ones.

   b. Emit one `outcome_record` with:
      ```
      {
        accepted: N,
        rejected: N,
        merged: 0,          # reserved for future slices
        classifier_drained: N,
        top_reject_reasons: ["..."],
        pending_remaining: N,
        duration_s: N
      }
      ```

## --status mode

```
/learn --status
```

Prints per-queue pending counts from `memory_review_list` without
mutating any row:

- Pending classifier items
- Pending candidates (future)
- Pending merge proposals (future)

No interactions, no writes, no `outcome_record`.

## Safety rules

- **Hard cap**: process at most 20 rows per `/learn` run (the
  `limit_count` parameter of `memory_review_list`). Remaining rows stay
  pending for the next run.
- **Fail-stop**: if `memory_review_decide` errors mid-run, stop
  immediately and report the error. Already-processed rows are committed;
  the remaining rows stay pending.
- **No defer**: the current slice does not support `defer`. If the owner
  wants to skip a row, use `skip` (leaves it pending for next run).
- **No blanket actions**: no `accept_all` or `reject_all`. Each row is
  reviewed individually.
- **Rollback safety**: rejected rows are marked `requires_review=false`
  with `reject_reason` populated — not deleted. Recovery is a manual
  DB update to flip `reject_reason = null, requires_review = true`.

## Implementation notes

The renderer lives at `mcp-memory/review_render.py` — import via
`importlib` (hyphen in parent directory name):

```python
import importlib.util
import subprocess
from pathlib import Path

_repo_root = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"])
    .decode()
    .strip()
)
spec = importlib.util.spec_from_file_location(
    "review_render",
    _repo_root / "mcp-memory" / "review_render.py",
)
render_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_mod)
```

For each classifier row that looks like an UPDATE (has a same-named
live memory in the DB), pre-fetch the current memory as the
`before_snapshot` and set `ctx['decision'] = 'UPDATE'` **before** calling
`render_mod.render_proposal(row, ctx)`. Without the explicit `'UPDATE'`
key the diff branch never fires — the row renders as a compact card
instead of a before/after diff.

RPCs (`memory_review_list`, `memory_review_decide`) are called via
`execute_sql` or direct supabase client — whichever is available in the
current context.
