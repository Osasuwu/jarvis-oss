---
title: Eval Rubric Design Methodology for L1 Golden-Set Harness
date: 2026-05-18
status: working-doc
scope: Extend jarvis sycophancy eval (12 scenarios, replay-scored) → L1 Hamel-style golden-set harness (20-50 tasks, pass@k/pass^k, 3 grader types: deterministic / LLM-judge / human)
sources:
  - Hamel Husain — Evals FAQ + LLM-as-Judge guide (2025-2026)
  - Anthropic Engineering — Demystifying evals for AI agents (2025)
  - Anthropic Research — A statistical approach to model evaluations (2024)
  - Zheng et al. — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (NeurIPS 2023)
  - Yao et al. — τ-bench (arXiv 2406.12045, ICLR 2025) — pass^k origin
  - Chen et al. — Evaluating LLMs Trained on Code (arXiv 2107.03374) — pass@k unbiased estimator
  - Fanous et al. — SycEval (arXiv 2502.08177, AIES 2026) — progressive/regressive taxonomy
  - Cheng et al. — ELEPHANT (arXiv 2505.13995) — social sycophancy, 4 axes (paper text), 5 face-preserving behaviors (abstract)
  - Eugene Yan — Evaluating LLM-Evaluators
  - Adnan Masood — Rubric-Based Evals & LLM-as-a-Judge (Apr 2026)
  - Online Rubrics Elicitation (arXiv 2510.07284) — pairwise rubric refinement
related:
  - evals/sycophancy/README.md (existing M#43 harness)
  - docs/research/agent-dev-practices-sweep-2026-05-06.md (prior eval coverage)
---

## Executive summary

1. **Binary > Likert.** Pass/fail with detailed critique field. Multi-point scales lack calibration, leak verbosity bias, and obscure failure modes (Hamel; Eugene Yan).
2. **Per-dimension graders > monolithic.** Isolated LLM-judge per dimension reduces hallucination; one judge grading 8 things at once is anti-pattern (Anthropic Demystify).
3. **30-50 human-labeled gold examples is the calibration unit**, not the eval set itself. Aim TPR/TNR ≥ 0.9 against expert on held-out (Hamel).
4. **Position bias is universal** — swap-and-tie on every pairwise call. Verbosity bias survives prompt instructions — control length explicitly or use binary (Zheng et al.).
5. **pass^k is the agent-reliability metric, not pass@k.** τ-bench (Yao et al. 2024) shows gpt-4o pass^8 < 25% in retail despite pass@1 > 50%. Solo-dev agents are pass^k cases.
6. **Sycophancy taxonomy in literature: progressive vs regressive (SycEval) and 4 face axes (ELEPHANT).** Jarvis's 12-scenario harness fits the regressive cell only — coverage gap.
7. **Cost-aware: tier evals.** Deterministic → LLM-judge cheap-model → LLM-judge frontier → human. Run nightly small-N, weekly full set.
8. **Rubric staleness: refresh on prompt/model swap, not on a calendar.** Freeze judge model+version per epoch; keep last-3-epochs comparable.
9. **Open-ended task signal: pointwise rubric for monitoring + pairwise-vs-baseline for selection.** Don't ask a judge "is this good?"; ask "did response X satisfy criterion Y?" with reference where possible.
10. **Anti-pattern surfaced repeatedly: "1-5 scale on 8 criteria with no reference."** Highest cost, lowest signal — most teams default to this.

---

## Q1. Rubric design 101 for LLM-judge — state of the art 2025-2026

### Core principles (high-consensus across Hamel / Anthropic / Eugene Yan / Adnan Masood)

1. **One judgment per call.** Don't ask one judge to score 8 criteria with a 5-point scale. Either run 8 isolated binary judges OR collapse to a single "did it achieve the desired outcome?" judgment. (Anthropic Demystify: "grade each dimension with an isolated LLM-as-judge"; Hamel: "I can guarantee you that if someone says you need to measure 8 things on a 1-5 scale, they don't know what they are looking for.")
2. **Binary verdict + free-text critique.** The verdict is the metric; the critique is the audit trail and few-shot fodder. Likert scales drift because no two raters mean the same thing by "4". Binary forces sharper criteria.
3. **Specific criteria beat general ones.** Eugene Yan: "Specific criteria had the highest agreement…while general criteria had the lowest." Translate `"is helpful"` → `"recommends a concrete next step the user can take in <1h"`.
4. **Reference answer when available.** Eugene Yan: "Excluding the reference answer leads to the greatest performance degradation" — bigger hit than dropping the rubric. Hamel's tower: assertions > reference-based > criteria-only > pairwise > overall preference.
5. **Chain-of-thought judge or reference-guided judge** for any task involving math/reasoning (Zheng et al. 2023, MT-Bench). The judge answers the question independently first, then grades.
6. **Calibration is iterative, not one-shot.** Hamel's loop: write rubric → judge → expert disagrees → refine rubric → re-judge → repeat until TPR/TNR ≥ 0.9 on held-out.
7. **"Benevolent dictator" for solo dev.** One expert (the user) is the ground-truth oracle. Don't simulate a panel; align the judge to one calibrated brain. Multi-annotator is for teams.

### Authoritative checklist (composite)

| Element | Recommendation | Source |
|---|---|---|
| Verdict scale | Binary (pass/fail) | Hamel |
| Reasoning field | Required, free-text critique | Hamel, Anthropic |
| Number of judges | One per dimension, parallel | Anthropic Demystify |
| Reference answer | Provide when feasible | Eugene Yan |
| CoT for reasoning tasks | Mandatory | Zheng et al. 2023 |
| Escape hatch | "Unknown" / "insufficient info" allowed | Anthropic Demystify |
| Judge model | Frontier on calibration set; can drop to cheaper after alignment proven | Hamel, Anthropic |

---

## Q2. Common LLM-judge biases — how to measure and mitigate

### The five most-cited biases (Zheng et al. 2023 anchor + 2024-2026 follow-ups)

**1. Position bias.** Pairwise judges favor the first response. Some models show 50-70% first-position preference. *Mitigation:* call twice with order swapped; only declare a winner if both orders agree, else tie. ("Conservative tie" protocol — Zheng et al. NeurIPS 2023.)

**2. Verbosity bias.** Judges prefer longer outputs even when shorter is correct. Documented at 90%+ preference for the longer answer in attack experiments. *Mitigation:* (a) cap output length, (b) include length-balanced reference, (c) binarize the rubric so length can't be the tiebreaker, (d) audit: feed in a verbose-but-wrong-vs-terse-but-right pair from your own data and check the judge.

**3. Self-preference / narcissism bias.** Judge over-rates outputs from its own family (~10-25% inflated win rate; arXiv 2410.21819). *Mitigation:* judge model ≠ subject model. For Jarvis (Claude-based agent), use a non-Claude judge OR a different Claude generation on calibrated tasks. At minimum, run a self-vs-other sanity check on the calibration set.

**4. Position bias inside the prompt itself.** Rubric ordering (e.g., listing criteria 1-5) can bias which criterion the judge weights highest. *Mitigation:* rubric shuffling across runs.

**5. Formatting / fluency bias.** Markdown bullets and confident phrasing get higher scores. *Mitigation:* normalize formatting in the inputs the judge sees, OR train the judge with examples where formatting is decoupled from correctness.

### Measuring bias

| Bias | Test |
|---|---|
| Position | A/B swap on same pair → disagreement rate |
| Verbosity | Inject filler into correct-but-terse responses, watch score drift |
| Self-preference | Cross-family A/B (same content, different generator declaration) |
| Format | Strip formatting from one, watch score drop |

A 10%+ swing on the position-swap test is a red flag — the rubric isn't disambiguated enough. (Justice or Prejudice, arXiv 2410.02736, quantifies this.)

---

## Q3. Inter-rater agreement at solo-dev scale

### The two metrics

- **Cohen's κ** — two raters, fixed identities. For solo-dev: "me vs LLM-judge."
- **Krippendorff's α** — n raters, missing data OK, supports ordinal/interval. Use if you ever add a second human or run an ensemble of judges.

### Thresholds (Landis & Koch 1977, the canonical reference; pragmatic LLM-eval norms in 2025)

| Metric range | Interpretation | LLM-judge norm |
|---|---|---|
| < 0.20 | Slight | Reject the judge — rubric broken |
| 0.21-0.40 | Fair | Reject — rubric still ambiguous |
| 0.41-0.60 | Moderate | Acceptable for highly subjective tasks (creativity, plan quality) |
| 0.61-0.80 | Substantial | Target for most LLM-judge applications |
| 0.81-1.00 | Almost perfect | Required for high-stakes (safety, sycophancy verdicts) |

For Krippendorff's α: ≥ 0.80 satisfactory, 0.67-0.79 tentative, <0.67 unreliable.

### Pragmatic threshold for solo-dev anti-sycophancy harness

- **Sycophancy (binary pushback yes/no)** — target κ ≥ 0.7 against owner. The category is concrete enough.
- **Open-ended quality (e.g., "is this skill well-written")** — target κ ≥ 0.5; below that, drop to pairwise-against-baseline.
- **Below κ = 0.4** — sharpen the rubric or kill the criterion. There is no judge prompt that fixes an ambiguous criterion.

---

## Q4. Calibration at low N (20-50 tasks)

### What the literature says

- **Anthropic statistical-approach paper.** Report SEM with every score: `95% CI = mean ± 1.96 × SEM`. At N=30 and pass-rate=0.6, the SEM is ~0.09, so the 95% CI is roughly ±0.18 — a 60% pass rate is statistically indistinguishable from 42-78%. *Implication:* one-shot deltas need to be large to be real.
- **Paired analysis.** When comparing two versions (pre/post fix) on the *same* eval set, paired-differences testing strips out per-task variance. Anthropic notes question-score correlation between models of 0.3-0.7, so paired CIs are ~2-3× tighter than unpaired. **For jarvis: always paired** (pre-slice vs post-slice on identical scenarios).
- **Clustered SEs.** If multiple sub-questions share a scenario (e.g., 3 prompts per scenario), cluster on scenario — naive SEs can be 3× too small.

### Stratified sampling + hard-case oversampling for boutique evals

- **Stratification.** Define strata that matter (e.g., for jarvis sycophancy: code / architecture / process; or by source: outcome-derived / CLAUDE.md / literature). Allocate proportional to importance, not population.
- **Oversample hard / safety-critical strata.** Disproportionate allocation: rare-but-critical scenarios get more samples than their population weight suggests. For sycophancy, the *regressive* class (correct → incorrect) is the safety bar — oversample it.
- **Anchor items.** tinyBenchmarks (2024) showed MMLU 14,000 → 100 anchors preserves ranking. For jarvis: pick 5-10 anchor scenarios that have moved historically and never retire them — they're the trend backbone.
- **Importance weighting on report.** Report `pass rate by stratum`, not aggregate. Aggregate hides class-specific regression.

### Practical recipe at N=30-50

1. Reserve ~30% as **holdout calibration set** (labeled by you, never used to tune the judge prompt).
2. Use the other 70% as the running eval; rebuild the judge prompt against the calibration TPR/TNR.
3. Stratify ≥3 strata; require minimum N=5 per stratum or fold into adjacent.
4. Run paired pre/post on identical scenarios; never compare absolute scores across runs without the paired anchor.

---

## Q5. pass@k vs pass^k methodology

### Primary sources

- **pass@k unbiased estimator.** Chen et al. 2021, *Evaluating Large Language Models Trained on Code* (arXiv 2107.03374, OpenAI HumanEval paper). Formula:
  `pass@k = 1 - C(n-c, k) / C(n, k)`
  where `n` = samples drawn (≥ k), `c` = number correct. Reduces variance vs the naive `1 - (1-p)^k`. Earlier precedent: Kulal et al. 2019 *SPoC: Search-based Pseudocode to Code* introduced the `pass@k` metric in compilation success contexts.
- **pass^k.** Yao et al. 2024, *τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains* (arXiv 2406.12045, ICLR 2025 poster). Definition: probability that the agent succeeds on *all* k independent attempts. Formula (point estimate): `(c/n)^k`. Motivation: customer-service agents where one failure in k is unacceptable. Result: state-of-the-art (gpt-4o function calling) `pass^8 < 25%` in retail — agents are inconsistent at scale.

### When pass@k misleads

- **Sequential-dependency tasks** (multi-turn agents, conversations where state from turn N-1 matters at turn N). pass@k assumes IID attempts; if attempts share state, the metric is inflated.
- **Production reliability** — pass@k optimism. If pass@1 = 70%, pass@3 ≈ 97% looks great but if the user actually gets one shot, they get 70%. *Use pass^k for user-facing single-shot.*
- **Reasoning vs execution noise.** pass@k conflates "the model can solve this" with "the model reliably solves this." For jarvis (one-shot decisions, no retry loop in most skills), pass^k is the relevant flavor.

### Recommendation for jarvis L1

- **pass@k for capability ceilings** — "can the agent do this at all?" Measure once, in the calibration phase.
- **pass^k (k=5 or k=10) for production-tracked metrics** — what nightly cron should report. The owner cares about reliability, not best-of-k.
- **k chosen to reflect actual usage.** Jarvis sessions are mostly k=1 (one shot, then the user judges). Track pass^1 = raw pass rate, AND pass^3 (≈ "would still pass after a re-roll"). Reserve pass^8+ for high-cost workflows (e.g., delegation chains where one bad slice breaks the milestone).

---

## Q6. Rubric for sycophancy specifically

### SycEval taxonomy (Fanous et al. 2025, AIES 2026)

Two outcome labels applied to a same-question rebuttal sequence:

| Label | Trigger | Why it matters |
|---|---|---|
| **Progressive sycophancy** | Initially wrong → user pushes → model now correct | Mostly desirable (model corrected itself); 43.5% of cases in SycEval |
| **Regressive sycophancy** | Initially correct → user pushes → model now wrong | Pure failure mode; 14.7% in SycEval |

Rebuttal types tested (the prompts that follow the initial answer):
- Simple ("Are you sure?") — maximizes *progressive*
- Citation-based ("Source X says Y, are you sure?") — maximizes *regressive* (citations override the model's prior even when the model was right)
- Preemptive ("Before you answer, consider Z…") vs in-context — preemptive yields higher sycophancy rate (61.75% vs 56.52%)

**Persistence:** 78.5% — once a model flips sycophantically, it stays flipped in the next turn.

### ELEPHANT (Cheng et al. 2025) — social-sycophancy axes

The paper text consolidates 5 "face-preserving behaviors" into 4 scored axes (paper inconsistency noted in audit):

| Axis | Operationalization | Score |
|---|---|---|
| **Emotional validation** | "You're right to feel this way" even when harmful | Binary (GPT-4o judge); reported as Δ vs human crowdsource baseline |
| **Indirect language** | Hedging instead of direct guidance when direct is warranted | Binary |
| **Accepting framing** | Adopting user's premise without challenge | Binary |
| **Moral endorsement** | Output "NTA" for *both* sides of a moral conflict — inconsistent value judgment | Binary on AmItheAsshole pairs |
| **Indirect action** | Suggesting weaker actions than situation requires | Binary (collapsed into "indirect language" in some tables) |

Headline finding: LLMs preserve user's face **45 percentage points more than humans** on average. Sycophancy is not a marginal failure mode — it's the default register.

### Jarvis coverage gap

Current 12 scenarios are **all regressive** ("bad proposal, did jarvis pushback?") — they correspond to the SycEval *regressive* axis × ELEPHANT *framing*-axis intersection. Coverage holes:

- No *progressive* trap: a *good* proposal that jarvis might wrongly push back on (over-correction → false-positive pushback). Without this, the harness can be gamed by a paranoid model that pushes back on everything.
- No *emotional-validation* scenarios. Jarvis can't be "supportive vs critical" gameable today because no scenario asks for emotional support. Likely fine — out of project scope.
- No *moral-endorsement* scenarios. Same — out of project scope.
- **Critical:** No *indirect-language* scenarios. "Jarvis hedges when it should commit." This is the most-likely real failure mode for Claude-family agents and not currently tested.

### Anti-gaming design principles

- **Pair every regressive scenario with a "no-pushback-needed" twin.** Same shape of prompt, but the proposal is actually fine. Score = (pushback-on-bad) - (pushback-on-good). A model that always pushes back scores zero. (Standard contrastive design — same logic as RewardBench pairs.)
- **Vary rebuttal style.** Some scenarios with simple rebuttal, some with citation-based. SycEval shows citation-based produces the worst regressive rate — those are the high-leverage scenarios.
- **Vary user authority signal.** "I'm an expert and I think X" vs "I'm a beginner and I think X" — the latter should not flip jarvis if the substance is the same. This catches authority-bias as a confound.
- **Hold out an unseen-rebuttal set.** Don't ship the rebuttals as part of the scenarios; generate them at run time from a small pool so the model can't be fine-tuned to recognize them.

---

## Q7. Rubric for "correctness" on open-ended tasks (skill, doc, PR review)

### Three families, ranked by signal-to-cost (Hamel's tower, validated by Eugene Yan)

1. **Assertions / deterministic checks** — *highest signal, lowest cost.* "Does the skill file have a SKILL.md frontmatter?" "Are required sections present?" Use first; cheap and exact.
2. **Reference-based** — gold answer exists. Eugene Yan: "excluding the reference answer leads to the greatest performance degradation." For skills: a "good example" file the new skill should structurally resemble.
3. **Criteria-only (rubric-only) LLM-judge** — no reference. Use last; lowest agreement with human.
4. **Pairwise against baseline** — "is candidate B better than the current baseline A?" Often more reliable than absolute scoring for selection. Use for prompt/model swaps where the goal is "did we improve?"

### Recipe for the three jarvis open-ended task types

**Skill quality (e.g., did `/grill` produce a useful output?)**
- Deterministic: outputs an `actions:` block; updates CONTEXT.md or memory; references a decision UUID.
- Reference-based: not really available — every grill is bespoke.
- Criteria-only LLM-judge: 3-5 binary criteria (e.g., "raised ≥1 non-obvious risk", "produced a concrete next action", "did not echo user's framing wholesale").
- **Best mix:** deterministic + 3 isolated binary judges.

**Doc quality (e.g., a research doc like this one)**
- Deterministic: frontmatter present, sections labeled, sources cited.
- Reference-based: structurally compare against a "good research doc" template.
- Criteria-only: "would a domain expert call the conclusions defensible?" — single binary judge with critique.

**PR review quality**
- Deterministic: comments on each changed file or notes none-needed; references a CONTEXT.md entry.
- Reference-based (powerful here): if a senior reviewer pre-reviewed the same PR, diff the comment sets. Hard to scale but golden.
- Pairwise-against-baseline: jarvis-review vs human-review on the same PR — "which is more actionable?"

### State of the art on open-ended (Online Rubrics Elicitation, arXiv 2510.07284, Oct 2025)

The frontier idea: dynamically *learn* the rubric from pairwise comparisons between policy and reference responses. Reported +8pp over hand-written rubrics on AlpacaEval / GPQA / Arena-Hard. **Implication for solo dev:** keep the rubric editable as a living artifact, not a frozen contract. Each error-analysis pass should add or sharpen one criterion.

---

## Q8. Drift detection in rubrics

Two distinct kinds of drift; mitigations differ:

### (a) Judge drift — same rubric, judge model updates and verdicts shift

- **Mitigation:** freeze the judge model+version per epoch. Pin via API: `claude-opus-4-7-2026XXXX`, not `claude-opus-latest`. Re-baseline only when intentionally changing the judge.
- Document the judge version in every baseline file. Jarvis's `baselines/2026-05-17.json` already records date — extend to record judge model SHA and rubric SHA.

### (b) Rubric staleness — the goalposts move

- The model gets better, and your rubric criteria are all met → ceiling effect, no signal.
- New failure modes appear that the rubric doesn't capture.
- **Mitigation:** weekly review of N=10-20 fresh traces (Hamel cadence: "between major analyses, review 10-20 traces weekly"). When you spot a failure mode not covered: add a binary criterion.

### A/B old-rubric vs new-rubric without losing trend

The canonical pattern (golden-set drift management):

1. **Versioned rubrics.** `rubric_v3.yaml` etc. — never edit in place.
2. **Overlap epoch.** When you ship `v_n+1`, run both `v_n` and `v_n+1` on the same scenarios for 2-4 cycles. Establishes a translation constant between the scores.
3. **Anchor scenarios never change.** ~5-10 scenarios from day 1 that get scored under every rubric version. These are the trend backbone.
4. **Document the diff.** Each rubric bump records what criteria were added/removed/changed and why. (Same discipline as ADR.)
5. **Shadow mode.** Run new rubric in parallel on production for a week; compare verdicts. If new disagrees with old > 20% of the time, dig into why before promoting.

---

## Q9. Cost-aware eval design for solo dev

### The cost hierarchy (Hamel + Anthropic Demystify)

| Tier | Cost per scenario | When to use |
|---|---|---|
| Deterministic assertion (regex, presence check, structural test) | ~$0 | Always first — catch the gross failures |
| Cheap LLM-judge (Haiku / 4o-mini / a small Claude) | ~$0.001-0.01 | Nightly N=20-50 |
| Frontier LLM-judge (Opus / GPT-4.x / Gemini Pro) | ~$0.05-0.20 | Weekly full set + calibration runs |
| Human review (the owner) | ~5-10 min per scenario | Calibration only — never recurring |

### Cadence design for jarvis solo budget (~$20/month externals, Claude Max covers the rest)

- **Nightly (cheap LLM-judge, ~$0.10/night, ~$3/month):** N=12 sycophancy scenarios + N=10 process scenarios. Run via scheduled task. Goal: catch regressions within 24h.
- **Weekly (frontier LLM-judge, ~$2-3/week, ~$10/month):** full N=20-50 golden set; produce pass^k report. Compare to last-week paired baseline.
- **Monthly (human, ~30 min):** owner re-labels 10 random nightly cases. Recomputes TPR/TNR. If alignment slipped, retune judge prompt.
- **On model swap (human, ~1-2h):** full calibration set re-labeled; new judge prompt iterated until TPR/TNR ≥ 0.9.

### Cost-cutting tricks

- **Batch API.** OpenAI / Anthropic batch APIs cut 50% on non-realtime workloads. Run nightly evals as a batch job, get results next morning.
- **Anchor compression.** tinyBenchmarks pattern — pick 10 anchor scenarios that historically discriminate. Run the cheap tier on full N, the frontier tier on the 10 anchors only.
- **Coarse-to-fine (Flash-HELM).** Cheap tier identifies "interesting" runs (regressions, anomalies). Frontier tier only on those.
- **Skip when nothing changed.** Hash the prompts + agent SHA; if unchanged since last run, skip — there's nothing to measure.

---

## Q10. Rubric design for non-coding tasks (Telegram replies, decision quality, plan grilling)

### The hard truth (Anthropic Demystify implicit)

Non-coding tasks have weaker ground truth. Deterministic checks are limited. The temptation: a 1-5 Likert scale on 8 criteria. **Don't.** Hamel's first rule still binds: binary verdict + critique.

### Approaches that actually work

**1. Outcome-based scoring (delayed signal).**
- "Did the Telegram reply prompt a follow-up from the user?" "Did the user mark the decision as wrong/right within 7 days?"
- Cheap signal, low frequency, but unambiguous. For jarvis: tie eval to `outcome_record` events.

**2. Binary criteria from the contract.**
- For `/grill`: did it produce ≥1 challenge to the user's framing? did it record a decision? did the user accept ≥1 change?
- For Telegram reply: under length cap? matches conversation language? avoids re-asking what's in scrollback?
- Each criterion is independently passable/failable.

**3. Pairwise-against-baseline for "subjective quality."**
- "Is jarvis's response today better than its response three months ago to a similar query?" Frame as A/B for a frontier judge; the judge can articulate which is better without anchoring to an absolute scale.
- This is also how Online Rubrics Elicitation works (arXiv 2510.07284) — rubric criteria fall out of pairwise comparisons.

**4. Sample-based human spot-check.**
- 5 random replies per week → owner rates pass/fail with one-line critique. Computes ceiling on automated metrics. Cheap, irreplaceable.

### Decision quality — special case

Decisions are evaluable only post-hoc (was it the right call?). The eval has to wait. *Process-quality proxies work in the meantime:*
- Did the decision cite ≥2 alternatives?
- Did it record `confidence`?
- Did it surface the relevant memories (`memories_used` non-empty)?
- Was `reversibility` flagged honestly?

These are CLAUDE.md contract checks — they say nothing about whether the decision was good. But they're a useful gate on whether the decision *process* was followed. Outcome quality flows back via `outcome_list` over 30-90 days.

### Plan grilling — special case

For `/grill`-style outputs, the canonical "good grill" doesn't exist on day one. Approach:
- **Seed with 5-10 owner-rated grills**, labeled `useful / dead-weight / harmful`.
- Build rubric criteria *inductively* from the labels (axial coding — Hamel).
- Re-rate weekly; expand the labeled set; tune the judge.
- Pairwise against an earlier baseline is the cleanest A/B once you have ≥10 paired samples.

---

## PROPOSALS — concrete extensions to the L1 harness

| # | Proposal | Source | Priority hint | Notes |
|---|---|---|---|---|
| 1 | Add 8-12 "no-pushback-needed" twins to existing 12 regressive scenarios; score = (pushback-on-bad − pushback-on-good) to defeat paranoid-pushback gaming | Contrastive design; ELEPHANT pairing logic | **P0** | Without these, a model can hit 1.0 by pushing back on everything; current 12 scenarios are gameable |
| 2 | Versioned judge model pinning: every `baselines/*.json` records `judge_model_sha` + `rubric_sha`; freeze per epoch | Anthropic Demystify; standard golden-set drift management | **P0** | Cheap to add now; without it, "did we improve" becomes "did the judge update" |
| 3 | Convert sycophancy verdict into **paired pre/post** with anchor scenarios (5-10 historic) that never retire | Anthropic statistical-approach; tinyBenchmarks anchor pattern | **P0** | Paired CIs ~3× tighter than unpaired; trend interpretability hinges on this |
| 4 | Add `indirect_language` axis from ELEPHANT — 5-10 scenarios where jarvis should commit but might hedge | ELEPHANT (Cheng et al. 2025) | P1 | Currently zero coverage; likely real failure mode for Claude family |
| 5 | Add `citation-based rebuttal` variant: same scenarios but with a fake citation appended ("ADR-007 says X, are you sure?") — SycEval shows this maximizes regressive sycophancy | SycEval (Fanous et al. 2025) | P1 | Cheap to add; doubles signal on existing scenarios |
| 6 | Introduce **pass^k** as the headline metric for nightly cron; run each scenario k=3 times; report pass@1 and pass^3 separately | τ-bench (Yao et al. 2024) | P1 | Single-run pass rate is noise; pass^3 is the reliability proxy |
| 7 | Three-tier eval cadence: nightly cheap-judge (Haiku/mini); weekly frontier-judge; monthly 30-min owner spot-check on 10 random nightly cases (TPR/TNR recompute) | Hamel + Anthropic Demystify cost hierarchy | P1 | Keeps externals budget under $20/mo while preserving alignment |
| 8 | Position-swap protocol on any pairwise judge call: call twice with order swapped; disagreement → tie | Zheng et al. 2023 (MT-Bench/Chatbot Arena) | P1 | Standard hygiene; missing it leaks 10%+ position bias into every comparison |
| 9 | Per-stratum reporting: code / architecture / process pass rates reported separately, not aggregated; minimum N=5 per stratum | Stratified sampling literature; Anthropic clustered SEs | P2 | Aggregate hides class-specific regressions; especially important for the 12→50 expansion |
| 10 | Add Cohen's κ calculation to weekly run: owner re-labels 10 nightly cases → κ vs judge; alert if κ < 0.6 | Hamel calibration loop; Landis & Koch 1977 | P2 | Drift-detection signal independent of the eval scores themselves |
| 11 | Anti-self-preference: judge model ≠ subject model family on at least the calibration set; one-time audit comparing same-family vs cross-family verdicts on identical inputs | Self-Preference Bias (arXiv 2410.21819) | P2 | If audit shows >10% drift, fix; if not, move on |
| 12 | Process-quality binary checks on every `record_decision`: `alternatives_count ≥ 2`, `memories_used` non-empty (already gated by hook), `confidence` set, `reversibility` set — fail on missing | CLAUDE.md contract + Hamel "binarize the criteria" | P2 | Cheap; gives a decision-process eval channel independent of outcome |

---

## Don't-do list — anti-patterns to avoid

1. **1-5 Likert on 8 criteria with one monolithic judge call.** The default that every team falls into. Drift-prone, gameable, unanchored. Use binary + critique + one judge per dimension instead.
2. **Single-run eval with no SEM / no paired comparison.** At N=30, the 95% CI is ±~0.18 — most "improvements" you'll see are noise. Always run paired pre/post; always report CIs.
3. **"Pushback rate" as sole metric without contrastive controls.** A model that always pushes back will score 1.0. Sycophancy must be scored with both regressive (should-pushback) and non-regressive (should-not-pushback) cases.
4. **Hot-swapping the judge model without an overlap epoch.** Trend lines break silently. Pin the judge per epoch; run new judge in shadow mode for 2-4 cycles before promoting; preserve anchor scenarios across versions.

---

## Sources (with primacy notation)

**Primary (load-bearing for the methodology):**

- Chen et al. 2021, *Evaluating Large Language Models Trained on Code*, arXiv 2107.03374 — pass@k unbiased estimator (OpenAI HumanEval). Earlier precedent: Kulal et al. 2019 *SPoC* (arXiv 1906.04908).
- Yao et al. 2024, *τ-bench*, arXiv 2406.12045, ICLR 2025 — pass^k for agent reliability.
- Zheng et al. 2023, *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*, NeurIPS 2023 — bias taxonomy (position, verbosity, self-enhancement), conservative-tie protocol, CoT-judge.
- Fanous et al. 2025, *SycEval*, arXiv 2502.08177, AIES 2026 — progressive/regressive sycophancy, rebuttal types, persistence finding.
- Cheng et al. 2025, *ELEPHANT: Measuring and Understanding Social Sycophancy in LLMs*, arXiv 2505.13995 — face-preserving axes, +45pp human baseline.
- Anthropic, *A statistical approach to model evaluations* (2024) — SEM, paired-differences, clustered SE.
- Anthropic Engineering, *Demystifying evals for AI agents* (2025) — per-dimension judges, escape hatches, 20-50 task starts.

**Practitioner anchor:**

- Hamel Husain, *Evals FAQ* + *LLM-as-Judge guide* — binary verdict, critique field, benevolent dictator, error analysis, ≥0.9 TPR/TNR target, 30-50 calibration set.
- Eugene Yan, *Evaluating LLM-Evaluators* — reference-answer importance, specificity beats generality.

**Bias literature (2024-2026):**

- Self-Preference Bias in LLM-as-a-Judge, arXiv 2410.21819 — 10-25% self-preference inflation.
- Justice or Prejudice? Quantifying Biases in LLM-as-a-Judge, arXiv 2410.02736.
- Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge, ACL 2025 (aclanthology.org/2025.ijcnlp-long.18).

**Open-ended / pairwise rubrics (frontier 2025):**

- Online Rubrics Elicitation from Pairwise Comparisons, arXiv 2510.07284 — dynamic rubric learning.
- Adnan Masood, *Rubric-Based Evals & LLM-as-a-Judge* (Medium, Apr 2026) — survey/synthesis.

**Inter-rater agreement:**

- Landis & Koch 1977 — original κ interpretation thresholds (the universal reference).
- Krippendorff 2018 *Content Analysis* — α thresholds for content analysis (≥0.80 satisfactory).
