---
title: Decision Quality Measurement Methodology — long-term decision → outcome attribution
date: 2026-05-18
status: working-doc
scope: Methodology for measuring decision quality in jarvis from `record_decision` + `outcome_record` schema. Brier score with N=10/week, counterfactual-lite reasoning, Decision Quality framework adapted for solo-dev+AI, stats-power note, concrete proposals [B4-N].
sources:
  - Brier 1950, "Verification of forecasts expressed in terms of probability", Monthly Weather Review — https://journals.ametsoc.org/view/journals/mwre/78/1/1520-0493_1950_078_0001_vofeit_2_0_co_2.xml
  - Murphy 1973, "A new vector partition of the probability score", J. Appl. Meteorology — https://journals.ametsoc.org/view/journals/apme/12/4/1520-0450_1973_012_0595_anvpot_2_0_co_2.xml (Brier = reliability − resolution + uncertainty decomposition)
  - Wikipedia, "Brier score" — https://en.wikipedia.org/wiki/Brier_score
  - Annie Duke, "Thinking in Bets: Making Smarter Decisions When You Don't Have All the Facts" (Portfolio, 2018) — outcome decoupling, resulting fallacy
  - Tetlock & Gardner, "Superforecasting: The Art and Science of Prediction" (Crown, 2015) + GJP papers — active open-mindedness, granular probability updating
  - Mellers et al. 2015, "Identifying and Cultivating Superforecasters as a Method of Improving Probabilistic Predictions", Perspectives on Psychological Science 10(3) — https://journals.sagepub.com/doi/10.1177/1745691615577794
  - Decision Quality Society — https://www.decisionquality.org (six-element framework: frame, alternatives, information, values, reasoning, commitment)
  - Howard & Abbas, "Foundations of Decision Analysis" (Pearson, 2015) — DQ source text
  - Clearer Thinking calibration tool — https://www.clearerthinking.org/post/2019/10/16/practice-making-accurate-predictions-with-our-new-tool
  - Open Philanthropy calibration training — https://www.openphilanthropy.org/research/calibration-training/
  - Metaculus scoring — https://www.metaculus.com/help/scoring/ (Baseline + Peer scores, log-score-based)
  - Manifold Markets scoring — https://docs.manifold.markets/awards#leagues (Mana profit), https://manifold.markets/calibration
  - Roulston & Smith 2002, "Evaluating probabilistic forecasts using information theory", Monthly Weather Review — log score
  - IARPA ACE program / Good Judgment Project — Mellers et al. 2014 "Psychological strategies for winning a geopolitical forecasting tournament", Psych Science 25(5)
  - Pearl 2009, "Causality" (CUP) — counterfactual ladder (skim, not load-bearing here)
  - Anthropic "On the biology of a large language model" (2025) circuit tracing — referenced in passing for AI-eval frontier
related:
  - mcp-memory/handlers/decision.py (record_decision schema)
  - mcp-memory/handlers/outcome.py (outcome_record schema)
  - mcp-memory/schema.sql L349-385 (task_outcomes table)
  - docs/design/memory-fok.md §D6 (existing Brier-equivalent infra for FOK judge)
  - docs/research/workflow-proposals-table-2026-05-18.md row 4 (origin of this research)
  - memory: decision_calibration_audit_2026_05_18_90d (the 250-decision audit this complements)
---

## Executive summary

1. **Brier score is already half-built in jarvis.** `fok_calibration_summary` RPC ships Brier for FOK judge ↔ task outcome linkage (§D6 of `memory-fok.md`). Decision Brier is a parallel computation over `episodes[kind='decision_made'].payload.confidence` vs the `task_outcomes` it eventually links to. Same shape, different join.
2. **The schema is 90% there.** `record_decision` captures `confidence`, `alternatives_considered`, `reversibility`, `memories_used`. The one missing field is **`prediction` / `success_criteria`** — a falsifiable statement of what "the decision worked" means, recorded *with* the decision so the later outcome judgment isn't post-hoc rationalisation.
3. **N=10/week is fine for trends, terrible for absolutes.** With N=10, 95% CI on Brier is roughly ±0.15. You need ~6 weeks (N≈60) before a 0.10 Brier delta is significant. Mitigation: report Brier as a rolling 60-day window, not weekly point.
4. **Decompose Brier or it lies.** Murphy (1973): `BS = reliability − resolution + uncertainty`. Solo dev with no calibration history has high `uncertainty` (base rate near 0.5); a chance forecaster scores ~0.25 trivially. Track **reliability** (calibration) and **resolution** (discrimination) separately — these are the actionable channels.
5. **Outcome decoupling is the load-bearing concept (Duke).** Good decision + bad outcome ≠ bad decision. Process-quality checks (`alternatives_count ≥ 2`, `memories_used` non-empty, `confidence` set, `reversibility` honest) run independently of Brier and don't wait for outcomes. *Both* metrics ship — calibration + process.
6. **Counterfactual-lite via reference-class forecasting (Kahneman/Tetlock).** Don't try to re-run history. For each closed decision, sample 3 reference-class peers (same `task_type`, similar `reversibility`, ±30 days) and ask: "of the chosen alternative + the rejected ones, which reference-class outcome distribution did each match?" This is a regret estimate, not a causal counterfactual — and that's the right granularity for a solo dev.
7. **Pre-registration matters.** Tetlock's GJP finding: forecasters who write their reasoning + falsification criteria *before* the event score significantly better than those who don't. `record_decision` already does this for rationale; extend to `success_criteria`.
8. **The 250-decision audit is a labeled training set, not a measurement.** Bad/Normal/Good labels on `decision_calibration_audit_2026_05_18_90d` are owner-judgment after-the-fact, not outcome data. Use them to **anchor** the rubric (what does "Good" mean operationally?) — then measure forward.

---

## Q1. Brier score: formal definition + decomposition

### Definition (binary outcome)

For N forecasts with predicted probability `pᵢ ∈ [0,1]` and outcome `oᵢ ∈ {0,1}`:

```
BS = (1/N) · Σ (pᵢ − oᵢ)²
```

Range [0, 1]. Lower is better. **0 = perfect, 0.25 = chance forecaster (always says 0.5), 1 = maximally wrong.**

Source: Brier 1950, MWR 78(1).

### Murphy decomposition (use this — Brier alone is misleading)

Murphy 1973 partitions BS into three terms by binning forecasts:

```
BS = reliability − resolution + uncertainty
```

- **Reliability** (calibration): `(1/N) · Σₖ nₖ · (pₖ − ōₖ)²` — when you said "70%", did 70% happen? Lower is better; 0 = perfectly calibrated.
- **Resolution** (discrimination): `(1/N) · Σₖ nₖ · (ōₖ − ō)²` — do your forecasts vary with outcome? Higher is better; 0 = useless (always predicts base rate).
- **Uncertainty** (base-rate variance): `ō · (1 − ō)` — the irreducible variance of the outcome itself. Independent of the forecaster.

Source: Murphy 1973, J. Appl. Meteorology 12(4); Wikipedia "Brier score" gives the formula in clean notation.

**Why this matters for jarvis:** if owner's outcomes are 90% success (he's a competent solo dev), `uncertainty = 0.09`. A "BS = 0.20" score sounds bad until you note resolution drives 0.10 of it — actually informative. Naked BS without decomposition is misinterpretable at low sample size.

### Reliability diagram methodology (calibration curve)

Bin predicted probabilities, plot `mean(predicted) vs mean(observed)` per bin. The 45° line is perfect calibration.

**Binning at small N:** standard 10-bin (0-0.1, 0.1-0.2, …) needs N ≥ 100 to be readable. For jarvis at N ≈ 10/week (60/quarter):
- **Use 5 bins** (0-0.2, 0.2-0.4, …) — Krzysztofowicz & Long 1991 standard for sparse data.
- Add **isotonic regression** (sklearn `CalibratedClassifierCV`) for a smoothed curve — handles non-monotone bins.
- Report **per-bin N** on the diagram; bins with N < 3 are noise.

---

## Q2. Stats power: when does signal emerge at N=10/week?

### Confidence interval on Brier

Brier is a mean of bounded values; CLT applies. Approximate 95% CI:

```
CI₉₅ = BS ± 1.96 · √(Var(p−o)² / N)
```

Var of `(p−o)²` for well-calibrated p is `~0.05–0.10`. Conservative: CI ≈ ±0.20/√N.

| N (window) | 95% CI width on Brier | Time to accumulate at 10/week |
|---|---|---|
| 10 | ±0.20 | 1 week |
| 30 | ±0.11 | 3 weeks |
| 60 | ±0.077 | 6 weeks |
| 120 | ±0.055 | 12 weeks |
| 250 | ±0.038 | 25 weeks (~6 months) |

**Practical rule:** detecting a Brier delta of 0.05 (the smallest meaningful improvement) requires N ≈ 100 with paired comparison, or N ≈ 250 unpaired. **Don't trend weekly. Trend rolling 60-day.**

### Paired vs unpaired

If you re-score the same decisions under two rubrics (or two judge models), paired-difference CI is ~2-3× tighter. Anthropic *A Statistical Approach to Model Evaluations* (2024) makes this point at length for eval design — same logic applies to decision audits.

### Floor: chance forecaster baseline

If owner's success rate is `s`, a chance forecaster (always predicts `s`) gets:

```
BS_chance = s · (1−s)
```

For `s = 0.85` (typical solo dev shipping: most decisions land OK), `BS_chance ≈ 0.13`. **Your Brier must beat 0.13 to add information over guessing the base rate.** This is the Resolution channel speaking.

---

## Q3. Decision Quality framework adapted for solo dev + AI agent

### Decision Quality Society six elements (decisionquality.org / Howard & Abbas 2015)

A decision is "high quality" if and only if all six are satisfied:

1. **Appropriate Frame** — right question, right scope, right time horizon.
2. **Creative Alternatives** — ≥3 meaningfully different options considered.
3. **Relevant & Reliable Information** — facts current, sources cited, uncertainty owned.
4. **Clear Values & Tradeoffs** — what matters, weighted.
5. **Sound Reasoning** — logic explicit, biases acknowledged.
6. **Commitment to Action** — owner accepts the call, doesn't re-litigate.

A decision can fail any element and still produce a good outcome (Duke's resulting fallacy). DQ measures the *process*; Brier measures the *outcome*; both are needed.

### Adapted to jarvis schema

| DQ element | jarvis `record_decision` field | Process-quality check |
|---|---|---|
| Frame | `decision` (the resolved statement) | Non-empty, scoped to one resolvable question (heuristic: ≤ 1 sentence, has a verb) |
| Alternatives | `alternatives_considered[]` | `len ≥ 2` (≥3 ideal); each has a rejection reason |
| Information | `memories_used[]`, `outcomes_referenced[]` | At least one is non-empty OR `intentionally_empty=true` with rationale |
| Values | `rationale` (the *why*) | Length ≥ 200 chars; mentions tradeoffs |
| Reasoning | `confidence` + `rationale` | `confidence` set; rationale references the memories cited |
| Commitment | `actor` populated, episode UUID captured | Downstream artefacts (issues/PRs) reference the UUID |

**These six checks form a Process-Quality Score 0-6.** Each binary, no Likert. Anti-pattern (per `eval-rubric-design`): 1-5 scales on 8 criteria.

### Sound-reasoning subscale (the hard one)

Process checks above don't catch *bad reasoning that satisfies the form*. Tetlock's Superforecasters share traits that *do* discriminate quality (Mellers et al. 2015, Persp. Psych. Sci. 10(3)):

- **Granular probability updating** — uses 5-10% increments, not just 0/0.5/1.
- **Active open-mindedness (AOM)** — actively seeks disconfirming evidence.
- **Reference-class reasoning** — anchors on similar past cases.
- **Calibration history** — knows their own track record.

Operationalise for jarvis: a `/reflect`-tier LLM judge re-reads N=5 random decisions/week and rates `granularity / aom / reference_class / calibration_awareness` as binary. Calibrate the judge against owner labels (Cohen's κ ≥ 0.6, per `eval-rubric-design` row 135).

---

## Q4. Outcome decoupling (Duke) and the "resulting fallacy"

From *Thinking in Bets* (Duke, 2018):

> "Resulting" is the tendency to equate decision quality with outcome quality. Bad outcomes happen to good decisions, and good outcomes happen to bad ones.

Operationalisation (the 2×2):

| | Good outcome | Bad outcome |
|---|---|---|
| **Good process** (DQ ≥ 5/6, confidence well-calibrated) | "Earned" — replicate | "Bad luck" — keep playbook |
| **Bad process** (DQ ≤ 3/6 or confidence wildly off) | "Got lucky" — fix process anyway | "Bad bet" — fix process AND outcome |

`/verify` already partially handles "Good outcome / Bad process" — it audits whether a decision shipped, not whether shipping was the right thing. Adding the DQ score gates 2×2 routing: "got lucky" cases get surfaced as *process* failures even though `outcome_status=success`.

**Schema gap:** `task_outcomes.quality_score` (0-100) exists but is unused for the decoupling matrix. Repurpose it: `quality_score = 100 * (DQ_score / 6)`, separate from `outcome_status`.

---

## Q5. Counterfactual-lite: "would the alternative have been better?"

### Pearl's ladder, abbreviated

Pearl (2009) defines three rungs: association (correlation), intervention (do-calculus), counterfactual (P(Y_x | X=x', Y=y')). For solo-dev decisions, **don't even try rung 3** — we have N=1 per decision, no replication, no instrumental variables. The right tool is:

### Reference-class forecasting (Kahneman & Tversky 1979; Flyvbjerg 2003-2006)

For a decision D with chosen alternative A and rejected R:

1. Sample 5-10 historical decisions with similar `task_type`, similar `reversibility`, similar surface features.
2. Split into "chose-A-shaped" and "chose-R-shaped" by alternative-text similarity (embedding cosine on `alternatives_considered`).
3. Compare outcome distributions: `P(success | chose-A-shaped)` vs `P(success | chose-R-shaped)` in the reference class.
4. **Output**: regret estimate Δ = `E[outcome | R-shaped] − E[outcome | A-shaped]`. Δ > 0 = "the alternative would likely have gone better in your reference class." This is NOT a causal claim; it's a base-rate observation.

This is cheap (one vector query + a SQL aggregation per audit), defensible, and aligned with how Superforecasters reason (Tetlock & Gardner 2015 — "view from the outside").

### When you can do better than reference class

- **A/B-able decisions** (skill threshold tuning, prompt variants): actually A/B.
- **Reversible decisions** with short feedback: do the alternative *next time* a sibling decision comes up.
- **Irreversible / one-shot** decisions: reference class is the ceiling. Accept it.

---

## Q6. AI-eval frontier (2025): what to steal

### Anthropic / OpenAI on reasoning quality

- Anthropic, *On the biology of a large language model* (2025) — circuit-level interpretability; tracks reasoning circuits *during* a decision. Not directly applicable to jarvis (no white-box access), but reinforces "reasoning trace ≠ reasoning quality" — what gets verbalised in `rationale` is a partial signal.
- Anthropic, *A statistical approach to model evaluations* (2024) — SEM/CI methodology directly applicable; clustered standard errors when one decision touches multiple files / outcomes.
- *Demystifying evals for AI agents* (Anthropic Eng 2025) — one-judgment-per-call, escape hatch, 20-50 task starts. Directly informs the DQ subscale design.

### Pre-registration analog

Pre-registration in science: write hypothesis + analysis plan *before* data. Tetlock's GJP shows the same effect in forecasting — forecasters who pre-commit (rationale + falsification) score better than those who narrate after.

**For jarvis**: add `success_criteria` field to `record_decision` — one-line falsifiable predicate. E.g., for "adopt skill bundle X": "criteria = at 30 days, X is invoked ≥ 5 times AND has no rollback memory." `/verify` reads this when scoring the outcome later.

### LLM-as-judge for hard-to-verify decisions

Pattern from `eval-rubric-design` deep-dive applied here:
- Binary verdict per DQ dimension (6 calls per decision), free-text critique.
- Cohen's κ ≥ 0.6 against owner re-labels on 5 random/week.
- Pin judge model per epoch (Brier trends break silently if judge drifts).

---

## Q7. Methodology spec: compute Brier from existing schema

### Inputs (current schema)

- `episodes WHERE kind='decision_made'` → `payload.confidence`, `payload.outcomes_referenced[]`, `id`, `created_at`.
- `task_outcomes` → `outcome_status ∈ {success, partial, failure, unknown}`, `quality_score 0-100`, `verified_at`.
- Linkage (current): `episodes.payload.outcomes_referenced[]` → `task_outcomes.id` (manual, set when the decision was made and pointed at a *prior* outcome).

### Linkage gap (real)

`outcomes_referenced[]` points **backward** (which outcomes informed this decision), not **forward** (which outcome eventually adjudicated it). The forward linkage is the missing piece.

Two paths:

**Path A — schema-light (recommended):** add `task_outcomes.decision_episode_id uuid REFERENCES episodes(id)`. When `/verify` closes out a decision, set this FK. Brier query joins on it.

**Path B — schema-zero, heuristic:** match by `(project, created_at within 30d, lessons OR outcome_summary contains decision episode UUID prefix)`. Brittle.

**Recommendation: Path A.** One column add, one migration, queryable forever.

### Brier computation steps

```sql
-- For each decision that has a verified outcome:
WITH paired AS (
  SELECT
    e.id              AS decision_id,
    (e.payload->>'confidence')::float AS p,
    CASE o.outcome_status
      WHEN 'success' THEN 1.0
      WHEN 'partial' THEN 0.5
      WHEN 'failure' THEN 0.0
      WHEN 'unknown' THEN NULL
    END AS observed
  FROM episodes e
  JOIN task_outcomes o ON o.decision_episode_id = e.id     -- requires Path A
  WHERE e.kind = 'decision_made'
    AND e.payload ? 'confidence'
    AND o.outcome_status != 'pending'
    AND o.verified_at >= now() - interval '60 days'
)
SELECT
  COUNT(*) AS n,
  AVG((p - observed) * (p - observed)) AS brier,
  AVG(observed) AS base_rate
FROM paired
WHERE observed IS NOT NULL;
```

Numbered steps the `/decision-audit` skill (or cron) runs:

1. **Pull paired set** — last 60 days of `decision_made` ↔ `task_outcomes` joined on `decision_episode_id` (Path A FK), `outcome_status ≠ 'pending'`.
2. **Compute headline Brier** — `mean((p − o)²)` with `o = {1.0, 0.5, 0.0}` per status.
3. **Compute base-rate uncertainty** — `ō · (1 − ō)`. If Brier ≥ uncertainty, you're losing to chance.
4. **Bin into 5 reliability buckets** — emit per-bin `(n, mean_p, mean_o)` for the calibration curve.
5. **Decompose** — Reliability `Σₖ nₖ(pₖ−ōₖ)²/N`, Resolution `Σₖ nₖ(ōₖ−ō)²/N`. Verify `BS ≈ reliability − resolution + uncertainty`.
6. **Stratify** — group by `task_type`, by `reversibility`, by `actor` (which skill). Min N=10 per stratum; below that, skip.
7. **Trend** — store `(brier, reliability, resolution, uncertainty, n)` rows in a `decision_calibration_runs` table (or memory of type `metric`); plot rolling 60-day.
8. **Surface** — if `reliability > 0.10` (consistently miscalibrated) OR `resolution < 0.02` (no discrimination) over N ≥ 60, flag in `/reflect`.
9. **Counterfactual-lite per decision** — for the worst 5 decisions by per-row `(p−o)²`, run reference-class lookup (§Q5) and report regret Δ.
10. **Pre-registration audit** — count decisions in the window with `success_criteria` populated; report ratio. Goal: ≥ 80%.

Output schema (mirrors `fok_calibration_summary` from `memory-fok.md` §D6):

```
{
  "n": 67,
  "window_days": 60,
  "brier": 0.18,
  "reliability": 0.06,
  "resolution": 0.04,
  "uncertainty": 0.16,
  "base_rate_success": 0.80,
  "by_task_type": [...],
  "by_reversibility": [...],
  "calibration_curve": [{"bin": "0.5-0.7", "n": 12, "mean_p": 0.62, "mean_o": 0.50}, ...],
  "worst_cases": [{"decision_id": "...", "p": 0.9, "o": 0.0, "regret_estimate": 0.4}, ...],
  "pre_reg_ratio": 0.43,
  "drift_signal": false
}
```

---

## Q8. Sampling bias (the silent killer)

User's gap, restated: jarvis only records when a "decision-shaped exchange" happens. The 9-of-10 things that *weren't* decisions are invisible. Two failure modes:

1. **Selection on the dependent variable** — only confident, articulated calls become `record_decision`. Tentative/implicit calls (which may be the *miscalibrated* ones) are missing.
2. **Drift in what counts as "decision-shaped"** — over time the bar moves; trend lines reflect coverage drift, not quality drift.

Mitigations:

- **Sample audit**: weekly, `/reflect` reads N=10 random session transcripts and flags exchanges that *should have been* `record_decision` but weren't. Reports `missed_decision_count / total_sessions`. Drift signal.
- **Trigger gating** (already in CLAUDE.md): "irreversibility, confidence<0.7, policy/schema/tag/config change, architectural direction" → MUST emit. The list is the calibration anchor; expand it on each missed-decision finding.
- **Post-hoc marker** is already in CLAUDE.md (`actor=session:<id>:post-hoc`). `/reflect` should count `post-hoc` ratio and trend; rising = trigger list too narrow.

---

## Q9. Calibration tools to use directly

- **Clearer Thinking Calibrate Your Judgment** ([https://programs.clearerthinking.org/calibrate_your_judgment.html](https://programs.clearerthinking.org/calibrate_your_judgment.html)) — referenced in row 4. Free, 10-25 question batteries, gives Brier score + reliability diagram + AUC. Owner-side practice for the *human* in the loop.
- **Open Philanthropy Calibration Training** ([https://www.openphilanthropy.org/research/calibration-training/](https://www.openphilanthropy.org/research/calibration-training/)) — adapted from Howard's Strategic Decisions Group; 80 questions per session. More rigorous than Clearer Thinking, less gamified.
- **Metaculus** ([https://www.metaculus.com/help/scoring/](https://www.metaculus.com/help/scoring/)) — Baseline + Peer scores; both are log-score-based variants. Use for *forecasting* a real-world event of interest to jarvis (e.g., "will milestone N close by 2026-07-01?"); pull personal track record monthly.
- **Manifold Markets** ([https://manifold.markets/calibration](https://manifold.markets/calibration)) — free play-money, real-time calibration plot per trader. Good for high-volume practice.
- **Python**: `sklearn.calibration.CalibratedClassifierCV`, `sklearn.metrics.brier_score_loss`. For reliability diagrams: `netcal` ([https://github.com/EFS-OpenSource/calibration-framework](https://github.com/EFS-OpenSource/calibration-framework)) — ECE, MCE, ACE, MMCE + plots; pip-installable.

---

## Q10. What the 250-decision audit tells us — and what's next

`decision_calibration_audit_2026_05_18_90d` (Bad/Normal/Good labels on ~250 decisions across memory/skills/arch/infra/process) is a **labeled retrospective**, not a calibration measurement. The labels are owner-judgment after-the-fact; they're prior-art for the rubric, not the rubric output.

Use them to:

1. **Define "Good"/"Bad" operationally** — what fraction of Bad-labeled decisions had `len(alternatives_considered) < 2`? `len(memories_used) == 0`? `confidence > 0.8` (overconfidence on Bad)? These cross-tabulations seed the DQ rubric thresholds.
2. **Calibrate the LLM judge** — same 250 are the gold set for κ measurement. Judge labels them Good/Bad; compare; iterate prompt until κ ≥ 0.6.
3. **Seed the reference-class index** — embed `decision` + `alternatives_considered` per row, store as vectors. The counterfactual-lite lookup (§Q5) draws from this index.
4. **Do NOT** treat as a Brier sample. Without forward-linked outcomes, you can compute label-agreement, not calibration.

---

## PROPOSALS [B4-N]

Numbered, prioritized, one-liner each. **B4** prefix matches the workflow-proposals table row.

| # | Title | Priority | One-liner |
|---|---|---|---|
| **B4-1** | Add `success_criteria` field to `record_decision` | **P0** | Falsifiable predicate written at decision time; the missing ingredient for honest Brier. Schema add + tools_schema.py + handler arg + payload. |
| **B4-2** | Add `task_outcomes.decision_episode_id` FK | **P0** | Forward linkage decision → outcome. One column, one migration. Unblocks every Brier query. |
| **B4-3** | Build `decision_calibration_summary` RPC | **P1** | Mirror of `fok_calibration_summary` (memory-fok.md §D6). Returns `{n, brier, reliability, resolution, uncertainty, calibration_curve, by_task_type, by_reversibility, worst_cases, pre_reg_ratio, drift_signal}`. |
| **B4-4** | Write `/decision-audit` skill (weekly cadence) | **P1** | Reads RPC, surfaces calibration curve, top-5 worst decisions, regret estimates, missed-decision audit. Runs as scheduled task. |
| **B4-5** | Process-Quality Score 0-6 on every `record_decision` | **P1** | Six binary checks (frame, alternatives, info, values, reasoning, commitment); hook-emitted; stored in `episodes.payload.dq_score`. Independent of outcome timing. |
| **B4-6** | Counterfactual-lite via reference-class lookup | **P2** | Embed `decision + alternatives_considered`; for worst-5 decisions per audit, sample 5 nearest reference-class peers; compute regret Δ. No causal claim. |
| **B4-7** | Missed-decision audit (sampling-bias channel) | **P2** | Weekly `/reflect` sub-task reads N=10 random sessions, flags exchanges that should've been `record_decision`. Trend `missed_count / total_sessions`. |
| **B4-8** | LLM-as-judge for reasoning-quality subscale | **P2** | Haiku-tier judge re-reads each new decision; binary `granularity / aom / reference_class / calibration_awareness`. κ ≥ 0.6 against owner labels on 5/week. |
| **B4-9** | Clearer Thinking weekly drill for owner | **P3** | 10-question batch every Friday; track owner Brier separately from jarvis Brier. Two-channel calibration. |
| **B4-10** | Calibration Brier dashboard (Friday surface) | **P3** | Rolling 60-day Brier + Reliability + Resolution as a one-screen surface in `/status`. Trend, not point. |

---

## Don't-do list

1. **Don't trend weekly Brier.** N=10 CI ≈ ±0.20. Anything you "see" weekly is noise. 60-day rolling minimum.
2. **Don't report naked Brier.** Decompose. A 0.20 Brier with 0.18 uncertainty is fine; a 0.20 Brier with 0.05 uncertainty is awful.
3. **Don't try real counterfactuals (Pearl rung 3).** No replication, no IVs, no chance. Reference-class is the ceiling.
4. **Don't conflate DQ score with Brier.** DQ measures process (available immediately, independent of outcome). Brier measures calibration (lags by weeks). Both ship.
5. **Don't auto-tune confidence based on Brier feedback.** Goodhart. Surface the gap; owner decides whether to revise prompt, change model, change rubric.
6. **Don't drop the labeled 250-audit into Brier math.** It's a labeled retrospective, not paired forecasts. Use it for κ calibration of the judge, not as a sample.

---

## Sources (with primacy notation)

**Primary (load-bearing):**

- Brier 1950, *Verification of forecasts expressed in terms of probability*, Monthly Weather Review 78(1) — score definition.
- Murphy 1973, *A new vector partition of the probability score*, J. Appl. Meteorology 12(4) — reliability/resolution/uncertainty decomposition.
- Annie Duke, *Thinking in Bets* (Portfolio 2018) — outcome decoupling, resulting fallacy.
- Tetlock & Gardner, *Superforecasting* (Crown 2015); Mellers et al. 2014 *Psychological Science* 25(5), 2015 *Persp. Psych. Sci.* 10(3) — Superforecaster traits, GJP findings.
- Howard & Abbas, *Foundations of Decision Analysis* (Pearson 2015) + Decision Quality Society — six-element DQ framework.

**Forecasting platforms / tools:**

- Metaculus scoring docs — [https://www.metaculus.com/help/scoring/](https://www.metaculus.com/help/scoring/)
- Manifold calibration page — [https://manifold.markets/calibration](https://manifold.markets/calibration)
- Clearer Thinking Calibrate Your Judgment — [https://programs.clearerthinking.org/calibrate_your_judgment.html](https://programs.clearerthinking.org/calibrate_your_judgment.html)
- Open Philanthropy calibration training — [https://www.openphilanthropy.org/research/calibration-training/](https://www.openphilanthropy.org/research/calibration-training/)
- `netcal` (Python ECE/MCE/reliability diagrams) — [https://github.com/EFS-OpenSource/calibration-framework](https://github.com/EFS-OpenSource/calibration-framework)

**Anthropic / eval frontier:**

- Anthropic, *A statistical approach to model evaluations* (2024) — paired CI, clustered SEs.
- Anthropic Engineering, *Demystifying evals for AI agents* (2025) — judge design.
- Anthropic, *On the biology of a large language model* (2025) — interpretability frontier (referenced, not load-bearing here).

**Counterfactual / reference class:**

- Kahneman & Tversky 1979, *Intuitive prediction: biases and corrective procedures* — outside view.
- Flyvbjerg 2006, *From Nobel Prize to project management: getting risks right* (Project Mgmt J 37(3)) — reference-class forecasting.
- Pearl 2009, *Causality* (CUP) — counterfactual ladder (boundary reference for what we're NOT doing).

**Inter-rater agreement (for the judge κ check):**

- Cohen 1960, *A coefficient of agreement for nominal scales* (Educ. Psych. Measurement).
- Landis & Koch 1977 — κ interpretation thresholds.

---

## Sizing

~470 lines. Designed to support a `/decision-audit` skill spec + a weekly cron without further research. Schema additions (`success_criteria`, `decision_episode_id`) gate B4-1 and B4-2; everything else builds on those two.
