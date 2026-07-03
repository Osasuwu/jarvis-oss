# Feeling-of-Knowing (Memory Phase 5.3) — Design

**Status:** 5.3-α approved — six decisions ratified via Discussion [#439](https://github.com/Osasuwu/jarvis/discussions/439) (2026-04-27). 5.3-β/γ/δ shipped via milestone #34 (CLOSED 2026-04-29). This doc was the PRD; live behaviour reflects shipped state — the §2 "what's already in the tree" table captured the pre-5.3 baseline (some "shipped" rows describe pre-#444 inline-upsert state, retained as historical context). Inline `Status (#N)` annotations below mark where descriptions changed post-shipment.
**Closes:** #420.
**Parent issue:** #185.
**Sprint:** Milestone #34 (Pillar 4 Sprint: feeling-of-knowing 2026-04-26).

This doc is **gap-analysis from #250**, not a clean-slate design. #250 (closed) shipped the recall-event emit and a working `scripts/fok-batch.py`. The work is now (a) reaffirm what's still right, (b) revise what drifted or didn't account for downstream substrate, (c) add the calibration loop that's genuinely new for 5.3.

---

## 1. What "feeling-of-knowing" means here

In Nelson-Narens (1990) metamemory, **FOK is the agent's judgment that it can or cannot retrieve a target it cannot currently access**. Adapted to a vector-recall pipeline: after `memory_recall` returns top-K, FOK is the secondary judgment "is what we got *sufficient* for the query at hand."

Without it, the agent silently accepts whatever the retrieval pipeline returns. Recall@5 on the eval set is 85% — meaning 15% of "real" recalls miss the target. The agent has no signal which 15%.

What FOK gives:
- **Per-recall verdict** (sufficient / partial / insufficient) — used to widen, gap-record, or pass through.
- **Aggregated calibration** — does the judge's verdict actually predict downstream task success? Lets us tune the prompt without flying blind.
- **Known-unknown promotion** — repeated insufficient verdicts on similar queries become tracked gaps surfaced by `/status`.

**Not FOK** in this iteration:
- Pre-recall predictions ("do I know X before retrieving?") — that's a separate primitive (Phase 6).
- Confidence on individual stored memories — already covered by `memories.confidence` + `memory_calibration` view.

---

## 2. What's already in the tree (#250 carried)

| Component | Status | File / Object |
|---|---|---|
| Recall event emit (fire-and-forget) | shipped | `_emit_recall_event` in `mcp-memory/handlers/memory.py`; payload: `{query, returned_ids, returned_similarities, returned_count, top_sim, threshold, project, type_filter, show_history}` |
| Events table | shipped | `events` (`mcp-memory/schema.sql`), `event_type='memory_recall'` |
| Batch judge | shipped | `scripts/fok-batch.py` — Haiku judges last-24h unfudged events, writes verdict back to `events.payload` |
| Known-unknowns table | shipped | `known_unknowns` (#249) |
| Known-unknowns from FOK | shipped (batch) | `try_insert_known_unknown` in `fok-batch.py` — `verdict=insufficient AND confidence<0.7 AND top_sim<0.6` triggers insert |
| Known-unknowns from recall | shipped (sync) | `_hybrid_recall` itself upserts `known_unknowns` at `top_sim < GAP_THRESHOLD=0.45` (memory.py:374) **and again** at `top_sim < 0.45` hardcoded (memory.py:397) — duplicate path. **Status (#444):** both inline upserts removed in Phase 5.3-γ; gap detection now lives in the batch FOK judge (per D5 plan below) |
| Calibration substrate | shipped | `memory_calibration` view (#251), `decision_made` episode (#252), `task_outcomes.memory_id` FK |

| Gap | Why it matters |
|---|---|
| Judge model is `claude-3-5-haiku-20241022` | Project standard moved to Haiku 4.5 (`claude-haiku-4-5-20251001`); 3.5 Haiku is on retirement track |
| No scheduled cadence | Script exists but no scheduled-task entry. Ran ad-hoc; events accumulate unjudged |
| Verdict stored in `events.payload` JSONB | Hard to FK to outcomes for calibration; awkward `WHERE payload->>'fok_verdict'` queries; no model/version columns |
| UserPromptSubmit hook calls match RPCs directly | `scripts/memory-recall-hook.py` bypasses `_handle_recall`, so its (high-volume) recalls are NOT in `events` and therefore NOT judged |
| No calibration of the judge | Verdicts written, never compared to whether the downstream task actually succeeded |
| Two redundant inline gap-detect paths | `_hybrid_recall` upserts `known_unknowns` twice in the same function call on the same condition. **Status (#444):** removed in Phase 5.3-γ |

---

## 3. Six decisions

### D1 — Trigger surface: REAFFIRM batch consumer of recall events

**Choice:** keep batch consumer reading `events WHERE event_type='memory_recall' AND payload->>'fok_verdict' IS NULL`. Run via scheduled task (cron `30 10 * * *`, after consolidation `0 10` and evolve `0 11`).

**Why not in-band post-recall hook:**
- Recall is on the hot path. UserPromptSubmit hook fires per prompt (≥10/session). 200–800 ms Haiku roundtrip per recall is unacceptable.
- Same architectural pattern as #232 evolution + #234 weekly cadence — we already operate this kind of fire-and-forget batch.

**Why not direct emit-and-judge:**
- The emit IS the batch's input — separation of concerns. Re-binding judge to emit means recall depends on Haiku availability.

**What changes from #250:** make the cadence real (register `memory-fok-daily` scheduled task) — see §4.

**Reaffirmed.**

### D2 — Judge inputs: REAFFIRM content + similarity, ADD volume controls

**Choice:** judge sees `{query, [(memory_id, content_truncated_2KB, similarity)] for top-5}`. Volume controls:
- Sample, don't process all. Default `--limit 50` per run already encodes this; promote to a hard policy: at most 50 judgments/day.
- Prioritize the interesting cases — events where `returned_count < 3` OR `top_sim < 0.6` OR `returned_count == 0` jump to the front of the batch queue.
- **High-volume recalls (UserPromptSubmit hook) emit events too** (D2-bis below) but participate in the same 50/day budget via prioritization, not by being excluded.

**D2-bis — UserPromptSubmit hook should emit:** `scripts/memory-recall-hook.py` calls `match_memories` + `keyword_search_memories` directly, bypassing `_handle_recall`. Result: ~10–50 hook recalls/session/device × 3 devices fire daily without entering FOK. Add a thin emit at the hook level (same payload shape). The volume cap then handles cost.

**Why not metadata-only (name + type + description + tags):**
- The judge's question is "do these answer the query?" — content matters more than canonical form. Phase 5.2 evolution principle (canonical form for embeddings) is about *vector representation*, not Haiku prompt context.
- 5×2KB = 10KB per Haiku call ≈ ~3000 input tokens. Negligible.

**Cost:** Haiku 4.5 at ~$0.001/judgment × 50/day × 30 = ~$1.50/month. In budget.

**Reaffirmed + scope expanded** to hook recalls.

### D3 — Judge model: MIGRATE to Haiku 4.5

**Choice:** `claude-haiku-4-5-20251001` — current project standard (rewriter, classifier, evolution all on 4.5).

**Why:** 3.5 Haiku has a known retirement timeline; 4.5 is faster and the rest of the system is already on it. No reason to keep a single legacy 3.5 caller.

**Migration:** `fok-batch.py` line 131 — single string change; smoke-test on existing event backlog (`--dry-run --limit 5`); diff verdicts against historical 3.5 verdicts on the same events to flag drift.

**Reaffirmed model name from #250 spec; revising from shipped code.**

### D4 — Output schema: REPLACE `events.payload` inline with `fok_judgments` table

**Choice:** dedicated table.

```sql
CREATE TABLE fok_judgments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recall_event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  query           text NOT NULL,                                -- denorm for indexing
  project         text,
  verdict         text NOT NULL CHECK (verdict IN ('sufficient','partial','insufficient','unknown','skipped')),
  confidence      real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  rationale       text,
  judge_model     text NOT NULL,                                -- 'claude-haiku-4-5-20251001'
  judge_version   text NOT NULL,                                -- prompt/spec version, e.g. 'fok-v1'
  judged_at       timestamptz NOT NULL DEFAULT now(),
  -- Action lifecycle:
  action_taken    text CHECK (action_taken IN ('pass_through','gap_recorded','widened') OR action_taken IS NULL),
  action_at       timestamptz,
  -- Calibration loop (5.3-δ wires):
  outcome_id      uuid REFERENCES task_outcomes(id) ON DELETE SET NULL,
  outcome_correct boolean,                                      -- did the verdict predict the outcome?
  UNIQUE (recall_event_id)                                      -- one judgment per recall
);

CREATE INDEX idx_fok_judgments_verdict ON fok_judgments(verdict, judged_at DESC);
CREATE INDEX idx_fok_judgments_query_project ON fok_judgments(project, query);
CREATE INDEX idx_fok_judgments_outcome ON fok_judgments(outcome_id) WHERE outcome_id IS NOT NULL;
```

**Why dedicated table over `events.payload`:**
- **Calibration loop** (D6) needs FK from judgment → outcome. Inline JSONB makes that join awkward and unindexed.
- **Versioning**: judge model and prompt evolve. Columns capture this; JSONB keys would mean implicit schema drift.
- **Queryability**: `WHERE verdict='insufficient' AND project='jarvis' GROUP BY query` is direct on a real table; clumsy and slow on JSONB.
- **Action lifecycle**: `pass_through` vs `gap_recorded` vs `widened` (D5) is structured state, not a payload field.

**Backward compatibility:** keep writing `events.payload.fok_verdict` for one release as a soft mirror — deprecated read path, removed in 5.3-δ. New code reads `fok_judgments` only.

**Revised from #250.**

### D5 — Action surface: REAFFIRM gap-record, REMOVE inline duplication, DEFER auto-widen

**Three actions, threshold-driven:**

| Verdict | Confidence | top_sim | Action |
|---|---|---|---|
| insufficient | ≥ 0.6 | < 0.6 | `gap_recorded` — upsert into `known_unknowns` (existing `try_insert_known_unknown` logic, now keyed off the new table) |
| insufficient | < 0.6 | any | `pass_through` — judge wasn't confident; don't pollute gap log |
| partial | any | any | `pass_through` (count for /reflect aggregation) |
| sufficient | any | any | `pass_through` |

**REMOVE the two inline `_upsert_known_unknown` calls inside `_hybrid_recall`** (memory.py:374 and :397). Reason: they fire on every recall regardless of whether the returned set is genuinely insufficient. The batch FOK judge subsumes both — judge sees content, threshold doesn't. Net: fewer false-positive gap entries, correct sample only. **Status (#444):** done in Phase 5.3-γ; the inline calls no longer exist in `_hybrid_recall`.

**Auto-widen recall (re-run with relaxed filters):** **DEFER to 5.3-ε / future.** Justification:
- Hot-path-side action; wrong threshold loops or worsens recall.
- Need the calibration data from D6 first to know what threshold actually means.
- Phase 5.3-α…δ is enough work; widening adds risk.

**`/reflect` integration (5.3-δ):** scan last 7d `fok_judgments`, group by query category (entity-extracted), surface clusters where insufficient verdicts repeat. Schema for that report:

```
INSUFFICIENT cluster — 4× last 7d
  Query exemplar: "what's our policy on telemetry redaction?"
  Top hits never included relevant memories.
  Suggested action: store decision memory or run /research.
```

**Reaffirmed gap-record path; revising to single source of truth.**

### D6 — Calibration of the judge: NEW

This is the genuinely new piece in 5.3.

**Question:** when the judge says "insufficient," does the downstream task actually fail more often than when it says "sufficient"? If yes, judge is calibrated. If no (or worse, anti-correlated), prompt needs revision.

**Linkage:** `fok_judgments.outcome_id` populated when:
1. A `decision_made` episode fires (`record_decision` MCP tool).
2. Episode payload `memories_used` overlaps the `recall_event.payload.returned_ids` for any recall in the same session (matched by `(project, ts within 30min)` window).
3. That recall has a `fok_judgments` row → set its `outcome_id` to the eventual `task_outcomes.id` linked from the episode.

The 30-minute window is a **soft heuristic, not a guarantee** — many recalls won't ever be linked to a task outcome (research, browsing, /status). Unlinked is fine; we score only the linked subset.

**Score:** Brier-equivalent, paralleling `memory_calibration_summary`:

```
verdict_score = {sufficient: 1.0, partial: 0.5, insufficient: 0.0, unknown: NULL}
outcome_score = {success: 1.0, partial: 0.5, failure: 0.0, unknown: NULL}
brier = mean((verdict_score - outcome_score)^2) over rows where both sides are non-NULL
```

**Threshold:** judge is "calibrated" if `brier < 0.25` over n ≥ 30 linked rows. Below that count it's noise.

**Surface:** new column in `memory_calibration_summary` output? — no, separate RPC `fok_calibration_summary` returning `{n, brier, by_verdict: [...], drift_signal: bool}`. Wired into `/reflect` (5.3-δ).

**Action when miscalibrated:** flag in `/reflect` output with examples (worst 5 cases by per-row brier). Owner decides: revise prompt, change model, change threshold. Auto-tuning is out of scope.

**New for 5.3.**

---

## 4. Sequence

```
                  ┌────────────────────┐
   user prompt ─► │ memory-recall-hook │  (UserPromptSubmit, brief)
                  └────────┬───────────┘
                           │ writes recall event (NEW for 5.3)
                           ▼
   skill call ───► _handle_recall ───► _hybrid_recall
                                            │
                                            └── _emit_recall_event (existing)
                                                       │
                                                       ▼
                                            ┌─────────────────┐
                                            │  events table   │
                                            │  type=memory_   │
                                            │       recall    │
                                            └────────┬────────┘
                                                     │
   cron 30 10 * * * ──► fok-batch.py ────────────────┘
                              │ Haiku 4.5 judge
                              ▼
                    ┌──────────────────┐       ┌──────────────────┐
                    │ fok_judgments    ├──────►│ known_unknowns   │
                    │ (NEW table)      │       │ (gap_recorded)   │
                    └────────┬─────────┘       └──────────────────┘
                             │
   record_decision ──────────┤ outcome linkage (5.3-δ)
                             │ via memories_used overlap
                             ▼
                    ┌──────────────────┐
                    │ fok_calibration_ │
                    │   summary RPC    │
                    └────────┬─────────┘
                             │
   /reflect ◄────────────────┘
```

---

## 5. Phase split (post-approval)

| Phase | Scope | Status |
|---|---|---|
| 5.3-α | This doc + sub-issue scaffolding | in flight |
| 5.3-β | Migration `add_fok_judgments_table.sql` + dual-write from `fok-batch.py` (table + legacy event.payload mirror) | sub-issue |
| 5.3-γ | Migrate to Haiku 4.5; add hook-level emit; remove inline `_upsert_known_unknown` duplication; register `memory-fok-daily` scheduled task | sub-issue |
| 5.3-δ | Outcome linkage + `fok_calibration_summary` RPC + `/reflect` integration; remove `events.payload.fok_verdict` mirror | sub-issue |

Out of scope (deferred):
- Auto-widen on insufficient (5.3-ε / future).
- Pre-recall FOK ("do I know X before retrieving?").
- Cross-encoder rerank (#185 parent issue, gated separately).

---

## 6. Resolved questions (Discussion #439, 2026-04-27)

What was originally framed as "open questions for owner" turned out to be 5 decisions Jarvis had context to make. Resolutions:

1. **Hook-level emit (D2-bis) — YES.** UserPromptSubmit hook recalls participate in the FOK pipeline. They shape agent context most heavily; sufficiency matters most there. Volume managed by 50/day cap with prioritization (§D2).
2. **Sample budget — 50/day, re-evaluate after 30 days.** Not a hard ceiling; tunable once we have a verdict-distribution baseline.
3. **30-min outcome-linkage window — keep as default.** Stretch in 5.3-δ: extend `record_decision` to capture `recall_event_ids` explicitly. Don't gate β on this.
4. **Backward-compat mirror — KEEP for one release.** Drop in 5.3-δ. Cost is one extra `update` call; benefit is no silent breakage of any consumer reading `events.payload.fok_verdict`.
5. **Inline `known_unknowns` removal — REMOVE as planned in D5.** No backstop. Cron reliability is part of operational hygiene; >24h lag is an alert-worthy event for `/status`.

---

## 7. References

- **Nelson, T. O., & Narens, L. (1990).** *Metamemory: A theoretical framework and new findings.* Psychology of Learning and Motivation, 26, 125–173. — formal split between object-level (memory) and meta-level (FOK / judgment of learning) cognition.
- **Wu et al. (2024).** *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.* arXiv:2410.10813. — explicit treatment of "did the system retrieve enough" as a measurable axis distinct from recall@k.
- **Mem0 (Mem0AI):** ADD/UPDATE/DELETE/NOOP classifier on the *write* side. We adapt the same idea to the *judgment* side — same Haiku-class fire-and-forget pattern.
- **Letta (formerly MemGPT):** explicit core-context vs archival distinction; doesn't have FOK as a primitive but does sufficiency judgments via tool-use flow.
- **Zep / Graphiti:** bi-temporal episode store; doesn't ship FOK directly but their entrenchment scoring depends on similar feedback signal.

---

## 8. Decision summary (ratified)

| # | Topic | Choice | vs. #250 |
|---|---|---|---|
| D1 | Trigger | Batch consumer of `events`, scheduled cron | reaffirm |
| D2 | Inputs | `{query, top-5 (id, content<2KB, sim)}` + sample 50/day, prioritize low-sim/low-count, hook recalls included | reaffirm + scope expansion |
| D3 | Model | `claude-haiku-4-5-20251001` | reaffirm spec, revise shipped code |
| D4 | Storage | New `fok_judgments` table, `events.payload` becomes deprecated mirror (drop in 5.3-δ) | revise |
| D5 | Actions | gap_record (≥0.6 conf, <0.6 sim); pass_through; remove inline dup; defer auto-widen | refine + cleanup |
| D6 | Calibration | Brier-equivalent over linked outcomes, n≥30 threshold, surface in `/reflect` | new |

**Approved 2026-04-27 via Discussion [#439](https://github.com/Osasuwu/jarvis/discussions/439). 5.3-β/γ/δ proceed.**
