# Reflect Aggregates — Pending Migration

**DO NOT DELETE until /self-improve and /verify grills resolve destinations. See follow-up issues #515, #516.**

**Status (2026-05-17):** this doc was the migration PRD captured after #510 closed (`/reflect` rename, 2026-05-07). Live state: `/verify` and `/self-improve` exist as installed skills and have absorbed some steps; #515 (grill /self-improve and /verify before migration) and #516 (re-route cross-skill refs) remain OPEN. The Known-dangling-refs section at the bottom carries inline `Status (post-#510)` annotations for entries whose refs have since been cleaned — preserved here as historical inventory of what needed routing.

This document captures the OLD `/reflect` skill (Steps 1–8) that was replaced by the new `/reflect` (cross-session comms audit) in #510. Each step below documents what it did, its output shape, data sources, known issues, and a candidate destination for migration.

---

## Step 1 — Load recent decisions

**What it did:** Recalled decision-type memories from the last ~2 weeks, filtering out those already resolved (with an `## Outcome` section).

**Output shape:**
- List of decision memory names + metadata (created_at, status)

**Data sources:**
- `memory_recall(query="decision approach chosen rejected", type="decision")`
- Filtered by: `created_at >= now() - 14 days AND outcome section absent`

**Known issues:**
- Decision freshness heuristic (2 weeks) was arbitrary; some projects need longer horizons.
- No deduplication of similar decisions across sessions.

**Candidate destination:** `/verify` (Step 1 of new verify skill mirrors this logic).

---

## Step 2 — Review task outcomes

**What it did:** Fetched pending task outcomes from the outcome table, queried GitHub for PR state, and updated outcomes to success/failure/partial status.

**Output shape:**
```
Per outcome:
- id, outcome_status (pending → success/failure/partial)
- pr_url resolved to state (merged / closed / open)
- pr_merged: boolean
```

**Data sources:**
- `outcome_list(outcome_status="pending", limit=10)`
- `gh pr view <number> --json state,mergedAt,closedAt,title --repo <owner/repo>`

**Known issues:**
- Hardcoded limit of 10 outcomes per run; large backlogs could miss items.
- No retry logic for GitHub API failures.
- Assumed outcome has `pr_url`; non-PR outcomes required manual user input.

**Candidate destination:** `/verify` (all outcome-tracking logic → verify skill).

---

## Step 3 — Check GitHub outcomes for decisions

**What it did:** For each decision with an embedded PR reference (`#NNN`), queried GitHub to determine if the PR was merged (accepted decision) or closed (rejected).

**Output shape:**
```
Per decision with PR ref:
- decision name, PR number
- classification: merged | closed | open
```

**Data sources:**
- Parsed decision memory content for `#NNN` pattern
- `gh pr view <number> --json state,mergedAt,closedAt,title`

**Known issues:**
- Cross-repo decisions required manual repo lookup in `config/repos.conf`; no automation.
- Did not handle force-pushes or PR rewrites that reset merge state.

**Candidate destination:** `/verify` (decision-outcome binding via GitHub).

---

## Step 4 — Check non-PR decisions

**What it did:** For decisions without a linked PR, prompted the user interactively to report outcome (worked / didn't work / ongoing / skip).

**Output shape:**
```
Per non-PR decision:
- decision name
- user-provided verdict: worked | didn't work | ongoing | skip
```

**Data sources:**
- User interaction (manual input via prompt)

**Known issues:**
- Assumes human availability; fails in fully-autonomous runs.
- No audit trail of user responses; hard to correlate with session timestamps.

**Candidate destination:** `/verify` (interactive verification step).

---

## Step 5 — Update decision memory + load basis

**What it did:** Appended an `## Outcome` section to each resolved decision memory, including result status, date, what happened, and (if available) the decision basis from a matching `decision_made` episode within ±24h.

**Output shape:**
```markdown
## Outcome
- **Result:** merged | rejected | worked | failed
- **Date:** YYYY-MM-DD
- **What actually happened:** <one sentence>
- **Decision basis:** <rationale + memories_used from matching decision_made episode>
```

**Data sources:**
- Resolved decision verdict from Steps 2–4
- `decision_made` episode lookup (±24h window from decision created_at)
- Failure classification logic (wrong memory → supersede; empty memories_used → known-unknown; sound basis + execution failure → reasoning/execution)

**Known issues:**
- ±24h window missed decisions made near session boundaries.
- Memory superseding was heuristic-based, not transactional.
- No automated feedback to memory confidence calibration.

**Candidate destination:** `/verify` (core outcome-tracking) + `/self-improve` (confidence calibration feedback).

---

## Step 5.25 — Recall audit aggregate

**What it did:** Aggregated recall-audit results across the last ~20 sessions to surface persistent process leaks (e.g., repeated failure to include memories_used in decision records).

**Output shape:**
```json
{
  "sessions": N,
  "record_decision_calls": M,
  "flags_total": K,
  "flags_by_kind": {
    "decision_text_no_recall": <count>,
    "store_no_recall": <count>,
    "empty_memories_used": <count>
  },
  "empty_memories_used_pct": <percentage>
}
```

**Data sources:**
- `python scripts/recall-audit.py --project jarvis --limit 20 --aggregate`

**Known issues:**
- Script timeout on large session backlogs (>50 sessions).
- Threshold tuning (30% for process leak, 10 flags for instability) was user-calibrated, not data-driven.
- Did not account for session-type variation (e.g., quick /end runs have fewer recall opportunities).

**Candidate destination:** `/self-improve` (process-leak detection and auto-proposal of new recall habits).

---

## Step 5.5 — Calibration check

**What it did:** Queried memory calibration metrics (Brier score per memory type) to surface over- or under-confidence patterns. Flagged types where `brier > 0.25` and predicted confidence ≠ actual outcome.

**Output shape:**
```
Per flagged type:
- type name
- brier score
- n (sample size)
- avg_predicted vs avg_actual
- verdict: overconfident | underconfident
```

**Data sources:**
- `mcp__memory__memory_calibration_summary(project="jarvis")`

**Known issues:**
- Brier > 0.25 threshold was arbitrary.
- Suppressed output for types with n < 20 (data scarcity), but no clear action for marginal cases (n = 15–20).
- Did not break down calibration by decision *category* (e.g., "architecture decisions vs. tooling decisions").

**Candidate destination:** `/self-improve` (confidence recalibration feedback loop).

---

## Step 5.75 — FoK calibration + insufficient clusters

**What it did:** Two parallel scans:
- **A. Calibration drift**: Queried `fok_calibration_summary` RPC to detect divergence between feeling-of-knowing verdicts and actual outcomes.
- **B. Insufficient-knowledge clusters**: Grouped last-7d "insufficient" FoK verdicts by normalized query to surface recurring knowledge gaps.

**Output shape:**
```
A. {n, brier, by_verdict breakdown, drift_signal: bool}

B. [
  {
    norm_query: string,
    hits: int,
    rationales: [string, ...]
  },
  ...
]
```

**Data sources:**
- `mcp__memory__execute_sql("SELECT * FROM fok_calibration_summary('jarvis')")`
- `fok_judgments` table (normalized query grouping, last 7 days)

**Known issues:**
- RPC not yet deployed in all Supabase branches; fallback was silent skip.
- Insufficient cluster grouping used naive normalization (lower + regex space collapse); missed semantic duplicates (e.g., "what's the best way to X" vs "how should I X").
- No automatic memory creation from insufficient clusters; flagging only.

**Candidate destination:** `/self-improve` (knowledge gap + confidence drift feedback).

---

## Step 6 — Extract lessons + patterns

**What it did:** Analyzed resolved outcomes for generalizable lessons and patterns. Looked for:
- Task types with high vs. low success rates
- Common pattern_tags on successful vs. failed outcomes
- Goal-area delivery consistency

Saved non-obvious lessons as `feedback` memories if they would change future behavior.

**Output shape:**
```
Per pattern:
- name (lesson_<slug>)
- type: feedback
- content: rule + why + how to apply
```

**Data sources:**
- `outcome_list(limit=20)`
- Manual analysis of task_type, pattern_tags, goal correlation

**Known issues:**
- "Would change future behavior" was subjective; no objective criterion for memory-save worthiness.
- Did not surface statistically rare but high-impact patterns (n=2, high surprise value).
- No automated pattern-tag standardization; tagging was inconsistent across sessions.

**Candidate destination:** `/self-improve` (outcome analysis + pattern extraction).

---

## Step 7 — Hypothesis review

**What it did:** Recalled project-scope hypotheses with `status: testing`, checked if enough evidence had accumulated to resolve them, and updated status to `confirmed`/`rejected` with supporting evidence.

**Output shape:**
```
Per hypothesis:
- name (hypothesis_<slug>)
- status: testing | confirmed | rejected
- claim, metric, evidence
```

**Data sources:**
- `memory_recall(query="hypothesis", type="project", limit=20)`

**Known issues:**
- "Enough evidence" was heuristic and hard to automate.
- No correlation with outcome patterns; hypotheses were reviewed in isolation.
- No expiry mechanism for stale hypotheses (e.g., testing the same thing for 3 months with no new data).

**Candidate destination:** `/self-improve` (hypothesis lifecycle management).

---

## Step 8 — Flag stale memories

**What it did:** Recalled project-scope memories and flagged any not updated in 14+ days (excluding hypotheses). These were surfaced in the output as reminders to revisit or archive.

**Output shape:**
```
Per stale memory:
- name
- last_updated
- age (days since last update)
```

**Data sources:**
- `memory_recall(type="project", limit=20)` + filter by `updated_at < now() - 14 days`

**Known issues:**
- 14-day threshold was arbitrary; archival decisions were manual.
- No distinction between "intentionally stable" memories (e.g., architectural decisions) and "actively-tracked" ones.

**Candidate destination:** `/self-improve` (memory hygiene + archival).

---

## Known dangling refs

The following files reference `/reflect` as a skill trigger or output consumer. They will need re-routing after #515 and #516 are grilled:

1. **`.claude-userlevel/skills/implement/SKILL.md`** — references `/reflect` as post-implementation checkpoint. **Status (post-#510, verified 2026-05-17):** no `/reflect` refs remain in the file — cleaned during skill restructuring. Entry preserved as historical inventory.
2. **`.claude-userlevel/skills/grill-me/SKILL.md:36`** — line 36 mentions `/reflect` output in decision grilling (skill deleted in #528 — Pocock grill restored to upstream form has no `/reflect` ref; entry preserved here as historical inventory)
3. **`.claude-userlevel/skills/to-issues/SKILL.md:22`** — line 22 routes outcome verification to `/reflect`. **Status (post-#510, verified 2026-05-17):** no `/reflect` refs remain in the file — cleaned during skill restructuring. Entry preserved as historical inventory.
4. **`.claude-userlevel/skills/to-prd/SKILL.md:18`** — line 18 mentions `/reflect` for lessons extraction. **Status (post-#510, verified 2026-05-17):** no `/reflect` refs remain in the file — cleaned during skill restructuring. Entry preserved as historical inventory.
5. **`scripts/recall-audit.py`** — multiple references to `/reflect` in docstrings + cli help text. **Status (verified 2026-05-17):** refs still present (the file's L3 NOTE explicitly flags them as transitional pending #516 resolution); cleanup blocked until `/self-improve` absorbs the aggregate consumer surface.

These references will be re-routed to `/verify` and/or `/self-improve` in follow-up issue #516 after those skills are grilled for scope acceptance.

---

## Migration decision trail

- **Decision 042deab5**: Rename, not wrapper. Single `/reflect` skill absorbs the analyze-comms pipeline.
- **Decision 9bbf9ca8**: Migration snapshot at this file, structured per-aggregate.
- **Decision b6a0e343**: Snapshot all 8 steps with candidate destinations; final routing deferred to future grills.
- **Decision 6b2d3daf**: Cross-skill `/reflect` refs stay AS-IS in this PR; cleanup deferred to #516.
