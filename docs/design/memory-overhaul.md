# Memory overhaul — research synthesis + design proposal

**Status:** this doc was the PRD; live state has moved on. Phase 0 (schema foundation) + Phase 1 (recall correctness) + Phase 2 (write-side correctness) + Phase 5.1d-α/β consolidation (#226) + Phase 5.2-α/β/γ A-MEM evolution (#230/#231/#232/#234) + Phase 5.3 feeling-of-knowing (#250/#251/#252/#444, milestone #34 closed 2026-04-29) + dual-embedding readiness (#242) have shipped. Parent: #185 (open, Pillar 4 umbrella). Section 1 below is the original gap inventory — read it as the *PRD baseline*, not current state; sections that have moved on carry inline `Status (#N)` pointers where known.
**Context:** Memory pillar. Current system has measurable lifecycle bugs; before fixing ad-hoc, principal asked for research on (a) how production LLM agent systems solve the same problems, (b) theory of agent memory. Two parallel research agents ran; this doc consolidates findings with our local audit.

## 1. Local audit — what's actually broken

**Status (PRD baseline, 2026-04-18):** the table below is the original 10-gap inventory that motivated Phases 0–5. Gaps 1–10 are largely addressed by the shipped phases listed in the doc header; specific per-gap ship-PR mapping lives in the Phase-N sections + #185 sub-issue thread. Do not read this table as live state.

Verified against live Supabase state (2026-04-18):

| # | Gap | Evidence | Impact |
|---|-----|----------|--------|
| 1 | `supersedes` link type defined, never created | 223 links, all `related`, zero `supersedes` | Contradicting decisions coexist in recall |
| 2 | `SUPERSEDE_SIM_THRESHOLD=0.85` unreachable | Real paraphrase similarity ≈0.80 | Auto-link never marks supersession |
| 3 | Auto-link supersedes gated on `type=decision` only | `_create_auto_links` in server.py | User/feedback/project memories can't supersede |
| 4 | Recall has no supersedes/archived filter | `_hybrid_recall`, `match_memories` RPC | Even if links existed, stale items still surface |
| 5 | Temporal scoring uses `updated_at` | `_apply_temporal_scoring` | Session-start touching memories resets age-decay clock |
| 6 | Archive is a dirty hack (`type = type \|\| '_archived'`) | `archive_memories` RPC | Breaks type-filtered recall; abuses type column |
| 7 | `deleted_at` column exists, most RPCs don't check it | Schema alter + RPC grep | Soft-delete is inconsistent |
| 8 | `find_consolidation_clusters` RPC exists, never invoked | No scheduled task calls it | Consolidation capability idle |
| 9 | No provenance on memories | No `source` column | Can't tell principal-stated from tool-output |
| 10 | No confidence / entrenchment ordering | No column | Contraction undefined when conflicts exist |

Two concrete contradicting memories observed: `jarvis_stays_goals_not_agile` (Jan) and `jarvis_v2_hybrid_agile` (Mar). Description of the latter explicitly says "replaces" the former. They're linked as `related` (strength 0.757), both surface in recall, no supersession marker.

## 2. Convergent signals from theory + production

Items flagged by **both** research streams — strongest evidence, should be in any design:

### 2a. Bi-temporal timestamps (valid time ≠ transaction time)
- **Theory (Snodgrass / Allen):** bitemporal is the minimum correct schema; single `updated_at` makes retroactive correction formally impossible.
- **Production (Zep/Graphiti):** every edge carries four timestamps: `created_at`, `expired_at`, `valid_from`, `valid_to`. Non-destructive; on contradiction, older edge's `valid_to` = new edge's `valid_from`.
- **Us:** only `updated_at`, bumped by session-start.

### 2b. Provenance / justification on every fact
- **Theory (Doyle, JTMS):** beliefs without justifications cannot be revised correctly. Revision needs source chain.
- **Production (memory-poisoning lit, 2025):** `MemoryGraft`, `AgentPoison` — stored-memory injection is a real attack class. Defense starts from provenance tagging (`principal_stated` / `tool_output` / `web_ingested` / `subagent_reported`).
- **Us:** no source column. Web-research output and principal statements are indistinguishable.

### 2c. ACT-R-style retrieval scoring (not raw cosine)
- **Theory (Anderson-Schooler, ACT-R):** rank by `relevance × recency-weighted usage frequency × context match × entrenchment / interference penalty`. Cosine alone is a category error.
- **Production (Claude Code memory-MCP, Memary):** `confidence × log(access_count+1) × decay(type, age)`. Type-dependent half-lives.
- **Us:** we do RRF hybrid + temporal re-rank, but use `updated_at` (wrong axis), no confidence, no access frequency, no context match.

### 2d. Explicit write-time classifier (ADD / UPDATE / DELETE / NOOP)
- **Theory (AGM Levi identity):** `K ∗ p = (K ÷ ¬p) + p` — contract negation *before* adding new belief. Requires an explicit decision, not append.
- **Production (Mem0):** LLM sees top-k neighbors + candidate, emits one of ADD/UPDATE/DELETE/NOOP. Their published LoCoMo benchmark is 91.6, p95 200ms.
- **Us:** our new dedup hook (`memory-dedup-check.py`) is the *detection* half (block on ≥0.78 similarity). Still missing the decision half.

### 2e. Episodic ↔ semantic separation with consolidation
- **Theory (McClelland CLS, Tulving):** single-shot writes to the "distilled" store cause catastrophic interference. Fast episodic buffer + slow semantic consolidation (offline batched) is the computational argument.
- **Production (Letta tiered memory, A-MEM evolution, LangMem background mode):** raw episodes non-lossy, semantic extraction async. Reruns with better models later.
- **Us:** single `memories` table. Every write goes direct to the "distilled" layer.

### 2f. Entrenchment / confidence ordering
- **Theory (Gärdenfors):** contraction nondeterministic without explicit entrenchment preorder. Drop least entrenched. User-stated > inferred > default.
- **Production:** confidence scores on writes, low-confidence hidden from default recall.
- **Us:** none.

### 2g. Background consolidation as a *real* job
- **Theory (CLS offline consolidation, Darwiche-Pearl iterated revision):** proper revision needs batch re-coherence, not per-write.
- **Production (LangMem background mode, Zep community summaries):** Haiku-class model runs async, groups by type+tag, detects pairwise conflicts, emits merge plan.
- **Us:** weekly hygiene task exists, doesn't do anything useful. `find_consolidation_clusters` RPC idle.

### 2h. Retrieval-induced forgetting / context rot
- **Theory:** repeatedly retrieving belief A inhibits sibling B/C; long-running retrieval history silently diverges from storage.
- **Production (LongMemEval):** more memory at session start actively *hurts* — 30-60% accuracy drop with full long memory vs curated extracts.
- **Us:** session-start hook injects profile + feedback + decisions + working state + goals. No measurement. Could be hurting us; we'd never know.

## 3. Signals only one side flagged — still worth catching

**Production-only:**
- **Embedding model migration hazard.** Voyage-3-lite → next model = whole corpus becomes incomparable. Store `embedding_model` + `embedding_version`, support dual columns during migration. (Our single biggest unnoticed production risk.) **Status (#242):** foundation shipped — `embedding_v2` column + `match_memories_v2` RPC, `EMBEDDING_MODEL_PRIMARY`/`SECONDARY` env vars drive dual-write + read-path selection. Zero behavior change while SECONDARY unset. Corpus backfill + episode_extractor dual-write are separate follow-ups.
- **Collections vs Profiles split.** Some memories should be overwrite-single-row (`principal_preferences`, `device_config`); others append (`decisions`). Without the distinction, you fight supersession bugs forever on the overwrite ones. One column: `single_instance BOOLEAN`.
- **Task-aware recall = query rewriting.** Cheap LLM call turns `{user_prompt, recent_turns}` into `{intent, entities, type_filter, tag_filter, timeframe}` *before* vector search. Don't embed the raw prompt — biggest quality win on relevance.
- **A-MEM memory evolution (second step).** New memory arrives → LLM also rewrites context/tags of linked neighbors. We have auto-linking; we don't have the in-place rewrite. Without it: linked graph of frozen interpretations.
- **Evaluation set.** 20 hand-written `(query, expected_memory_ids)` pairs, re-run weekly, track recall@5 and MRR. Without this, every "improvement" is vibes.
- **Cross-session actor namespacing.** Memory written in autonomous mode ("I decided X because no one was around") ≠ principal-stated. Namespace by actor, not just project.
- **Embed canonical form not raw content** (Cognee, A-MEM). Embed `{name, type, tags, one_line_description}` not raw note text — survives rewrites and model upgrades.

**Theory-only:**
- **Metacognition (Nelson-Narens).** Feeling-of-knowing, confidence, explicit "known unknowns". Without this, agent detects contradictions but not *gaps*. ToT state is the missing piece.
- **Darwiche-Pearl iterated revision.** Storing beliefs isn't enough — you need to store the *belief state* (beliefs + their entrenchment ordering) and update both. Single-pass AGM drifts across iterations.
- **Spohn ranking functions.** The right formal substrate for quantitative belief change (ordinal conditional functions, ranks as grades of disbelief).
- **Quine web-of-belief / holism.** Revising one belief has non-local consequences. Central beliefs should be more entrenched precisely because revising them cascades.

## 4. What the theorists flagged as traps (we hit most of them)

1. Justification-free facts → we're here
2. Monolithic similarity → we're here (mitigated by hybrid + RRF, not enough)
3. Catastrophic interference from direct writes to distilled layer → we're here
4. One-shot revision (no iterated-revision state) → we're here
5. No transaction time (only `updated_at`) → we're here
6. No entrenchment ordering → we're here
7. Collapsing types (episodic = semantic) → we're here
8. No metacognition → we're here
9. Holism ignored → we're here (links exist but no cascade)
10. Retrieval-induced forgetting silent drift → unmeasured
11. "Forgetting is bug" mindset → we're here (archive is a hack, nothing decays properly)

## 5. Proposed design — phased

Priorities ordered by (value × low-cost). Each phase is independently shippable and independently valuable.

### Phase 0 — schema foundation (migration only, no code)

One migration, zero risk if reversible:

```sql
ALTER TABLE memories
  ADD COLUMN content_updated_at timestamptz,      -- split from updated_at
  ADD COLUMN last_accessed_at_v2 timestamptz,     -- distinct from updated_at; rename after backfill
  ADD COLUMN valid_from timestamptz,              -- bi-temporal: when true in world
  ADD COLUMN valid_to timestamptz,                -- when it stopped being true
  ADD COLUMN expired_at timestamptz,              -- when we stopped believing it
  ADD COLUMN superseded_by uuid REFERENCES memories(id),
  ADD COLUMN confidence real DEFAULT 0.5,         -- [0, 1]
  ADD COLUMN source_provenance text,              -- principal_stated / tool_output / web_ingested / subagent_reported / autonomous_agent
  ADD COLUMN single_instance boolean DEFAULT false,
  ADD COLUMN embedding_model text DEFAULT 'voyage-3-lite',
  ADD COLUMN embedding_version text DEFAULT 'v1';
```

Backfill defaults; don't drop old columns yet.

### Phase 1 — recall correctness (immediate wins)

Gates everything else. Without this, no amount of write-side sophistication helps.

1. Split timestamps — `updated_at` becomes "any write"; `content_updated_at` gates age decay; `last_accessed_at` tracks recall frequency. Session-start hook updates only `last_accessed_at`.
2. Default recall filter: `WHERE expired_at IS NULL AND (valid_to IS NULL OR valid_to > now()) AND superseded_by IS NULL`. Add `show_history` mode that ignores this.
3. Collapse `supersedes` chains in recall: follow `superseded_by` chain, return only the head. (Even without write-side fixes, this makes manually-added `superseded_by` pointers immediately useful.)
4. Fix temporal scoring to use `content_updated_at` not `updated_at`.

### Phase 2 — write-side correctness

1. Replace append-on-write with Mem0-style ADD/UPDATE/DELETE/NOOP classifier. Haiku-class model, sees top-5 neighbors. Cost: ~$0.001/write, budget-negligible.
2. Lower `SUPERSEDE_SIM_THRESHOLD` from 0.85 to ~0.75; remove `type=decision` gate (all types can supersede).
3. On UPDATE/DELETE decision: set `superseded_by` + `valid_to` + `expired_at` on old, not `type='…_archived'` rename.
4. Provenance required on every write (default `principal_stated` if unset — but log warnings to force callers to be explicit).
5. Embed canonical form `"{name}\n{description}\n{tags}"` not raw content.

### Phase 3 — task-aware recall

1. New hook: UserPromptSubmit. Cheap LLM call rewrites prompt → structured query `{intent, entities, type_filter, tag_filter, timeframe}`.
2. Fan out: semantic + keyword + metadata filter + BFS on links.
3. RRF with cross-encoder rerank of top 20 (one Haiku call).

### Phase 4 — episodic layer

1. New `episodes` table — non-lossy conversation/tool-call log, minimal schema (id, actor, kind, payload, created_at).
2. Extractor runs async: episodes → candidate memories → Phase 2 classifier.
3. Benefit: re-extract with better models later without losing source data.

### Phase 5 — consolidation + metacognition

1. Wire up `find_consolidation_clusters` to a real weekly job. Haiku groups by `(type, tag)`, detects pairwise contradictions, emits merge/supersede plan. Auto-apply above confidence 0.9; else queue for principal review.
2. A-MEM evolution second step: on UPDATE, also rewrite linked-neighbor context/tags if meaning shifted.
3. Confidence self-monitor: after each recall, store feeling-of-knowing judgment (did answer sufficiency hit). Feeds entrenchment over time.
4. Known-unknowns table: propositions the agent knows it *should* know but can't retrieve — surfaced proactively.

#### A-MEM evolution pipeline (Phase 5.2-α/β)

First step shipped as `scripts/evolve-neighbors.py` (#230, #231). Offline batch: scans recent `memory_review_queue` rows where `decision='UPDATE' AND status='auto_applied'`, fetches each target's 1-hop live neighbors via `get_linked_memories`, and asks Haiku-4.5 per neighbor whether its tags/description drifted after the (target → candidate) swap. Output is a KEEP / UPDATE_TAGS / UPDATE_DESC / UPDATE_BOTH proposal with confidence — rendered as markdown (default) or JSON (`--json`), upserted as `evolution_plan_YYYY-MM-DD` on `--save-memory`. Fallback pattern matches the Phase 2 classifier: any Haiku/parse/HTTP failure collapses to KEEP with confidence 0, and `new_tags`/`new_description` are required only when the action commits to them — missing payload downgrades to the nearest safe action.

**5.2-β (#232) wires the apply path.** `memory_review_queue.decision` CHECK gains `'EVOLVE'`; new column `evolution_payload jsonb` holds per-neighbor `old_tags`/`old_description` snapshots + parent lineage (`update_queue_id`, `candidate_id`, `target_id`). RPCs `apply_evolution_plan(plan, queue_meta)` and `rollback_evolution(queue_id)` parallel the 5.1b-β consolidation pair — same provenance/load-bearing-field guards, same `auto_applied → rolled_back` lifecycle. The script's `--apply` flag routes plans by **plan-level min confidence**: all proposals ≥ gate (default 0.85) auto-applies with a queue audit row; any below gate queues the whole plan as `pending` for principal review. KEEP-only plans are skipped (no-op). A functional index `idx_review_queue_update_queue_id` on `evolution_payload->>'update_queue_id'` drives the planner's dedup pre-filter. Rollback re-bumps `content_updated_at` via the existing trigger — deliberate tradeoff, rollback is rare and the column is a soft decay signal.

**5.2-γ (#234)** wires the cadence. `scripts/evolve-run.py` is the scheduler wrapper paralleling `consolidation-run.py`: subprocess-calls `evolve-neighbors.py --apply --json --save-memory`, parses `apply_outcomes[]`, emits one `evolve_run` event to Supabase, prints a JSON recap for the scheduled-task session. Registered as `memory-evolve-weekly` via the scheduled-tasks MCP with cron `0 11 * * 0` — Sundays 11:00 Astana, one hour after `memory-consolidation-weekly` so the two jobs don't contend for the same DB txn slots or Haiku budget. Review threshold is lower than consolidation's (severity `medium` at `queued_pending >= 1`, not `>= 3`): evolution plans are smaller-grain than consolidation clusters, so a single queued EVOLVE plan is already worth a review pass. The sync in-request trigger inside `_apply_classifier_decision` stays deferred — offline batch keeps write-path latency bounded while we calibrate the Haiku confidence distribution from the first few weeks of `evolve_run` events.

#### Operating the weekly evolution job (Phase 5.2-γ)

Same shape as the consolidation job (below). `scripts/evolve-run.py` runs weekly, Sundays 11:00 Astana, as `memory-evolve-weekly`. Emits one `evolve_run` event per run (`source='scheduled_task'`, severity `info` on clean / `medium` when `queued_pending >= 1` / `high` on subprocess or parse failure). When `needs_review=true` the task session surfaces a review chip; the queued EVOLVE plans show up in `memory_review_queue` with `decision='EVOLVE' AND status='pending'`. Review uses the shared `consolidation-review.py` CLI (extended for EVOLVE in #235): `--list --kind evolution` shows pending plans with per-row actionable counts, `<queue_id> --diff` renders each proposal's old→new tags/description against the current neighbor state, `--approve` calls `apply_evolution_plan(plan, queue_meta)` and reconciles the resulting snapshots back onto the original pending row so `rollback_evolution` can later restore, `--reject` is a pure status flip (no rollback needed — nothing was applied). Disable: `mcp__scheduled-tasks__update_scheduled_task(taskId='memory-evolve-weekly', enabled=false)`. Manual smoke-test: `python scripts/evolve-run.py --dry-run --no-save-memory --limit 2` from repo root (does not pass `--apply`, still emits an event).

#### Operating the weekly consolidation job (Phase 5.1d-α)

The scheduler runs `scripts/consolidation-run.py` every Sunday at 10:00 Astana — registered as `memory-consolidation-weekly` via the native scheduled-tasks MCP (not OS cron). The wrapper subprocess-calls `consolidation-merge-plan.py --apply --json --save-memory`, parses the JSON summary, writes one `consolidation_run` event to Supabase (`source='scheduled_task'`, severity `info` on clean runs / `medium` when ≥3 new pending / `high` on subprocess or parse failure — must be one of `critical/high/medium/low/info` per the `events` CHECK constraint), and prints a JSON recap the task session reads. When `queued_pending >= 3` the task should surface a review prompt (CLI: `memory_review_queue` rows with `decision in (MERGE, SUPERSEDE_CONSOLIDATION)` and `status='pending'`; a dedicated `scripts/consolidation-review.py` CLI lands in Phase 5.1d #226). To disable: `mcp__scheduled-tasks__update_scheduled_task(taskId='memory-consolidation-weekly', enabled=false)` — or reschedule by re-registering with a different cron. Manual smoke-test: `python scripts/consolidation-run.py --dry-run` from the repo root (does not pass `--apply`, still emits an event).

### Phase 6 — evaluation harness

**Actually belongs at Phase 0.5** — build before Phase 1 so we can measure every subsequent change. Listed last only because it's purely infrastructure:

1. 20 hand-written `(query, expected_memory_ids, expected_types)` pairs.
2. `scripts/eval-recall.py` runs them, prints recall@5 + MRR.
3. Baseline before each phase, diff after.

### Phase 7 — scale (premature now)

- Community summaries (Graphiti). Only past thousands of memories.
- Ontology canonicalization (Cognee). Only when entity dedup becomes a pain.
- Dual-embedding migration machinery. When we actually upgrade models.

## 6. Open questions for principal

1. **Phase ordering.** Above is cost-ordered. Alternative: do Phase 6 (eval) first so we can measure Phase 1-2 quantitatively. Adds ~half-day upfront. Recommend: eval first.
2. **Classifier model.** Mem0 uses GPT-4o at write. We'd use Haiku-4.5. Risk: classifier errors become silent corruption. Mitigation: low-confidence decisions require principal review (above a threshold, auto-apply; below, queue).
3. **Migration strategy for existing corpus.** ~600 memories currently. Options: (a) leave as-is with defaults, rely on Phase 1 filters to gate the worst cases; (b) run one-off backfill through classifier to tag provenance, detect chains; (c) wipe and restart. Recommend: (a). (c) loses decision history.
4. **Scope.** Phases 0-3 + eval is ~a week's work. Phases 4-5 are another week. Stop after Phase 3 and measure, or commit to the whole stack?
5. **The `jarvis_stays_goals_not_agile` / `jarvis_v2_hybrid_agile` concrete pair.** Manually set `superseded_by` now (one-line SQL), or wait for Phase 2 auto-detection. Recommend: manual now, it's a known live bug poisoning recall today.

## 7. Explicitly out of scope

- Replacing pgvector / moving to a different store.
- Switching away from voyage-3-lite.
- Moving memory server out of Python (only justified Python per CLAUDE.md is here).
- Letta/Mem0 wholesale adoption — their write paths are LLM-heavy and we're budget-constrained.

## 8. References

**Local:**
- `mcp-memory/server.py` — current implementation
- `mcp-memory/schema.sql` — current schema
- `scripts/memory-dedup-check.py` — detection half, shipped
- Memory `memory_management_strategy_v1` — prior thinking

**Production systems (research A):**
- Mem0 — [paper](https://arxiv.org/abs/2504.19413), [blog](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm)
- Zep/Graphiti — [paper](https://arxiv.org/abs/2501.13956), [blog](https://blog.getzep.com/beyond-static-knowledge-graphs/)
- Letta/MemGPT — [docs](https://docs.letta.com/concepts/memgpt/)
- A-MEM — [paper](https://arxiv.org/abs/2502.12110)
- LangMem — [concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- Memory poisoning — [MemoryGraft](https://arxiv.org/abs/2512.16962), [AgentPoison](https://billchan226.github.io/AgentPoison.html)
- LongMemEval — [paper](https://arxiv.org/pdf/2410.10813)
- Embedding migration post-mortem — [Decompressed](https://decompressed.io/learn/rag-observability-postmortem)

**Theory (research B):**
- McClelland, McNaughton, O'Reilly (1995) — Complementary Learning Systems
- Alchourrón, Gärdenfors, Makinson (1985) — AGM
- Anderson & Schooler (1991) — Rational analysis of forgetting
- Doyle (1979) — TMS
- Nelson & Narens (1990) — Metamemory
- Darwiche & Pearl (1997) — Iterated belief revision
- Tulving — Episodic/semantic distinction
- Allen (1983) — Interval algebra
