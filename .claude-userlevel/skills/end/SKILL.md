---
name: end
description: "Session close. Default: full reconciliation (decision log, CONTEXT gap check, outcome enrichment, memory save, commit, handoff, ~5 min). With --quick: checkpoint + commit only (~30 sec). Triggers: 'end', 'end session', 'end quick', 'быстро закончим'."
---

# End Session

Closes the session. Two modes:

- **Default** (`/end`) — full reconciliation. Run Steps 0 → 8 below.
- **`--quick`** (`/end --quick`) — fast exit. Run only Step 5 (working state, if meaningful) and Step 7 (commit), then emit the one-liner from the "Quick output" section. Skip Steps 0–4, 6, and 8.

The compaction-safety note from the old `/end-quick`: skipping Steps 0–4 in quick mode is safe because the PreCompact hook (`scripts/pre-compact-backup.py`) persists a pre-compact snapshot to Supabase under `session_snapshot_<session_id>` on every compaction, and `record_decision` writes decisions in real time. That's enough durable handoff. Run without `--quick` when you want reflection + decision reconciliation.

## Quick output (`--quick` only)

One-liner:

```
Session saved. <committed: hash | no commit: reason>. <working state: saved | nothing to save>.
```

That's it. Go.

## Full mode

**Mindset — survives compaction:** *Supabase is the session journal. The conversation is working memory.* The post-compact conversation is a lossy summary; the journal (pre-compact snapshot + real-time `record_decision` entries) is the authoritative record. `/end` consolidates the journal and enriches it. Don't rely on scanning the conversation alone — anything older than the last summary may already be gone from your window.

## Step 0 — Load the session journal (non-negotiable)

Before reconciling or enriching, pull everything durable from Supabase:

1. **Pre-compact snapshot** — **do NOT use `memory_recall` for this**: snapshot rows are tagged `session-snapshot`, which `memory_recall` excludes by design (`EXCLUDE_TAGS_FROM_RECALL`, #417) — the query will never return them.
   - **Preferred — indexed point lookup.** If you know this session's id, fetch the row directly: `memory_get(name="session_snapshot_<session_id>", project="jarvis")`. The id is the `# Session Snapshot — <session_id>` header in the auto-loaded *Pre-Compact Recovery* block (if no Pre-Compact Recovery block was loaded, the id is unavailable — use the fallback below). The lookup is backed by the `UNIQUE(project, name)` constraint (PostgreSQL creates an implicit B-tree index for unique constraints), so its cost is O(log N) on table size, not O(rows-scanned).
   - **Fallback — id genuinely unknown.** `memory_list(project="jarvis", type="project")`. Note: the `memory_list` handler has no `limit` parameter — the call returns **all** `type=project` rows (the `project` filter includes `project IS NULL` global rows too). With `type="project"` specified, all returned rows share that type; the handler sorts `type` asc then `updated_at` desc, so with a single type value the `type` sort is a no-op and the effective order is `updated_at` desc. Do **not** drop the type filter: an unfiltered list interleaves types and the "newest-first" property no longer holds. Scan the list newest-first and pick the first entry whose name starts with `session_snapshot_` (ignore any with `test` in the name). If no match found, treat as hook-didn't-fire and proceed with conversation only. Then `memory_get(name=..., project="jarvis")` to load the full content. This is why the exact lookup above is preferred — the fallback fetches unbounded rows.
   - **Freshness check.** An LLM has no reliable session-start timestamp, so don't try to test whether `updated_at` is "from *this* session". **Content consistency is the deciding test; age is only a trigger.** If a snapshot's content is consistent with this session's actual context, trust it regardless of `updated_at` — a legitimately long AFK session must not distrust its own fresh snapshot just because the clock advanced. Use age solely to decide *when* to run the content-mismatch check below: when `updated_at` is **older than 4 hours**, run the check before trusting the snapshot; age alone is never grounds to discard it. Note this 4-hour cutoff is for the **direct `memory_get` fetch path only** — if you took the snapshot straight from the auto-loaded *Pre-Compact Recovery* block, `session-context.py` already gated it at `PRE_COMPACT_FRESHNESS_MINUTES` (30 min), so anything surfacing there is fresher than this cutoff by construction.
   - **Independent compaction signal — read the gen-counter, do NOT infer from snapshot presence.** Whether the session compacted is answered by the per-session counter the PreCompact hook bumps (`scripts/pre-compact-backup.py` → `_bump_compaction_count`), **not** by whether a snapshot row exists. The two are decoupled on purpose: snapshot-missing ≠ never-compacted (that conflation was the bug where `/end` reported "session was not compacted" through a hook outage). Read it:
     - File: `~/.claude/compaction-counts/<session_id>.txt`, where `<session_id>` is sanitized the same way as the writer — keep only `[A-Za-z0-9-_]`, everything else stripped. Content is a single integer `gen` (generations survived). Missing file or unreadable → treat as `gen = 0`.
     - On Windows use `Read` (the `Bash` tool's `read` builtin is unrelated and `~` may not expand — resolve `$HOME`/`%USERPROFILE%` explicitly, or just `Read` the absolute path `C:\Users\<user>\.claude\compaction-counts\<id>.txt`).
   - **Now branch on (gen, snapshot) jointly:**
     - `gen == 0` AND no snapshot → this session never compacted. Fine, skip. Conversation alone is enough.
     - `gen > 0` AND snapshot found → normal compacted session. Use the snapshot as the journal (per the freshness rules above).
     - `gen > 0` AND **no** fresh `session_snapshot_*` row → **the session DID compact `gen` times but the PreCompact hook failed to persist a snapshot.** Do NOT report "not compacted" — report "compacted `gen`× but snapshot missing — hook failed". Known failure mode: rewriting `~/.claude/settings.json` (e.g. `install.ps1 -Apply`) while the desktop app runs disables ALL hooks for every spawned session until app restart (2026-06-12 outage). Confirm via `.claude/session-snapshots/hook.log` — a `trigger=auto`/`manual` heartbeat present means the hook ran (look for a write failure outcome); its absence at compaction time means the hook never fired at all. Flag in Step 8 output and fall back to conversation only.
     - `gen == 0` BUT a snapshot *is* found → counter file was lost/cleared (e.g. wiped `~/.claude`), not a contradiction worth blocking on. Trust the snapshot; note the counter gap in Step 8.
   - If the freshest snapshot looks like a *different* session's work (content references work unrelated to what you remember from the current context) → flag in Step 8 output and fall back to conversation only.
   - Multiple compacts in one session share a single snapshot (same session_id, upserted on each compaction); the one you pick is the latest state.
2. **Real-time decisions** — `memory_recall(query="decisions today <date>", project="jarvis", type="decision", limit=20, brief=true)` where `<date>` is today's ISO date. These should already be in place via `record_decision` calls made during the session. Step 1 will verify completeness and enrich with post-hoc markers.
3. **Recent episodes (optional)** — if you need finer-grained provenance, `events_list` surfaces `tool_call`, `decision`, and `observation` episodes the extractor captured.

Carry the snapshot + decisions into Steps 1-3 as the primary source. The conversation (post-compact) is only a hint overlay for anything that happened *after* the snapshot was written.

## Step 1 — Decision reconciliation & post-hoc marking

Reconcile pre-existing records with decisions identified from snapshot + conversation:

- **Decisions** made this session should already live in Supabase via `record_decision` (fires in real time). Go through the snapshot + conversation and check: every decision you can identify → is it in the list from Step 0?
  - If yes → do nothing. Don't re-save.
  - If no → save it now via `record_decision` **and flag in Step 8 output**: "Decision X was not recorded in real time — post-hoc save". Real-time capture is the goal; post-hoc saves are a regression. **Mark post-hoc decision saves by encoding `:post-hoc` into the `actor` field** (e.g. `actor="session:<id>:post-hoc"`) — `/self-improve` greps the actor field to detect regression patterns. The `record_decision` tool has no dedicated `post_hoc` field today; #517 tracks adding one as a structured payload extension.
- **User preferences** or profile updates → `user` memory (upsert existing, don't duplicate).
- **Project state** changes → `project` memory.
- **Feedback** given by principal → `feedback` memory.

Upsert existing memories, don't create duplicates. Check name before creating new.

## Step 2 — CONTEXT.md gap check

Scan for domain terms this session that fall outside the CONTEXT.md glossary, and check for new design docs that may need documentation.

**Trigger (skip silently if neither fires):**
1. **Rationale-term diff** — extract topic/domain terms from all `decision_made` episode rationales (snapshot + session decisions). Common terms: noun phrases from rationale text that appear 2+ times and are not in `CONTEXT.md` glossary section.
2. **Design-doc git-diff** — run `git diff --name-only HEAD origin/main -- docs/design/ docs/adr/`. If files were added/modified this session → signal fires.

If **either signal fires**:
- Output: "Potential CONTEXT.md gap. New terms: X, Y, Z. New design docs: <file list>. Patch?" 
- Owner answers yes/no. If yes → **you generate an inline diff for owner to apply** (don't apply yourself — stays in conversation for owner review).
- If **neither signal fires** → skip silently; do not output anything.

If the signal check itself fails (git command error, CONTEXT.md unreadable) → skip silently.

## Step 3 — Outcome record enrichment

For each `decision_made` episode loaded in Step 0 from this session:

1. Check if an `outcome_record` already exists for that decision UUID.
   - If yes → skip.
   - If no → proceed to step 2.
2. **Deliverable heuristic** — scan the decision's `rationale` field for any of:
   - GitHub issue reference: `#NNN` 
   - PR reference: `PR <num>` or `pull request <num>`
   - File path under: `docs/`, `scripts/`, `mcp-memory/`, `.claude-userlevel/`, or `.github/`
   - If **any match found** → extract the deliverable kind (`pr`, `issue`, or `file`)
   - If **no match** → skip (architectural-only decision; outcome attribution belongs to `/self-improve`)
3. **Create outcome record** — call `outcome_record(outcome_status="pending", ...)` with:
   - `task_description` = first sentence of decision rationale (max 1 line)
   - `task_type` = `"autonomous"` (the `outcome_record` enum is `delegation|research|fix|review|autonomous`; agent-emitted decisions during session work map to `autonomous`)
   - `project` = extracted from decision payload (or "jarvis" if missing)
   - `pattern_tags` = **union of**:
     - Topic tags already in the decision's `pattern_tags` (if present)
     - `"source:end-enrichment"`
     - `"deliverable_kind:<pr|issue|file>"` (the detected kind)
   - `pr_url` = extracted from rationale if kind==pr (format: extract `PR <num>` → construct URL)
   - `issue_url` = extracted from rationale if kind==issue (format: extract `#NNN` → construct URL)

Skip if decision's rationale has no deliverable hint (architectural decisions stay untracked here; `/self-improve` owns those).

**Session-PR fallback** (for PR-based enrichment if journal doesn't give source):
- Primary: session journal decision captures the PR context (most reliable)
- Fallback: `gh pr list --author @me --search "created:>=<today>" --json number,title` — use the freshest PR if journal is empty

Post-hoc decisions saved by Step 1 are now enriched here if they have deliverable hints.

## Step 4 — Goal progress log

If work this session advanced any active goal:

1. Call `goal_list(status="active")`
2. For each goal that was advanced, call `goal_update(slug=..., progress=[...])` — append new items as `{item: "<5-word summary> (YYYY-MM-DD)", done: true}`
3. Keep existing progress items unchanged. Only append new ones.

Keep items terse — "secret scanner + credential registry (2026-04-13)", not a sentence. Details live in git history.

Skip if the session didn't advance any goal (e.g., pure discussion, research without deliverables).

## Step 5 — Working state (non-negotiable)

Save `working_state_jarvis` (type=project) to Supabase. Always. Content:
- What was done this session
- Open items: unfinished work, things to fix, deferred tasks
- Key context for next session (blockers, decisions pending review)
- **Suggested next skills** — explicit chain hint for the next session, e.g. `/status → /implement #532 → /verify`. One line, ordered. Omit only if truly nothing pending (rare; usually at least `/status`). Lets the next session skip the "what should I run first" decision; mirrors the one useful idea from Pocock's `/handoff` skill without forking durable storage out of Supabase.

This is the handoff to the next session. If open items exist in Step 8 output, they MUST be in this memory too — output is ephemeral, memory persists.

Only exception: truly empty session (user asked one question and left).

## Step 6 — Branch cleanup

Check for local branches whose remote tracking branch has been deleted:
```bash
git branch -vv | grep ': gone]' | awk '{print $1}'
```

If any found, for each branch:
1. Try `git branch -d <name>` (safe delete)
2. If it fails ("not fully merged") — this usually means the PR was squash-merged. Verify:
   ```bash
   gh pr list --head <branch> --state merged --json number --limit 1
   ```
3. If PR confirmed merged → `git branch -D <name>` (force delete is safe)
4. If no merged PR found → report in output, don't delete

Skip if none found.

## Step 7 — Commit (non-negotiable: leave nothing uncommitted)

Check ALL project repos for uncommitted changes (jarvis, redrobot, any other repo touched this session).

For each repo with changes:

1. `git status` and `git diff --stat`
2. Determine if changes are committable:
   - Complete work → commit
   - Mid-task, broken, merge conflicts → stash with descriptive message (`git stash push -m "session YYYY-MM-DD: <description>"`) and note in output
3. **Branch handling** (before committing):
   - On `main` → commit directly
   - On a feature branch **created/used for this session's work** → commit there
   - On an **unrelated branch** (pre-existing branch for different work) → relocate changes:
     ```bash
     git stash
     DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
     git checkout $DEFAULT_BRANCH
     git pull --ff-only  # get latest, fail-safe
     git stash pop
     ```
     Then commit on the default branch. If stash pop has conflicts, resolve them (add our version). If pull fails, commit as-is (don't block on upstream).
4. Stage only session-related files. Standard commit format with Co-Authored-By.

**Goal: zero uncommitted changes across all repos after /end.**
If stashing (mid-task), report the stash ref and repo in output so next session can recover.

## Step 8 — Output

```
## Session closed — YYYY-MM-DD

### Journal source
- Compactions (gen): <N from compaction-counts/<id>.txt — "0 (never compacted)" | "N">
- Snapshot: <session_snapshot_... name + "fresh" | "stale" | "none — HOOK FAILED, compacted Nx but no snapshot" | "none (never compacted)">
- Decisions loaded: N
- Post-hoc decision saves: N  (0 is ideal — every decision should have been recorded in real time)

### CONTEXT.md gap (Step 2)
- <"No gaps detected" OR "Potential gap: new terms X, Y or new design docs — patch?" — only render when Step 2 signals fire>

### Outcome enrichment (Step 3)
- <"Outcomes created: N" OR "No deliverable hints detected" — only render when enrichment fired>

### Saved to memory (N)
- <name> — <one-line>

### Committed
- <hash + message, or "No commit — <reason>">

### What was done
- <bullets — draw from snapshot where applicable, not just post-compact conversation>

### Open items
- <unfinished work, deferred tasks, things for next session>
```

Keep it concise. This is a handoff, not a report. Render the CONTEXT.md gap and Outcome enrichment sections only when their respective steps fire (heuristic triggers).
