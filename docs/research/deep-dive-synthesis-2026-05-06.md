# Deep-dive synthesis — 5 topics, cross-cutting

Date: 2026-05-06
Inputs: 5 deep-dive docs in `docs/research/deep-dive-*.md`
Trigger: Telegram msg 161 → wide sweep → top-5 selection → 5 parallel deep dives

## Sourcing health (read this first)

4/5 deep dives executed in subagents that hit a sandbox web-block; the Ralph dive ran inline in the main session after sandbox was confirmed-blocked. **None of the docs are sourced from full primary-source reads.** All five flag unverified claims inline. Confidence numbers below reflect that.

To upgrade verification: owner runs `install.ps1 -Apply` (canonical permission edit is committed to `.claude-userlevel/settings.json`, awaiting propagation), then `/research` re-runs against any of the five with WebFetch/firecrawl unblocked.

## 1. The five docs at-a-glance

| # | Topic | Doc | Top recommendation | Confidence |
|---|---|---|---|---|
| 1 | Hamel-style evals | `deep-dive-evals-hamel.md` | 30-min manual error analysis on 20-30 `/implement` PRs first; trace-capture in `/implement`+`/delegate`; in-house JSONL until ~200 cases | 4/5 structural |
| 2 | Spec-Driven Development | `deep-dive-spec-driven-development.md` | Shrink `/to-prd` to ≤500-word PRD + `specs/<feature>/spec.md` executable artefact; new `/spec-validate` becomes real validation gate | 4/5 structural |
| 3 | Memory beyond vector | `deep-dive-memory-architectures.md` | Add `'procedural'` to VALID_TYPES; activate dangling extractor; A-MEM-style typed edges on `memory_links`; lightweight `entities` tables in same Postgres | 3-4/5 |
| 4 | llms.txt + LLM-friendly docs | `deep-dive-llms-txt.md` | Ship minimal ~40-line jarvis `llms.txt` as router (NOT content); redrobot is a Sergazy decision; HARD NO on `llms-full.txt` | 4/5 jarvis, 2/5 redrobot |
| 5 | Ralph loop + back-pressure | `deep-dive-ralph-loop-backpressure.md` | Pilot Ralph on §4.4 skill-test scaffolding; never on net-new features (§4.5) or bug diagnosis (§4.6) | 5/5 structural |

## 2. Cross-cutting findings

### 2.1 The five docs are not parallel — they compose

```
            grill-me (existing)
                  ↓
            spec [#2 SDD]
                  ↓
       ┌─── /to-issues + /implement ───┐
       │                                │
   eval [#1]                       Ralph loop [#5]
       │                                │
       └─── outcomes [#3 procedural memory] ───┘
                  ↓
            llms.txt [#4] (publishes the surface)
```

- **#2 (SDD) feeds #1 (evals).** Executable spec → unambiguous pass/fail rubric. Without SDD, eval judges have to infer intent from prose PRDs.
- **#5 (Ralph) consumes #2 (spec) as termination criterion.** Without an executable spec Ralph hits "agent optimizes the wrong thing." Composition kills that failure mode.
- **#3 (procedural memory) consumes #1 (eval traces).** Eval failures → procedural memories → next agent starts with the lesson loaded. This is Pillar 4 ⟷ Pillar 5 (eval) becoming a feedback loop.
- **#4 (llms.txt) is independent and lowest-cost** — but only if it's a router (not content). It earns its keep when *external* agents (or owner's other Claude Code sessions) start consuming the repo.

### 2.2 The eval/spec pair is a single move, not two

Doing #1 without #2 means eval judges grade against fuzzy intent. Doing #2 without #1 means specs become writeable but not measurable. **Sequenced together they create the missing validation gate.** Treat as one initiative on the milestone.

### 2.3 Procedural memory is a precondition for "scale up" of the others

#5 (Ralph) generates many traces. #1 (evals) generates many failure cases. Without #3's procedural slot, those don't compound into agent improvement — they just sit in Postgres. #3's "activate the dangling extractor" recommendation is the cheapest of the bunch (1d, conf 4/5) and unblocks the others' value capture.

### 2.4 The blast-radius gradient

From safest to riskiest:
1. **#4 jarvis llms.txt** — additive doc file, no behavior change, trivially revertable
2. **#3 step 1 (procedural memory + extractor)** — adds an enum value + activates a dangling pipeline; no rewrites
3. **#1 trace-capture in `/implement`** — additive instrumentation, doesn't change behavior, just emits JSONL
4. **#2 specs/ scaffolding (Phase 0 with `--spec` flag)** — additive, opt-in
5. **#1 first eval suite + LLM judge** — introduces grading; needs alignment work
6. **#2 `/spec-validate` as the new gate** — changes pipeline semantics
7. **#5 Ralph pilot on skill-tests** — autonomous loop, even with safety
8. **#3 step 3 (entities + edges)** — schema additions, cross-project naming concerns
9. **#5 Ralph on real refactor** — wider blast radius

### 2.5 The eval/spec/Ralph triple is one strategic shift, not three tactical adds

Reading the docs sequentially, it becomes clear: SDD + evals + Ralph together = the shift from "agent assists owner-driven coding" to "agent ships under owner-defined guard rails." Each alone is incremental. Together they are the architectural change.

## 3. Recommended sequence (one-month view)

### Phase 0 — bookkeeping (1 day, this week)
- Commit `.claude-userlevel/settings.json` allow-rule additions; PR; owner runs `install.ps1 -Apply`
- Decide: do the five deep dives become five GitHub issues on next milestone, or one umbrella issue with five children? (Recommendation: umbrella + 5 children, since they compose.)

### Phase 1 — capture before changing (2-3 days)
**#1.A — manual error analysis.** 30 min on 20-30 recent `/implement` PRs (Hamel's prescribed move). Output: `evals/notes/error-analysis-2026-05.md`. **Don't write judges yet.**
**#3.A — activate the dangling episode→semantic extractor.** ~1d. Adds `'procedural'` to `VALID_TYPES`. Backfills nothing — just unblocks future capture.

These two unlock measurement of everything else without changing behavior. Cheap. Low blast radius.

### Phase 2 — instrumentation (3-5 days)
**#1.B — trace-capture in `/implement` and `/delegate`.** Writes `evals/traces/<id>.json` at end of pipeline. Additive only. Eval cases become bootstrap-able from real runs.
**#4 — minimal jarvis llms.txt** as a router. Ship as a Fix-track inline (CLAUDE.md #428). 30-min commit.

### Phase 3 — first validation gate (1 week)
**#2.Phase0 — specs/ + `--spec` flag** on `/to-prd` and `/to-issues`. Opt-in, additive. Pilot on one upcoming feature. Measure: does /implement complete in fewer iterations when spec exists vs prose PRD?
**#1.C — first 10 eval cases on `/implement`**, all binary pass/fail, 3-4 from known failures. First LLM judge. Align to ≥80% TPR/TNR before deploying.

### Phase 4 — graduate the gate (1-2 weeks)
**#2.Phase1 — `/spec-validate` as the new validation gate.** Makes specs first-class. Measure pass-rate band 60-80% (Hamel).
**#3.B — A-MEM-style typed edges on `memory_links`** + LLM rationale. ~1.5d.

### Phase 5 — autonomous loop pilot (after 2-4 of the above)
**#5 Ralph pilot on §4.4 (skill-test scaffolding).** Branch `chore/ralph-pilot-skill-tests`. Hard prerequisites: trace-capture (#1.B), spec-validate (#2.Phase1) so the termination criterion is grounded.

## 4. Actionable issue map for next milestone

Suggested issue split if owner agrees with the Phase 0-5 sequence:

- `[research-followup] Phase 0: propagate web permissions + open PR` — chore
- `[evals] Phase 1.A: manual error analysis of 20-30 /implement PRs` — research
- `[memory] Phase 1.B: add 'procedural' type + activate dangling extractor` — feat
- `[evals] Phase 2: trace-capture in /implement and /delegate` — feat
- `[docs] Phase 2: minimal jarvis llms.txt router` — docs (Fix-track inline)
- `[skills] Phase 3: specs/ scaffolding + --spec flag on /to-prd /to-issues` — feat
- `[evals] Phase 3: first 10 eval cases + LLM judge for /implement` — feat
- `[skills] Phase 4: /spec-validate as new validation gate` — feat
- `[memory] Phase 4: typed edges + LLM rationale on memory_links` — feat
- `[skills] Phase 5: Ralph pilot on skill-test scaffolding` — feat (gated)

Approx 10 issues, sized M-L. Likely 1.5 milestones.

## 5. What I'm NOT recommending

- **Don't adopt Spec Kit / Augment / Kiro / OpenSpec wholesale.** Steal the seven-phase model + ADR-coexistence pattern; keep implementation native to skills (deep dive #2).
- **Don't add Neo4j / Memgraph / AGE / Zep-as-a-service.** Stays in same Postgres; $20/mo external budget honoured (deep dive #3).
- **Don't ship `llms-full.txt`.** Audience zero, drift cost real (deep dive #4).
- **Don't put Ralph on net-new features or bug diagnosis.** Wrong tool — `/grill-me + /tdd` and `/diagnose` stay (deep dive #5).
- **Don't deploy Braintrust yet.** Single-grader solo-dev shape doesn't justify it; revisit at ~200 eval cases or >10 min CI runtime (deep dive #1).

## 6. Open decisions for owner

Aggregated from the five docs, deduplicated:

1. **Spec format** for SDD pilot — Gherkin (deep dive #2 default) vs structured-markdown vs JSON-schema?
2. **Eval judge model** — Sonnet for accuracy, Haiku for cost, or split (Sonnet for E2E judges, Haiku for binary checks)?
3. **First Ralph pilot scope** — §4.4 skill-tests (recommended), §4.1 deepen-shallow-modules, or §4.3 dependency upgrade?
4. **Procedural memory authority** — who decides what's `procedural` vs `feedback`? Classifier prompt, or human-in-loop on each store?
5. **llms.txt for redrobot** — defer to Discussion with Sergazy, or skip?
6. **Eval suite location** — `evals/` in jarvis repo (recommended), or split per-skill in `.claude/skills/<name>/evals/`?
7. **Cross-project entity namespacing** in graph memory — jarvis and redrobot share Supabase. Namespace via project column or via name prefix?
8. **Are the five deep-dive docs themselves merge-bound?** (They live under `docs/research/` — I'd ship as one PR. Owner may prefer to leave on a research branch.)

## 7. Memory + commit hygiene

- Save this synthesis as `research_deep_dive_synthesis_2026_05_06` (reference, project=jarvis).
- The five deep-dive docs + synthesis under `docs/research/` form a coherent set; commit together with body `Closes <umbrella issue>` once owner triages.
- `record_decision` if owner approves the Phase 0-5 sequence — that's an architectural commitment.
