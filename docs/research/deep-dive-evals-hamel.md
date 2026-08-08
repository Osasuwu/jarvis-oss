# Deep dive: Hamel-style evals for Jarvis's skill catalog

Date: 2026-05-06
Author: research subagent (Opus 4.7)
Parent sweep: [`agent-dev-practices-sweep-2026-05-06.md`](agent-dev-practices-sweep-2026-05-06.md) §5.1, §5.5, §5.6
Memory: `research_agent_dev_practices_sweep_2026_05_06`

> **Sourcing note (read first).** WebFetch and Firecrawl were sandbox-denied
> in the research session, so the Hamel canon below is reconstructed from
> prior reading of the four canonical articles plus the parent sweep's
> distilled tags. Direct quotes are quoted from memory and tagged
> `[paraphrase]` where wording is approximate; URLs are kept so the owner
> can verify any specific claim before it becomes load-bearing. Confidence
> tags account for this.

## 0. TL;DR (one screen)

1. **Start with manual error analysis on 20–50 real `/implement` traces — do not build an eval harness first.** This is the Hamel move that 90% of teams skip and the parent sweep flagged as the highest-leverage gap.
2. **Pick `/implement` as the first instrumented skill.** Cleanest input/output (issue → PR diff), highest blast radius, regression-from-failure loop already plumbed via `outcome_record`.
3. **Use binary pass/fail rubrics, never Likert.** Per-trace judgment is ternary at most: `pass / fail / off-policy`.
4. **Storage: flat JSONL of trace + label in `evals/` of this repo, judge prompts in `evals/judges/`, runner is a small Python module under `scripts/eval/`. No external SaaS yet.** Braintrust is the right escape valve when (and only when) we have >200 cases and >2 graders.
5. **Process metrics matter as much as E2E.** A `/delegate` run can produce a green PR while violating the protected-files rule, the worktree-isolation cap, or the grill-me checkbox. Eval each gate as its own binary, not one outcome variable.
6. **Confidence on the recommendations:** medium-high (4/5) on bootstrap path and skill choice; medium (3/5) on judge-prompt designs (untested); medium (3/5) on the Braintrust threshold (educated guess from sweep §5.3, not personal benchmarking).

---

## 1. Hamel's framework, distilled

Sources (canonical, per parent sweep §"Sources"):

- `https://hamel.dev/blog/posts/evals-faq/` — FAQ, the densest single artefact.
- `https://hamel.dev/blog/posts/evals/` — long-form philosophy, level 1/2/3, data flywheel.
- `https://hamel.dev/blog/posts/llm-judge/` — full LLM-as-Judge methodology + alignment.
- `https://hamelhusain.substack.com/p/evals-skills-for-coding-agents` — the recent (spring 2026) post that maps the framework specifically to slash-command-style coding agents and is the direct trigger for this dive.
- `https://newsletter.pragmaticengineer.com/p/evals` — applied case studies, useful as ammo against "we don't need this yet".

The five claims we are stealing wholesale:

### 1.1 Error analysis comes first, infrastructure last

`[paraphrase]` "If you can't sit down for thirty minutes and read twenty to fifty
of your model's outputs, you do not have an eval problem yet — you have a
*looking at your data* problem." Hamel returns to this in every artefact: the
modal failure of teams adopting evals is to buy a vendor (Braintrust /
LangSmith / Langfuse) and build dashboards before they have ever opened a
spreadsheet of real traces. The dashboards then measure noise.

The procedure is:
1. Sample 20–50 real production traces (random or stratified, not cherry-picked).
2. Open each one. Assign a binary label: pass / fail.
3. **For every fail, write a free-text reason in your own words.** Do not pre-pick error categories.
4. After the sample, cluster the free-text reasons into 5–15 categories. *These are your eval categories.* They almost never match what you would have written a-priori.
5. Now — and only now — write rubrics, judge prompts, datasets, CI.

(Sweep §5.1 captured this as "Error analysis first; binary pass/fail; 70% pass rate ≥ 100%; 30 мин просмотра 20-50 outputs до инфраструктуры.")

Confidence: 5/5 — this claim is repeated identically across all four Hamel
sources and is borne out by the Pragmatic Engineer case studies.

### 1.2 Binary judgments, not Likert

`[paraphrase]` "Likert scales (1–5) collapse under inter-rater noise. A 3 from
me is a 4 from you. Binary forces you to commit." Once a judge — human or LLM
— has to say `pass` or `fail`, the rubric has to be sharp enough to justify
the call. Likert lets graders hide behind the middle.

Two corollaries:
- **Add a free-text `critique` field next to the binary** so the judge has somewhere to put nuance without polluting the metric.
- **Use ternary only for "off-policy" / "not applicable"** — a trace where the eval question doesn't apply (e.g. judging code quality on a `/diagnose` trace that produced no code). Off-policy is excluded from the denominator, not counted as a fail.

Confidence: 5/5.

### 1.3 The 70% pass-rate target

`[paraphrase]` "If your evals show 100% pass, your evals are wrong. If they
show 30% pass, your model is wrong. Aim for the band where the eval is
discriminating — usually 60–80% pass."

This is counterintuitive and worth holding firmly. A 100%-passing eval suite
has lost its ability to detect regression. The implication for Jarvis is:
when we adopt evals, we should *expect and want* a substantial chunk of red
in the first run — that's the eval working. Driving pass rate to 100% by
loosening rubrics is the most common anti-pattern (§6 below).

Confidence: 4/5 on the specific number; 5/5 on the principle. Hamel's
articles use 70% as a centre-of-band heuristic, not a hard rule.

### 1.4 LLM-as-Judge requires alignment, or it's noise

The judge methodology (`hamel.dev/blog/posts/llm-judge/`):

1. Build the judge prompt from your category-grounded rubrics.
2. Pick ~50 examples already labelled by a human (you).
3. Run the judge on them.
4. Compute three numbers: TPR (true positive rate — judge says fail when human says fail), TNR (judge says pass when human says pass), and overall agreement (Cohen's kappa is overkill at this volume; raw % is fine).
5. **If TPR or TNR < ~80%, do not deploy the judge.** Iterate on the prompt or use the judge only as a triage filter, not a metric.
6. Re-align quarterly — judge drift is real (model updates change behaviour).

The judge **must** be allowed to output a `critique` string alongside its
verdict. Two reasons: (a) the critique is the data you use to debug a bad
judge, (b) it captures the failure-mode evidence for the regression-test
loop (§1.5).

Confidence: 4/5 — the methodology is well-documented; the 80% TPR/TNR
threshold is Hamel's heuristic and may be too strict for some skills (e.g.
`/grill-me` quality is genuinely subjective; lower agreement may be
acceptable if compensated with smaller batches and faster human review).

### 1.5 Process metrics vs end-to-end metrics

`[paraphrase]` "End-to-end metrics tell you the system is broken. Process
metrics tell you where."

For agentic workflows with multiple LLM-driven steps, Hamel argues:
- E2E metric = "did the final artefact (PR, answer, file) satisfy the user goal?" — one bit, expensive to attribute.
- Process metrics = per-step bits: did step 1 produce a well-formed plan? did step 2 select the right files? did step 3 satisfy the linter? — cheap, attributable, catch cascade failures early.

For Jarvis this is direct: a `/implement` trace passes through `0a` (grill
checkbox), `0b` (memory recall), `1` (preflight), `3` (claim+branch),
`3.5` (record_decision), `4` (implement+lint+test), `5` (PR), `6`
(outcome_record). Each gate is a process metric. E2E is "PR merged + AC
satisfied + no regression in adjacent code". Both are needed.

Confidence: 5/5 — directly from `evals-skills-for-coding-agents`.

### 1.6 Production traces become regression tests

`[paraphrase]` "Every production failure that surprises you should become an
eval case. Permanent. The set only grows."

This is the data flywheel. The Braintrust tooling around it is convenient
but not essential — a failed `/implement` PR that we manually classify
becomes one new row in `evals/cases/implement.jsonl`, with the trace as
input and the human verdict as ground truth. Run the eval suite on every
prompt or skill change; CI red on regression, green on improvement.

This is the loop Jarvis already half-has via `outcome_record(status,
lessons)` — the missing piece is converting a `failure` outcome into an
eval case, not just a memory note.

Confidence: 5/5 on principle.

---

## 2. How this maps to a slash-command skill catalog

Skills in Jarvis are markdown documents read into the agent's context that
turn the agent into a workflow runner. They have:

- A **deterministic-ish skeleton** (numbered steps, gates, rules).
- **LLM-driven steps inside** the skeleton (read code, write code, write a
  PR body, judge whether to merge).
- **Hard rules expressed as text** that the agent may follow, paraphrase,
  or quietly skip.

This is exactly the workflow shape Hamel addresses in `evals-skills-for-coding-agents`.
The mapping:

| Hamel concept | Jarvis equivalent |
|---|---|
| Production trace | A full `/implement` or `/delegate` run (transcript + tool calls + git diff + PR + outcome_record) |
| E2E metric | `outcome_record.outcome_status == success` AND PR merged AND no rollback within 14d |
| Process metric | Each numbered step in the skill's pipeline ("did §0a checkbox actually run?", "did §3.5 emit `record_decision` with non-empty `memories_used`?", "did §4a already-done audit happen?") |
| Hard rule | "Subagents NEVER merge", "Do not modify protected files", "Cap parallelism at 2-3" — every one of these is a binary eval question |
| Judge prompt | LLM grader fed (skill text + trace + diff + outcome) — outputs binary per process-metric question |
| Regression case | A real failed run, frozen as JSONL, replayable against any future skill revision |

**Key insight specific to skills.** Skill markdown is itself an artefact
that can be A/B-tested. When we change `implement/SKILL.md` (e.g. add a new
gate, tighten the protected-files policy, rephrase the grill-me trigger),
we should be able to:

1. Replay the eval suite against the *new* skill text using the *old* trace
   inputs, asking "would the new skill have caught this failure?"
2. See the regression-or-improvement number before merging the change.

This is the tightest loop available and worth designing for from day one.

Confidence: 4/5 — mapping is sound; (1) above requires being able to
replay a trace, which is non-trivial because real `/implement` runs depend
on live `gh`/`git`/`pytest`, but the *judge* portion can replay against the
recorded trace + new skill prompt without re-executing tools.

---

## 3. Concrete proposal: eval suites for `/implement`, `/delegate`, `/grill-me`

I propose instrumenting these three because:

- `/implement` is **the highest-frequency, highest-blast-radius skill**, and
  it has the cleanest input/output (issue → PR).
- `/delegate` shares most of `/implement`'s pipeline plus subagent-specific
  failure modes (worktree contamination, scope drift, value-change audit) —
  much marginal value per case.
- `/grill-me` is **the skill we use to build the eval cases for the other two**,
  and its own failures (skipping the decision-recording gate, missing
  staleness flags) are precisely the failures that propagate downstream.

### 3.1 `/implement`

#### Eval dataset

`evals/cases/implement.jsonl` — one row per case:

```jsonc
{
  "id": "imp-0001",
  "source": "real-trace",            // or "synthetic" for hand-crafted edge cases
  "trace_path": "evals/traces/imp-0001.json",
  "issue_url": "https://github.com/.../issues/N",
  "issue_body_excerpt": "...",
  "pr_url": "https://github.com/.../pull/M",
  "pr_diff_path": "evals/traces/imp-0001.diff",
  "outcome_status_recorded": "success" | "partial" | "failure",
  "human_label": {                    // ground truth
    "e2e_pass": false,
    "process": {
      "grill_checkbox_run": true,
      "memory_recall_done": true,
      "preflight_5_checks": true,
      "claim_before_implement": true,
      "record_decision_emitted": false,   // <-- failure mode
      "already_done_audit": true,
      "no_protected_file_edits": true,
      "lint_test_run": true,
      "pr_body_rich": true,
      "outcome_recorded": true
    },
    "critique": "skipped §3.5 record_decision; memories_used was empty in the eventual outcome_record"
  }
}
```

#### Process-metric questions (binary, judged automatically)

Each becomes one judge prompt. Most can use the trace alone (no LLM needed
for the deterministic ones). I'll mark `[det]` for deterministic-checkable
and `[llm]` for needs-judge.

1. `[det]` Was step `0a` (grill checkbox) explicitly answered in the trace, with all 4 questions visible?
2. `[det]` Was step `0b` (`memory_recall`) called at least once before any code-modifying tool?
3. `[det]` Were the 5 preflight checks (assignees, label, comments, PR, branch) all run?
4. `[det]` Was `status:in-progress` label added before any `Edit`/`Write` tool call?
5. `[det]` Was `record_decision` emitted between claim and first code edit?
6. `[det]` Did `record_decision.memories_used` have ≥1 UUID, given that `0b` returned hits?
7. `[llm]` Did the already-done audit (§4a) actually grep for AC symbols, or was it skipped/faked?
8. `[det]` Were any protected files edited without principal approval in-session?
9. `[det]` Did lint + test commands run before commit?
10. `[llm]` Is the PR body "rich" per the §5 template (Summary, Why, Decisions & Alternatives, Risk, Testing, Files Changed all present and substantive — not boilerplate)?
11. `[det]` Was `outcome_record` called with non-empty `memory_id` if §3.5 had memories_used?

#### E2E question (binary, judged by LLM + human spot-check)

12. `[llm]` Given the issue body and the final PR diff: do the changes plausibly satisfy *every* explicit acceptance criterion in the issue, with no scope drift?

#### Sample judge prompt (for Q12 — the hardest)

```text
You are evaluating whether a code change satisfies a GitHub issue.

ISSUE BODY:
<<<
{issue_body}
>>>

ACCEPTANCE CRITERIA (extracted):
{enumerated_ac}

PR DIFF:
<<<
{pr_diff}
>>>

PR BODY:
<<<
{pr_body}
>>>

Verdict rules:
- PASS only if EVERY enumerated AC is demonstrably satisfied by the diff.
  A criterion is satisfied if there is a code change that implements it
  AND there is a test exercising the new behaviour (unless the AC explicitly
  excludes tests).
- FAIL if any AC is unmet, partially met, or only met in a comment / TODO.
- FAIL if the diff contains substantive changes outside the AC scope
  (scope drift). Cosmetic adjacent fixes (formatting, imports) are not drift.
- OFF_POLICY if the issue body does not contain enumerated ACs at all.

Output JSON only:
{
  "verdict": "PASS" | "FAIL" | "OFF_POLICY",
  "critique": "<one paragraph: which ACs satisfied, which not, evidence by file:line>",
  "ac_table": [
    {"ac": "<verbatim>", "satisfied": true|false, "evidence": "<file:line or 'none'>"}
  ]
}
```

#### Pass/fail rubric for the suite as a whole

- **Per-case E2E pass** = Q12 verdict is `PASS` AND no `[det]` process-metric is FAIL.
- **Suite pass rate** = passed cases / (total cases - off-policy).
- **Target band** = 60–80% (per §1.3). 100% means rubrics are too loose; <50% means model+skill are genuinely broken.

### 3.2 `/delegate`

Inherits all 12 questions from `/implement` (each subagent runs the
implement pipeline) plus delegate-specific:

13. `[llm]` Did the orchestrator produce a split plan ("which delegated, which inline, why") before claiming any issue?
14. `[det]` Was parallelism ≤ 3 concurrent subagents at any point in the trace?
15. `[det]` Did the orchestrator run the value-change audit (grep for numeric literals / defaults / seeds in the diff) before merge decision?
16. `[llm]` Did the orchestrator review the diff in the **main repo tree**, not the agent's worktree (§4a worktree-isolation caveat)?
17. `[det]` Did any subagent attempt to merge its own PR? (Hard fail — should be impossible per `JARVIS_PRINCIPAL=subagent` hooks; eval verifies.)
18. `[llm]` In the final outcome_record, do `pattern_tags` distinguish `subagent` vs `inline`?

The big one specific to `/delegate` is **scope-drift detection**. The
parent sweep memories about `parallel_delegate_worktree_isolation_failed_2026_04_20`
and the IK-seeds incident (`#648`) are exactly the failure class evals
exist for. A `/delegate` eval case derived from that incident should have:
- input: the original issue body + initial state of the file
- ground truth label: FAIL on Q15 (value-change audit), critique referencing
  the seed-vector replacement
- a frozen JSONL row that any future `/delegate` skill revision must clear
  before merge

#### Pass rate considerations

`/delegate` is lower frequency than `/implement` and has higher
per-failure cost. Target band slightly stricter: 70–85%, with all
hard-rule questions (Q14, Q15, Q17) at 100%.

### 3.3 `/grill-me`

`/grill-me` is the trickiest because the output is largely a *conversation
transcript*, not an artefact. The eval has to grade conversational
properties.

#### Process-metric questions

1. `[det]` Was `memory_list(always_load=true)` called before the first question?
2. `[det]` Was `memory_recall` called with the literal skill name `grill-me` in the query (per skill's own contract memory `grill_me_record_decision_gate`)?
3. `[det]` Were `outcome_list` calls made for the area being grilled?
4. `[det]` Was `CONTEXT.md` read?
5. `[llm]` Did the grill resolve WHY before HOW for each branch (no skipping to mechanism)? (Judge reads the transcript.)
6. `[llm]` For each architectural Q resolved in the transcript, was `record_decision` emitted in the same turn (not at the end)?
7. `[det]` Did the closing summary include `decision_uuids[]` for every architectural Q?
8. `[llm]` Did the grill ask one question at a time, or did it stack multiple?
9. `[llm]` When recalled memories were stale (file/skill/issue no longer exists), did the grill auto-flag and continue rather than asking the user?

#### E2E question

10. `[llm]` Given the grill transcript: did the user reach a state where a downstream `/to-prd` or `/to-issues` could act on the resolved decisions without re-grilling? (Judge reads the closing summary + decision_uuids list.)

#### Sample judge prompt for Q5 (WHY before HOW)

```text
You are grading a /grill-me transcript on the "WHY before HOW" rule.

TRANSCRIPT:
<<<
{transcript}
>>>

Rule: For each branch the grill walks, the FIRST question on that branch
must establish the *problem* the design addresses (WHY), not the
*mechanism* (HOW). Examples of HOW-first violations: "should we use Postgres
or SQLite?" before "what reads/writes is this storing?". Examples of
correct WHY-first: "what failure are we trying to prevent?" before "should
we add a hook?".

Verdict rules:
- PASS if every branch begins with a WHY question.
- FAIL if any branch begins with a HOW question and the user had to
  redirect ("wrong question").
- OFF_POLICY if the transcript has fewer than 2 branches (insufficient
  signal).

Output JSON: {"verdict": ..., "critique": "...", "branches_observed": N,
"violations": [{"branch": "...", "first_question": "..."}, ...]}
```

#### Pass-rate target

`/grill-me` is inherently subjective. Target band 50–75%. Lower bound is
fine — what matters is that regressions are visible and the absolute
trend is up over time.

---

## 4. Infrastructure — opinionated proposal

### 4.1 Where eval cases live

```
evals/
├── README.md                       — bootstrap doc, how to add a case
├── cases/
│   ├── implement.jsonl             — one row per case (schema in §3.1)
│   ├── delegate.jsonl
│   └── grill-me.jsonl
├── traces/
│   ├── imp-0001.json               — captured tool-call trace
│   ├── imp-0001.diff               — captured PR diff
│   └── ...
├── judges/
│   ├── implement_q12_e2e.md        — judge prompt, versioned
│   ├── implement_q07_already_done.md
│   ├── delegate_q15_value_audit.md
│   └── ...
├── alignment/
│   ├── implement_q12_alignment.csv — human labels vs judge labels for
│   │                                  the calibration set; recompute on
│   │                                  judge-prompt edits
│   └── ...
└── fixtures/
    └── synthetic/                  — hand-crafted edge cases (e.g. an
                                      issue whose AC are all already
                                      satisfied — must trigger §4a STOP)
```

Why JSONL not SQLite: trivial diff, trivially append-only, git-blameable,
no migration cost when the schema evolves. Migration to SQLite/Parquet
becomes worth it past ~500 cases or when query patterns get complex
(neither is true for the next 6 months).

### 4.2 Runner

`scripts/eval/run_evals.py` (single file, ~200 lines):

```python
# pseudocode
for skill in args.skills or ["implement", "delegate", "grill-me"]:
    cases = load_jsonl(f"evals/cases/{skill}.jsonl")
    for case in cases:
        det_results = run_deterministic_checks(case, skill)  # process metrics
        llm_results = run_judge(case, skill, judge_prompts)  # E2E + soft process
        record(case.id, det_results, llm_results)
    print_summary_table(skill)  # pass rate, per-Q breakdown, regressions vs last run
```

The runner reads each trace from disk, applies the deterministic checks
(grep the trace for tool-call patterns, parse JSON, etc.), then calls
Claude (via the existing Anthropic SDK or claude CLI) for the LLM-judge
questions, and writes results to `evals/runs/<timestamp>.json`.

A second script — `scripts/eval/diff_runs.py <run_a> <run_b>` — produces
a human-readable regression report.

Cost estimate: ~50 cases × ~10 LLM-judge questions × ~2K input + ~500
output tokens with Sonnet ≈ \$0.30 per full suite run. Comfortable inside
the \$20/month externals budget even at daily cadence; in practice the
suite runs only on skill edits and PR creation, so monthly cost is
single-digit dollars.

### 4.3 CI integration

Two stages, both opt-in to start:

1. **PR-time check** (only when files in `.claude/skills/` or `evals/`
   change): GitHub Action runs `scripts/eval/run_evals.py --skills <changed>`
   and posts a comment with the regression diff. Fails the PR if pass rate
   drops by more than ~5pp without an explicit waiver in the PR body.
2. **Nightly full run** on `main`: writes `evals/runs/<date>.json`,
   updates a small dashboard (markdown table in `evals/README.md` or a
   GitHub Pages page if appetite grows).

Path-filtered guard: per `CLAUDE.md` §"Path-filtered CI guards require a
meta-test (#326)", the eval workflow needs a co-located meta-test in
`tests/ci/test_evals_guard.py` covering both the path filter and the
regression-threshold logic.

### 4.4 Vendor question — Braintrust / Langfuse / in-house

**Recommendation: in-house JSONL + Python runner, for now.** Concretely:

- **Braintrust** is the best-in-class for the prod-trace → regression-test
  loop and PR blocking (parent sweep §5.3 captures this). Their pricing
  starts around \$50/seat/month for the team tier. For a solo dev with
  ~50 cases, this is overkill and burns the entire externals budget on a
  dashboard.
- **Langfuse** is open-source and self-hostable; fits the budget but
  introduces a Postgres + Docker dependency we don't have today, plus
  another auth surface. Worth revisiting if/when Jarvis gets a second
  user (i.e. never, per current plan).
- **LangSmith** is dashboard-first without the regression-test enforcement
  story — explicitly the wrong half of the toolkit per Hamel.
- **In-house** wins on (a) zero new dependency, (b) git-native diffs and
  blame, (c) the eval cases are queryable by `grep`/`Read` from any future
  Jarvis session, which is its own kind of memory.

The triggers to switch to Braintrust later:
- Cases > ~200 (manual JSONL editing gets painful).
- More than one human grader (alignment becomes a multi-person workflow).
- Need to share results with non-technical stakeholders (not in scope).
- Production volume justifies hosted trace ingestion.

Confidence on this recommendation: 3/5. The recommendation is shaped by
budget + solo-dev constraints; an organisation with different shape would
correctly choose differently.

### 4.5 Trace capture

The unsung dependency. We currently get partial traces via:
- `outcome_record.outcome_summary` (one paragraph)
- `record_decision.rationale` (one paragraph)
- The PR body and diff (concrete artefacts)
- Session transcripts under `~/.claude/projects/<project>/`

What we **don't** have is a clean "this was the full sequence of tool
calls in the `/implement` run" object. The session transcript files
contain it but are noisy and contain unrelated turns.

**Suggested addition to `/implement` and `/delegate` (separate issue, not
part of this design):** at end of pipeline, write a `evals/traces/<id>.json`
file containing the slice of session transcript that ran the skill, plus
the diff, plus the recorded decision UUIDs. This is the cleanest path to
having real eval cases instead of synthetic ones. Without it, building
even the first 10 real cases takes ~hours of manual transcript-extraction.

---

## 5. Bootstrap path

### Step 0 — pre-bootstrap (one session)

Read 20–30 recent `/implement` and `/delegate` PRs in `Osasuwu/jarvis`
and `SergazyNarynov/redrobot`. **Do not write any judge prompts yet.**
For each: open it, write a one-line free-text "what would have made this
not-a-success" if anything went wrong. This is the Hamel error-analysis
half-hour. ~3 hours of human time end-to-end if done in one sitting.

Output of step 0: a free-text doc in `evals/notes/error-analysis-2026-05.md`
listing failure modes seen. Cluster into 5–10 categories. **These
categories drive everything below — do not skip this step in the name of
moving fast.**

### Step 1 — instrument `/implement` first (one PR)

- Add the trace-capture step to `/implement` (writes `evals/traces/<id>.json`).
- Create `evals/cases/implement.jsonl` with a header comment + 0 rows.
- Land the runner skeleton in `scripts/eval/`.
- No judges yet, no CI integration.

### Step 2 — first 10 cases (one session, on top of step 1)

Pick 10 historical PRs. For each:
1. Reconstruct the trace from session transcripts (manual the first time;
   automated for future runs once trace-capture lands).
2. Add a row to `evals/cases/implement.jsonl` with `human_label.process`
   filled in from §3.1's question list.
3. Aim for 3–4 of these to be `e2e_pass: false` — the failure cases are
   what the suite exists to catch.

Specific candidates from the parent sweep + memories:
- A `/delegate` case from the IK-seeds incident (#648) — should fail Q15.
- A `/delegate` case from `parallel_delegate_worktree_isolation_failed_2026_04_20`
  — should fail Q14 and/or Q16.
- A `/implement` case from any PR where `record_decision.memories_used`
  was empty despite a populated `memory_recall` — should fail Q6.
- A `/implement` case where the already-done audit was skipped (any PR
  closed as duplicate post-facto, e.g. #237 closed as dup of #209) —
  should fail Q7.

The remaining 6 should be successful cases — the suite needs both
populations or the rubric never gets stress-tested.

### Step 3 — first judge + alignment (one session)

Pick *one* LLM-judge question first: **Q12 (E2E acceptance)**. It is the
most expensive and the most important.

1. Write the judge prompt (sketched in §3.1).
2. Run it on the 10 cases.
3. Compare to your human label. Compute TPR, TNR, agreement %.
4. If <80% on either, iterate the prompt. Common fixes: tighten the
   "AC must have a corresponding test" rule, add few-shot examples of
   PASS/FAIL diffs.
5. Once aligned, save the prompt to `evals/judges/implement_q12_e2e.md`
   with a header comment recording the alignment numbers and the
   commit SHA the alignment was measured against.

### Step 4 — regression-from-failure loop (continuous from here on)

Every time a `/implement` or `/delegate` PR ships and a problem is found
in review or post-merge:
1. Convert it into one new row in the eval JSONL (one-paragraph manual
   labelling).
2. Run the suite — confirm the new case fails (it should, that's why we
   added it).
3. Edit the skill markdown to address the failure mode.
4. Re-run the suite — confirm the new case now passes AND the existing
   passing cases didn't regress.
5. Land both the skill edit and the eval case in the same PR.

This is the Hamel data flywheel, expressed as a Jarvis development
discipline. The skill catalog goes from "rules I promised the agent
would follow" to "rules with empirical evidence of being followed".

### Step 5 — extend to `/delegate` then `/grill-me` (later sprints)

`/delegate` reuses 12 of `/implement`'s questions, so the marginal cost is
~6 new judges + dataset bootstrap. `/grill-me` is the long pole because
its outputs are conversational and require the most judge-prompt
iteration; defer until the first two are stable.

---

## 6. Anti-patterns to avoid (Hamel-canon plus Jarvis-specific)

### 6.1 Optimising for high pass rate

The most insidious failure mode. Once a number is on a dashboard, the
incentive is to make it green. The way that happens in practice: rubrics
get loosened, judge prompts get softer, off-policy classifications
multiply. The number stays green; the eval has stopped measuring
anything.

**Defence:** review rubric edits in PR with the same scrutiny as code
edits. If a PR softens a judge prompt, require the PR body to explain
*why the previous rubric was wrong* (not "why the judge was being too
strict"). Hamel's 70%-band rule is the operational version of this:
proactively flag suites that drift to >90% pass rate as "rubric likely
too loose, re-audit".

### 6.2 Likert-scale judges (1–5)

Already covered in §1.2. Specifically for Jarvis: it would be tempting
to grade `/grill-me` quality on a 1–5 "depth of grilling" scale. Don't.
Pick concrete binary questions: "did it ask about reversibility?" "did it
flag stale memory?" "did it surface a relevant outcome?". These compose
into something more useful than a 4 vs a 3.

### 6.3 Jumping to infrastructure before manual review

Hamel's flagship warning. The temptation: "let's set up Braintrust /
Langfuse first, then we'll have somewhere to put the cases." The result:
infra exists, no cases, no rubrics, three months later you have a
pretty dashboard showing nothing. The discipline is the inverse:
notebook + 50 traces + your eyes + a free-text spreadsheet. Only after
that does any tool become worth installing.

### 6.4 Judge model = generator model (silent)

If the judge for `/implement` E2E is the same Claude version doing the
implementation, expect inflated pass rates — the judge shares the
generator's blind spots. Mitigation: judge with a *different* model where
possible (e.g. Haiku judging Sonnet output, or vice-versa), and
periodically re-align with human labels. Document the judge model in the
judge prompt header so model-switch effects are visible in regressions.

### 6.5 Eval cases that are just unit tests in disguise

If every `/implement` eval case is "given this issue with AC `function X
returns 42`, did the diff implement X returning 42?", you're writing
unit tests with extra steps. Real eval cases capture *behaviour the
agent's process produced*: scope drift, skipped gates, missing decision
records, value mutations. Synthetic cases for edge conditions are fine,
but if the JSONL has zero rows derived from real production traces, the
suite is theatre.

### 6.6 Treating off-policy as fail (or as pass)

Off-policy = "this question doesn't apply to this trace" (e.g. judging
already-done audit on an issue that genuinely had nothing to grep for).
Off-policy is its own bucket — exclude from the denominator. Folding it
into either bucket creates spurious trend signal.

### 6.7 Jarvis-specific: skipping the trace-capture dependency

Without the `evals/traces/<id>.json` artefact (§4.5), every eval case
takes hours to bootstrap from raw session transcripts. It is tempting to
"just write synthetic cases", which lands you in 6.5. Trace capture is
the cheapest unblock; do not defer it.

### 6.8 Jarvis-specific: confusing `outcome_record` with eval

`outcome_record` is the agent's self-report. Eval is the third-party
check. They're complementary, not substitutable. The strongest eval
question on this axis is in fact: *"did the recorded outcome status
match the human-judged truth?"* If the agent self-reports `success` on
PRs that the human-judged eval marks `fail`, that gap is itself the
signal.

---

## 7. Open questions / decisions for owner

1. **Trace capture: write a separate issue and ship before any of §3?**
   My recommendation: yes. Without it the bootstrap is 5x longer. The
   issue is well-scoped (~1 file change in `/implement` and `/delegate`
   step 6) and unblocks everything below. Confidence: 5/5.

2. **Judge model — Sonnet, Haiku, or mixed?**
   Default suggestion: Sonnet for E2E (Q12), Haiku for the cheap
   process-metric soft questions (Q7, Q10). Cost ratio favours mixing;
   alignment story is harder. Open. Confidence: 2/5 — needs empirical
   alignment data to decide.

3. **Should `/implement`-eval be a Jarvis skill (`/eval-skills`) or just
   a script?** A skill would let it inherit the `record_decision` and
   `outcome_record` discipline, plus chain into `/self-improve` when
   regressions appear. A script is simpler but lives outside the
   memory loop. Mild lean: skill, given Jarvis's existing skill-first
   architecture. Open.

4. **Pass-rate band per skill — 70% target across the board or
   skill-specific?**
   `/grill-me` quality is genuinely more subjective than `/implement`
   correctness. Skill-specific bands feel right (60-80% / 70-85% /
   50-75% as suggested in §3) but I have no empirical basis for the
   exact numbers. Treat as conjectures.

5. **Cross-skill eval cases?**
   Some failure modes span the chain `/grill-me` → `/to-prd` → `/to-issues`
   → `/implement`. A real production case starts in one and ends in
   another. The proposed schema is per-skill; this misses end-to-end-
   chain eval. Open question whether to bolt on a `chains/` directory
   later or restructure.

6. **What to do when the eval suite catches a regression in CLAUDE.md
   itself, not in a skill?**
   Skill text is one input to the agent; SOUL.md, CONTEXT.md, and
   CLAUDE.md are others. A regression triggered by editing SOUL.md
   should fail the eval suite the same way a skill edit would. Are
   those files in the path-filter for the eval CI check, or only
   skills? I'd say yes (include SOUL/CONTEXT/CLAUDE.md), but it
   broadens the scope considerably.

7. **Production-trace privacy.**
   Real traces contain issue bodies, PR diffs, and decision rationales
   from `Osasuwu/jarvis` and `SergazyNarynov/redrobot`. The former is
   private personal infra; the latter is a research collaborator's
   project. The eval cases would be checked into a Jarvis repo — fine
   for `Osasuwu/jarvis`, requires a conversation with Sergazy for
   redrobot-derived cases. Decision needed before any redrobot trace
   becomes a frozen eval case.

8. **When do we revisit Braintrust?**
   Concrete trigger I propose: "when `evals/cases/*.jsonl` exceeds 200
   total rows OR the eval suite runtime exceeds ~10 minutes per CI run,
   re-evaluate Braintrust." Both metrics are mechanical; neither is
   reached today. Confidence: 3/5 on the threshold.

---

## 8. Summary recommendations (for the owner-facing note)

1. Spend the next session doing 30 minutes of error analysis on 20–30
   recent `/implement` PRs. Output: `evals/notes/error-analysis-2026-05.md`.
   Only after this should anything else here be acted on.
2. Ship trace-capture in `/implement` and `/delegate` (§4.5) as one
   small PR — it is the unblock for all real-trace eval cases.
3. Bootstrap `evals/cases/implement.jsonl` with 10 cases, ~3-4 of them
   real failure modes from the catalog already in memory (IK-seeds,
   worktree contamination, skipped record_decision, skipped already-done
   audit).
4. Stay in-house (JSONL + Python runner) until the suite reaches
   ~200 cases or runtime exceeds ~10 min per CI run. Then revisit
   Braintrust.
5. Treat the suite's pass rate as a tuning fork: green at 100% means
   the rubric is broken. Aim for the 60–80% band and *welcome* the red.

End of doc.
