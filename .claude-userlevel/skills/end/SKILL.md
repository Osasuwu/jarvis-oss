---
name: end
description: "Session close. Default: full reconciliation (decisions.md append, CONTEXT gap check, handoff.md working state, commit, ~5 min). With --quick: checkpoint + commit only (~30 sec). Triggers: 'end', 'end session', 'end quick', 'быстро закончим'."
model: sonnet
effort: low
---

# End Session

Closes the session. Steps 0, 3, and 4 (Supabase journal load, outcome enrichment, goal tracking) are retired — the surviving steps keep their original numbers so cross-references elsewhere stay valid; the numbering gap is deliberate, not a mistake.

Two modes:

- **Default** (`/end`) — full reconciliation. Run Steps 1, 2, 5, 6, 7, 8 below.
- **`--quick`** (`/end --quick`) — fast exit. Run only Step 5 (working state, if meaningful) and Step 7 (commit), then emit the one-liner from the "Quick output" section. Skip Steps 1, 2, 6, and 8.

Compaction-safety note: skipping Steps 1, 2, 6, and 8 in quick mode is safe because decisions.md already receives real-time appends during the session (per `/implement` §3 and §6), and Step 5's handoff.md write — the one piece of state that must never be skipped — captures the rest. Run without `--quick` when you want CONTEXT.md gap review and branch cleanup too.

## Quick output (`--quick` only)

One-liner:

```
Session saved. <committed: hash | no commit: reason>. <working state: saved | nothing to save>.
```

That's it. Go.

## Full mode

**Mindset — survives compaction:** *`decisions.md` and `handoff.md` are the session journal. The conversation is working memory.* The post-compact conversation is a lossy summary; the two files under `~/.claude/projects/<project>/memory/` are the authoritative record. `/end` reconciles decisions.md and rewrites this session's block in handoff.md. Don't rely on scanning the conversation alone — anything older than the last summary may already be gone from your window.

## Compaction check — independent signal, read before Step 1

Whether this session compacted is answered by the per-session generation counter the PreCompact hook bumps (`scripts/pre-compact-backup.py` → `_bump_compaction_count`), never by memory of a compaction happening in the conversation. This decoupling is why #1052 added the counter in the first place: a hook outage (`~/.claude/settings.json` rewritten while the desktop app runs, 2026-06-12) can silently break compaction recovery with no other symptom, and the old pre-#1793 detection inferred "compacted?" from Supabase `session_snapshot_*` row presence alone — exactly the signal the outage killed. The counter itself is a plain local file (`~/.claude/compaction-counts/<session_id>.txt`), unrelated to Supabase, so this check survives #1793's move off the memory MCP for this skill.

- **Read** `~/.claude/compaction-counts/<session_id>.txt` — sanitize the id the same way the writer does (`_sanitize_session_id`: keep only `[A-Za-z0-9_-]`, empty → `unknown-session`). Use `Read`, not `Bash`'s `read` builtin — `~` may not expand there; resolve the absolute path. Missing file, unreadable, or non-integer content → `gen = 0`.
- `gen == 0` → this session never compacted (or the counter file was lost, e.g. a wiped `~/.claude`) — nothing to flag either way.
- `gen > 0` **and** no `## Pre-Compact Recovery` block was ever auto-loaded into this conversation (check your own context — that heading, when present, appears in a `SessionStart:compact hook success` system reminder) → the hook bumped the counter but recovery-context delivery failed silently this session. Flag loudly in Step 8: "compacted `gen`× but recovery context never loaded — hook or `session-context.py` may be broken."
- `gen > 0` **and** a `## Pre-Compact Recovery` block did load at least once → normal compacted session; note the count in Step 8, no flag needed.

## Step 1 — Decision reconciliation & post-hoc marking

Read `~/.claude/projects/<project>/memory/decisions.md`. Go through the conversation and check: every decision you can identify (per the trigger list in `/implement` §3/§6, and any made outside that pipeline) → is it already a line in the file?

- If yes → do nothing. Don't duplicate the line.
- If no → append it now:
  ```
  - YYYY-MM-DD — <decision> — <why, one clause> — <#issue or PR>
  ```
  and flag in Step 8 output: "Decision X was not recorded in real time — post-hoc save". Real-time capture (at the moment of `/implement` §3/§6, or any other resolution) is the goal; post-hoc saves are a regression. **Mark a post-hoc append by adding ` (post-hoc)` at the end of the line itself** — there's no separate actor field on a text file; the marker lives in the line's own text.
- **User preferences, project state, and feedback** learned this session → update the relevant memory file under `~/.claude/projects/<project>/memory/` (per the auto-memory conventions in `MEMORY.md`) rather than decisions.md, which is for decisions only.

**Cross-device disclosure**: decisions.md and handoff.md live under `~/.claude/projects/<project>/memory/`, which is machine-local, not synced across devices, and per-repository — this operator runs jarvis on 3 devices, so replacing the Supabase memory MCP (cross-device, queryable, typed) with these files loses cross-device decision and handoff continuity; that loss is an accepted trade-off approved by the operator, not a gap to design around.

## Step 2 — CONTEXT.md gap check

Scan for domain terms this session that fall outside the CONTEXT.md glossary, and check for new design docs that may need documentation.

**Trigger (skip silently if neither fires):**
1. **Rationale-term diff** — extract topic/domain terms from the `decisions.md` lines this session appended (Step 1) plus any recorded earlier this session. Common terms: noun phrases from those lines that appear 2+ times and are not in `CONTEXT.md` glossary section.
2. **Design-doc git-diff** — run `git diff --name-only HEAD origin/main -- docs/design/ docs/adr/`. If files were added/modified this session → signal fires.

If **either signal fires**:
- Output: "Potential CONTEXT.md gap. New terms: X, Y, Z. New design docs: <file list>. Patch?" 
- Owner answers yes/no. If yes → **you generate an inline diff for owner to apply** (don't apply yourself — stays in conversation for owner review).
- If **neither signal fires** → skip silently; do not output anything.

If the signal check itself fails (git command error, CONTEXT.md unreadable) → skip silently.

## Step 5 — Working state (non-negotiable)

Rewrite this session's block in `~/.claude/projects/<project>/memory/handoff.md`. Always. Use **read-modify-write** on the file itself — Read the current content, splice in your own block, Write the result — to avoid clobbering other blocks. Parallel sessions on the same device can still race on this file; see "Scoping of gates" below for the mitigation.

### Read-modify-write on handoff.md

1. **Read** `~/.claude/projects/<project>/memory/handoff.md`. If it doesn't exist yet, start from an empty merge-doc (just the `# Working state — <project>` header).
2. **Splice** your own `### [entry] <branch-or-task-slug> — <YYYY-MM-DD> — <status>` block into the document — replace it if a block with the same slug already exists, else append. Leave every other block byte-for-byte as-is.
3. **Write** the full document back.
4. **Read it back immediately** (see "Read-after-write verification" below) to confirm the write landed.

### Merge-doc format

The document is a markdown merge-doc with per-session `### [entry]` blocks. Structure:

```
# Working state — <project>

### [entry] <branch-or-task-slug> — <YYYY-MM-DD> — <status>

- What was done this session
- Open items: unfinished work, things to fix, deferred tasks
- Key context for next session (blockers, decisions pending review)

[... possibly other old entries from previous sessions ...]
```

**Your session's block:** Replace **only your own** `### [entry]` block (identified by branch/task slug — the slug must match what you used this session). If your block doesn't exist yet, append a new one. Other blocks are copied verbatim.

### Garbage collection (GC)

Before writing, scan all blocks and **delete** a foreign block only if **both** conditions hold:

1. **Status is resolved** — its PR is merged OR its issue is closed, **OR**
2. **Age >14 days** — `updated_at` older than 14 days ago (conservative: err on keeping)

All other blocks are preserved. GC is conservative by design: a wrong date results in "keep longer", not "delete too soon".

### Size cap & eviction

Total document size must be ≤**1500 characters** of content, and ≤**3 entries**.

If adding your block would exceed either limit:
1. Identify old blocks (oldest by date first)
2. Evict them one at a time until both constraints are satisfied
3. For each evicted block: **leave a tombstone** (see below)

### Tombstone marking

When you evict a foreign block (GC or size cap), replace it with a single-line marker:

```
### [evicted] <slug> — <date> — <reason>
```

Where `<reason>` is one of:
- `GC: PR merged` or `GC: issue closed`
- `GC: age >14 days`
- `Size cap: ≤3 entries`
- `Size cap: ≤1500 chars`

Keep the marker short (≤80 chars total line). Tombstones count toward the 1500-char cap and 3-entry limit, but aid debugging when a session's checkpoint disappears.

### Scoping of gates

If future code reads handoff.md to check for decision references or issue numbers, no live reader does so today after this slice's native-memory rewrite — name only readers that still exist at the time you're reading this. Regardless of whether a reader exists yet:

- **Search only within your own `### [entry]` block**, not the entire document
- Treat all other blocks as foreign history; don't use their content to make decisions

This prevents the merge-doc from becoming monotonically more permissive as entries accumulate.

### Read-after-write verification

After writing the updated document, **immediately read it back**:

```
updated = Read(handoff.md)
# Verify your block is present and matches what you wrote
if "<your-slug>" not in updated:
    # Report loudly in Step 8: "Working state: failed to persist own block"
```

This catches silent data loss (e.g., a failed write, or a concurrent session's write landing after yours) and makes it visible rather than discovering it in the next session.

<!-- ceiling: the read-modify-write above is prose-enforced, not server-atomic — two sessions on the same device writing handoff.md at the same instant can still race and clobber each other's block. No known incident yet; if one occurs, revisit (e.g. a lock file, or moving this state to a per-session file instead of a shared merge-doc). -->

This is the handoff to the next session. If open items exist in Step 8 output, they MUST be in this file too — output is ephemeral, the file persists.

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

### Compaction check
- <"gen=N, recovery loaded OK" | "gen=N, recovery context never loaded — hook or session-context.py may be broken" | "never compacted (gen=0)">

### Decision log (N lines appended)
- <line> — <one-line>

### CONTEXT.md gap (Step 2)
- <"No gaps detected" OR "Potential gap: new terms X, Y or new design docs — patch?" — only render when Step 2 signals fire>

### Working state (Step 5)
- <"Saved — <project> (N entries, Y chars)" if success | "FAILED to persist own block" if read-after-write check failed>
- <"Evicted: <slug-1>, <slug-2>" if tombstones were created | omit if none>

### Committed
- <hash + message, or "No commit — <reason>">

### What was done
- <bullets from the conversation>

### Open items
- <unfinished work, deferred tasks, things for next session>
```

Keep it concise. This is a handoff, not a report. Render the CONTEXT.md gap and Working state sections only when their respective steps fire (heuristic triggers for Step 2; Step 5 always renders). Compaction check always renders too — it's a one-line read, not a heuristic trigger.
