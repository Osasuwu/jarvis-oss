---
title: AI agent failure modes — a taxonomy for orchestrator-side detection
date: 2026-05-18
status: working-doc
scope: jarvis orchestrator + /delegate subagent dispatch + post-flight verifier
sources:
  - MAST (UC Berkeley), arxiv:2503.13657 — Why Do Multi-Agent LLM Systems Fail?
  - AgentErrorTaxonomy / AgentDebug, arxiv:2509.25370
  - System-Level Failure Modes Taxonomy, arxiv:2511.19933
  - Aegis (agent-environment failures), arxiv:2508.19504
  - Empirical Study of Failures in Automated Issue Solving, arxiv:2509.13941
  - τ-bench auto_error_identification (Sierra Research)
  - Cognition AI — Don't Build Multi-Agents (Sep 2025)
  - Anthropic — How we built our multi-agent research system (Jun 2025)
  - Anthropic — Effective harnesses for long-running agents
  - OWASP LLM Top 10 (2025) + OWASP Top 10 for Agentic Applications (Dec 2025)
  - MITRE ATLAS — adversarial-only frame, scope-checked for operational overlap
---

## Executive summary

1. The Jarvis "three open issues" (#651 fabrication, #652 AC-dodge, #653 post-compaction premise hallucination) are **three modes inside one taxonomy of ~16**, not the taxonomy itself. The literature has done the categorisation work; we should adopt MAST as the spine.
2. **MAST (UC Berkeley, Mar 2025)** is the closest fit: 14 modes across 3 categories (specification/system-design, inter-agent misalignment, task verification). Validated across 7 frameworks, 200 traces, κ=0.88. Jarvis's #651/#652/#653 all sit in **FC3 (Task Verification)** — confirming the gap user named: we have ad-hoc points, not a frame.
3. **OWASP and MITRE ATLAS are mostly out of scope** for solo-dev Jarvis. OWASP LLM01/LLM07/LLM04 (prompt injection, system-prompt leakage, data poisoning) and **all** ATLAS tactics are adversarial. The exceptions worth keeping: **LLM06 Excessive Agency**, **LLM09 Misinformation**, **LLM05 Improper Output Handling**, **LLM08 Vector/Embedding Weaknesses**. From the OWASP Agentic Top 10: **ASI06 Memory Poisoning** and **ASI10 Cascading Hallucination** map to operational failure under benign conditions.
4. **The cheapest universal detection signal is `git diff --stat` + tool-output diff**, not LLM judges. Anthropic confirms: end-state evaluation beats step-by-step rubrics for cost. τ-bench's auto-error identifier shows a 4-class agent-fault taxonomy is enough resolution to drive fixes.
5. **Cognition's "context fragmentation"** is the unifying explanation: every Jarvis mode in #651/#652/#653 is a side-effect of orchestrator and subagent not sharing the same view. The verifier's job is to **reconstruct the missing shared view post-hoc** by reading objective state (`git diff`, `gh pr view`, test output, AC bullets) the subagent's summary should agree with.
6. **The row-23 verifier is a `PostToolUse` hook on `Task`** running 8–12 deterministic checks (see §6). Shadow mode for 2 weeks, then promote to blocking on Tier-1 checks only.
7. **Jarvis has detection gaps in 8 categories** beyond #651/#652/#653: loop-stuck, goal-drift, tool-misuse-no-error, premature-success, partial-success-claimed-complete, infinite-clarification, role-disobey, termination-condition-blindness. Sections 3–4 quantify cheapest signal per gap.
8. **Don't run LLM-as-judge on every dispatch** — cost-bloats, false-positives on legitimate "no-op needed" returns, and adds a second hallucinator. Reserve judges for sampling (~10%) or escalation (after deterministic check failure).

---

## 1. Survey of existing taxonomies

### 1.1 MAST — Multi-Agent System Failure Taxonomy (the spine)

Cemri et al., UC Berkeley, arxiv:2503.13657 (Mar 2025). 14 modes, 3 categories, derived from 200 traces × 7 frameworks (MetaGPT, ChatDev, HyperAgent, OpenManus, AppWorld, Magentic, AG2). Inter-annotator κ=0.88. Authors note "no single error category disproportionately dominates" — implication: a verifier needs coverage across categories, not depth in one.

**FC1. Specification and System Design Failures (5):**
- FM-1.1 Disobey task specification
- FM-1.2 Disobey role specification
- FM-1.3 Step repetition
- FM-1.4 Loss of conversation history
- FM-1.5 Unaware of termination conditions

**FC2. Inter-Agent Misalignment (6):**
- FM-2.1 Conversation reset
- FM-2.2 Fail to ask for clarification
- FM-2.3 Task derailment
- FM-2.4 Information withholding
- FM-2.5 Ignored other agent's input
- FM-2.6 Reasoning-action mismatch

**FC3. Task Verification and Termination (3):**
- FM-3.1 Premature termination
- FM-3.2 No or incomplete verification
- FM-3.3 Incorrect verification

### 1.2 AgentErrorTaxonomy (arxiv:2509.25370)

Modular five-axis split: **memory / reflection / planning / action / system**. Pairs with AgentDebug framework — root-cause isolation + corrective feedback. Validated on ALFWorld, GAIA, WebShop — up to 26% task-success improvement when failures classified before retry. Less granular than MAST but better-aligned to single-agent (Jarvis) shape.

### 1.3 System-Level Failure Modes (arxiv:2511.19933)

15 modes for production LLM apps. Named examples: multi-step reasoning drift, latent inconsistency, **context-boundary degradation** (≈ Jarvis #653), incorrect tool invocation, **version drift**, cost-driven performance collapse. Operationally-framed, not academic.

### 1.4 Aegis (arxiv:2508.19504) — Agent-Environment failures

6-mode taxonomy specifically for agent↔environment interaction breakdowns. Useful for the tool-misuse and stale-environment-state classes Jarvis sees post-compaction.

### 1.5 τ-bench (Sierra, arxiv:2406.12045)

Two-axis classification:
- **Fault assignment**: `user | agent | environment`
- **Fault type** (agent-side): `called_wrong_tool | used_wrong_tool_argument | goal_partially_completed | other`

Coarse, but enough to drive fixes. The **pass^k consistency drop** (GPT-4o: pass^1≈85%, pass^8≈25% on τ-retail) is the key empirical: agents that pass once are not agents that pass eight times. Implication for Jarvis: a verifier that runs on 1 attempt does not generalise to "the subagent is reliable."

### 1.6 Cognition AI — "Don't Build Multi-Agents"

Not a taxonomy — a single load-bearing diagnosis: **context fragmentation**. Subagents lose the orchestrator's full context, default decisions diverge, outputs become incompatible. Jarvis's #651/#652/#653 are downstream symptoms.

### 1.7 Anthropic — Multi-agent research system (Jun 2025)

Empirical failure inventory from production:
- Over-allocation (50 subagents for trivial query)
- Duplication + gaps under-spec'd dispatch
- Source-quality blindness (SEO over authoritative)
- Verbose query bias
- **Compound error propagation** — one bad step → entirely different trajectory

Evaluation strategy worth copying: **LLM-as-judge with 5-criterion rubric** (factual accuracy, citation accuracy, completeness, source quality, tool efficiency), 0.0–1.0 score, **end-state evaluation over step-by-step**. They explicitly warn against per-step validation — too expensive, too brittle.

### 1.8 OWASP LLM Top 10 (2025) — operational vs adversarial split

| Code | Name | Operational relevance to solo-dev Jarvis |
|---|---|---|
| LLM01 | Prompt Injection | Adversarial only — skip |
| LLM02 | Sensitive Information Disclosure | Edge (credentials in logs) — already covered by `.gitignore` |
| LLM03 | Supply Chain | MCP server pin already handles |
| LLM04 | Data and Model Poisoning | Adversarial only |
| **LLM05** | **Improper Output Handling** | **YES — diff-lie/coverage-lie are this class** |
| **LLM06** | **Excessive Agency** | **YES — autonomous edit outside intent** |
| LLM07 | System Prompt Leakage | Adversarial only |
| **LLM08** | **Vector/Embedding Weaknesses** | Edge (memory poisoning by stale records, see memory-arch deep-dive M8) |
| **LLM09** | **Misinformation** | **YES — fabrication, hallucinated premises** |
| LLM10 | Unbounded Consumption | YES (cost ceiling, infinite loops) |

### 1.9 OWASP Top 10 for Agentic Applications (Dec 2025)

Released as a separate framework. Operationally-relevant subset: **ASI06 Memory & Context Poisoning** (≈ #653), **ASI10 Cascading Hallucination Failures** (≈ #651/#652), ASI09 Tool Misuse, ASI03 Goal/Instruction Hijacking. The "progressive breach" framing (Lakera writeup) is useful: failures compose, so detection at any one stage prevents cascade.

### 1.10 MITRE ATLAS — out of scope

100% adversarial frame (Reconnaissance, Resource Development, ML Model Access, ML Attack Staging, ...). No category maps cleanly onto "agent makes honest mistake." **Skip for this taxonomy.** Useful only if Jarvis ever processes untrusted input.

---

## 2. The unified Jarvis taxonomy (mapping table)

Rows = failure modes. Columns: jarvis-seen / detection-mechanism / cost / FP-rate / mitigation.

| # | Mode | MAST/source | Jarvis seen | Detection mechanism | Detection cost | FP-rate | Primary mitigation |
|---|---|---|---|---|---|---|---|
| 1 | **Diff-lie** (claim ≠ git diff) | FM-3.3, LLM05, ASI10 | YES (#651: redrobot #678, #640) | `git diff --stat` post-flight; assert non-empty when summary claims edits | static, ~5ms | LOW (legitimate no-op cases) | Block merge + re-dispatch with diff in prompt |
| 2 | **Coverage-lie** ("tests pass" w/o test for new symbol) | FM-3.2, LLM05 | YES (#651: #688, #700) | Diff new symbols vs `grep tests/` for ref | static, ~50ms | MED (heuristic on test-path) | Block + require `pytest -k <symbol>` output |
| 3 | **State-lie** (claims PR merged when not) | FM-3.3, LLM09 | YES (#651: #397) | Regex "merged/closed/labeled" → `gh pr view` cross-check | static, ~200ms (gh call) | LOW | Refuse summary trust; verify |
| 4 | **AC-dodge** (item relabeled "out of scope") | FM-1.1, FM-2.3 | YES (#652: γ/δ delegations) | Parse issue `- [ ]` AC list; for each item, assert PR-body claim + diff hunk + explicit defer-rationale | static + light semantic, ~1s | MED | Block merge until each AC has a decision |
| 5 | **Post-compaction premise hallucination** | FM-1.4 (loss of history), "context-boundary degradation" (2511.19933) | YES (#653: 2026-04-22 "1.0" incident) | Detect compaction → grep every literal/path/symbol in prompt against repo before first Edit | runtime hook on Edit, ~300ms | LOW once grep is mechanical | PreToolUse gate on Edit/Write/gh post-compaction |
| 6 | **Loop-stuck / step repetition** | FM-1.3 | UNKNOWN (likely yes, unmeasured) | Track tool-call hash sequence; alert when window of 5 contains ≥3 identical args | behavioral, free | MED (legitimate retries) | Surface to orchestrator; force replan |
| 7 | **Goal-drift** (drifts to adjacent task) | FM-2.3 | LIKELY (no incident logged) | LLM-judge sample: "does the diff serve the issue title?" (10% sample) | runtime, ~$0.001/check | HIGH (judges hallucinate) | Sample-only, advisory, not blocking |
| 8 | **Tool-misuse no-error** (wrong tool, no exception) | FM-2.6, ASI09, τ-bench `called_wrong_tool` | UNKNOWN | Compare tool-args schema to issue-implied actions (heuristic) | runtime, ~10ms | HIGH | Advisory log; don't block |
| 9 | **Premature success declaration** | FM-3.1 | YES (covered by #651) | Subagent claims complete + (git diff empty OR test count = 0) | static, ~50ms | LOW | Block + re-dispatch |
| 10 | **Partial-success-as-complete** | FM-3.1 + FM-3.3 | YES (#652 mode-C silent-skip) | Sub-rule of AC-walk (#4) — partial AC coverage ⇒ block | static, derivative of #4 | LOW given AC-walk | Same gate as #4 |
| 11 | **Infinite clarification / no progress** | FM-2.2 inverse | UNKNOWN | Turn-count ceiling; if N turns with 0 tool calls → kill | behavioral, free | LOW | Hard kill at ceiling (60 turns?) |
| 12 | **Role disobey** (subagent edits protected files) | FM-1.2 | EDGE (orchestrator catches) | Path allowlist check on Edit/Write/MultiEdit | static, ~5ms | LOW | Already partially covered by `.claude/settings.json` permissions |
| 13 | **Termination-blindness** (keeps going past done) | FM-1.5 | UNKNOWN | Token-spend ceiling; warning at 50%, hard stop at 100% | behavioral, free | LOW | Hard ceiling; surface in summary |
| 14 | **Reasoning-action mismatch** (plans X, does Y) | FM-2.6 | UNKNOWN (high-confidence rare) | LLM-judge on transcript final block | runtime, ~$0.005/check | HIGH | Sample only; advisory |
| 15 | **Information-withholding** (skipped output) | FM-2.4 | YES (subset of #652 mode-C) | AC-walk catches it | derivative | LOW | Same as #4 |
| 16 | **Memory poisoning / stale-fact** | ASI06, LLM08 | LIKELY (CLAUDE.md "dead refs" rule exists) | Quarterly lint: parse memory bodies, test path/skill/issue existence | scheduled, free | LOW | Tag stale; demote rank (see memory-arch M8) |

**Seen-in-jarvis distribution** (issues closed/tracked vs gaps): 5 directly tracked, 4 "likely/unknown", 7 not measured. Verifier should cover at least #1–#5, #9, #10, #12 deterministically.

---

## 3. Detection-signal taxonomy by cost class

Anthropic's principle: prefer end-state to step-by-step. τ-bench's principle: 4 fault types is enough resolution. Combining:

### 3.1 Static (cheap, deterministic)

Run on PostToolUse(Task). Operate on `git diff`, `gh` JSON, file existence, regex. Catch modes #1, #2, #3, #4, #9, #10, #12, #15. **Highest leverage; verifier should be 80% these.**

### 3.2 Runtime (medium cost, hook-based)

Run on PreToolUse(Edit/Write/Bash). Operate on tool args + recent context. Catch modes #5 (compaction grep), #8 (schema check), #12 (path allowlist). **Catches things before they happen** — strict superset of static for those modes.

### 3.3 Behavioral (free, transcript-derived)

Run on hidden state: turn count, tool-call frequency, hash-of-args window. Catch modes #6 (loop-stuck), #11 (no-progress), #13 (termination-blindness). Cost zero, FP-rate medium. **Best for advisory signals**, not blocking.

### 3.4 LLM-judge (expensive, last-resort)

Catch modes #7 (goal-drift), #14 (reasoning mismatch). Reserve for: (a) sampling 10% of dispatches for quality calibration, (b) escalation when a deterministic check flags. **Never on every dispatch** — cost-bloats and adds a second hallucinator (Anthropic's 5-criterion rubric is the model here but it's quality-eval, not safety-gate).

---

## 4. What we DON'T have detection for (gaps beyond #651/#652/#653)

Cross-referencing §2's table with current Jarvis enforcement:

1. **Loop-stuck** (mode #6) — no turn-count or tool-arg-hash tracking exists. Cheap fix; not built.
2. **Goal-drift** (mode #7) — no judge sampling exists. Defer until volume justifies.
3. **Tool-misuse without exception** (mode #8) — no schema check. Hardest gap; OWASP ASI09 names it. Build only after #1–#5 deterministic checks ship.
4. **Premature success declaration as standalone** (mode #9) — partially covered by #651 fix (empty-diff catches), but "non-empty diff, still premature" is uncovered.
5. **Infinite clarification** (mode #11) — Claude Code probably has internal turn ceiling; not exposed to skill/hook layer.
6. **Termination-blindness** (mode #13) — token budget tracked per Anthropic's docs; not currently used as a Jarvis gate.
7. **Reasoning-action mismatch** (mode #14) — no transcript audit.
8. **Memory poisoning** (mode #16) — partial coverage via CLAUDE.md "dead refs" rule + memory-arch M8 lint proposal; not enforced.

**Priority for jarvis to act**: #6, #11, #13 are free behavioral signals — implement first. #1–#5 are the existing-issue territory (already grilled). #7, #14 (judge-class) defer.

---

## 5. Where #651 / #652 / #653 fit the spine

Mapped to MAST and AgentErrorTaxonomy:

| Jarvis issue | MAST mode | AgentErrorTaxonomy axis | OWASP map | Detection mechanism owed |
|---|---|---|---|---|
| **#651 diff-lie** | FM-3.3 Incorrect verification | Action + System | LLM05 + ASI10 | Static: `git diff --stat` ≠ ∅ when claimed |
| **#651 coverage-lie** | FM-3.2 No/incomplete verification | Action + Reflection | LLM05 | Static: new-symbol grep in tests/ |
| **#651 state-lie** | FM-3.3 Incorrect verification | System | LLM09 | Static: `gh pr view` cross-check |
| **#652 AC-dodge** | FM-1.1 Disobey task spec + FM-2.3 Task derailment | Planning | ASI03 | Static: AC-walk over `- [ ]` bullets |
| **#653 post-compaction premise** | FM-1.4 Loss of conversation history | Memory | ASI06 + 2511.19933 "context-boundary degradation" | Runtime: PreToolUse grep gate |

All three live in the **verification + history** half of MAST (FC1 + FC3). FC2 (inter-agent misalignment) is **understudied in jarvis** — likely because there's only ever one subagent active per dispatch. As soon as `/delegate` runs 2+ subagents on dependent issues, FC2 modes (information withholding, ignored input, reasoning-action mismatch) become live.

---

## 6. Verifier spec — checks the row-23 verifier should run

PostToolUse hook on `Task` tool. Reads: the Task tool call args (subagent prompt) + the Task tool return (subagent summary) + repo state (`git diff`, `gh`). 11 checks below, ordered by leverage. Tier-1 are blocking after shadow-mode passes; Tier-2 are advisory.

### Tier 1 — deterministic, blocking after 2-week shadow

1. **Diff-presence check** — if subagent summary contains any of `{edited, added, modified, wrote, created, refactored, fixed}` + filename token → `git diff --stat` must show non-empty. (Catches mode #1, #9.)
2. **Claimed-file-exists-in-diff check** — extract `path/to/file.py` patterns from summary → assert each appears in `git diff --name-only`. (Catches mode #1 sub-case.)
3. **Claimed-symbol-in-diff check** — extract `function_name`, `ClassName`, `method()` tokens from summary that are flagged as "added/new" → assert each appears in diff additions. (Catches mode #1 sub-case.)
4. **New-symbol test-coverage check** — for each function/class added in diff, grep `tests/` (or framework-equivalent path; lang-aware) for one reference. If new symbol has zero test refs and subagent claimed "tests pass" → block. (Catches mode #2.)
5. **State-claim cross-check** — regex summary for `"merged"|"closed"|"labeled X"|"PR #N opened"|"approved"` → run `gh pr view N --json state,merged,labels` and assert match. (Catches mode #3.)
6. **AC-walk** — fetch parent issue body → extract `- [ ]` AC bullets from `## Acceptance criteria` section → for each AC, assert: (a) PR body claims completion of this AC, OR (b) PR body has explicit `[deferred: <rationale>]` row. Reject if any AC has neither. (Catches mode #4, #10, #15.)
7. **Protected-files check** — diff touches `.claude/`, `.mcp.json`, `config/SOUL.md`, `~/.claude/CLAUDE.md`, `mcp-memory/schema.sql` → require explicit orchestrator approval, not just subagent claim. (Catches mode #12.)

### Tier 2 — advisory, log-only

8. **Empty-diff-with-non-trivial-claim check** — diff is empty AND summary is >200 tokens AND doesn't say "no changes needed" / "already implemented" → suspicious. Flag for orchestrator. (Sibling of #1; catches the "wrote a wall of text about work I didn't do" pattern.)
9. **Repeated-tool-args check** — hash subagent's tool-call args in a 5-call window; ≥3 identical → loop-stuck flag. (Catches mode #6.)
10. **No-progress check** — N consecutive turns with zero Edit/Write/Bash calls → kill candidate. (Catches mode #11.)
11. **Token-ceiling check** — running token spend > expected for issue size (heuristic: lines-of-AC × 10k tokens) → log warning. (Catches mode #13.)

### Out of scope (don't build into the verifier)

- LLM-judge on transcript (modes #7, #14) — separate sampler skill, not gate.
- Memory-staleness lint (mode #16) — covered by memory-arch deep-dive M8.
- Cross-subagent FC2 modes — premature; build when 2+ subagents run dependent.

### Shadow-mode rollout

Ship in 3 phases:
- **Phase 1 (2 weeks)**: all 11 checks run in shadow, log to `.claude/verifier-shadow.jsonl`. No blocking. Collect FP-rate, especially on Tier-1.
- **Phase 2**: promote Tier-1 (#1–#7) to blocking once FP-rate < 5% per check. Keep Tier-2 advisory.
- **Phase 3**: review Tier-2 quarterly. Promote #9 if loop-stuck incidents recur; otherwise hold.

---

## 7. PROPOSALS

| # | Proposal | Source | Priority hint | Notes |
|---|---|---|---|---|
| **B2-1** | **Ship the 11-check verifier as PostToolUse hook on `Task`** — shadow-mode 2 weeks, then promote Tier-1 to blocking | This doc §6; row 23 of master table | HIGH | The headline deliverable. Sized so shadow data drives the FP-rate cutoff, not vibes. |
| **B2-2** | **Adopt MAST as Jarvis's official failure-mode spine** — file the 14 modes as labels (`mast:fm-1.1`, ...) on the verifier issue cluster (#650/#651/#652/#653) | MAST paper | HIGH | Cheap (label creation + memory). Unlocks "what modes don't we measure?" queries. |
| **B2-3** | **AC-walk gate inside `/delegate` epilogue** (Tier-1 check #6) as the first Tier-1 to ship — covers #652 deterministically | #652 + AC-walk research | HIGH | Largest single MAST coverage win for jarvis: catches FM-1.1 + FM-2.3 + FM-3.1 + FM-3.2 in one check. |
| **B2-4** | **PreToolUse(Edit) compaction-grep gate** for mode #5 — only fires if Claude Code surfaces a "post-compaction" signal (#324 axis-4 dependency) | #653 + arxiv:2511.19933 | MED | Gated on #324 work landing. Until then, promote `post_compaction_task_premise_verification` to `always_load=true`. |
| **B2-5** | **Free behavioral signals**: turn-count + tool-arg-hash logger surfacing modes #6, #11, #13 as advisory | Anthropic engineering; MAST FM-1.3, FM-1.5 | MED | Zero LLM cost. Builds the dataset to know whether loop-stuck is actually rare in jarvis or just under-reported. |
| **B2-6** | **10% sampled LLM-judge on dispatched PRs** for modes #7 (goal-drift) and #14 (reasoning-mismatch) — 5-criterion rubric copied from Anthropic | Anthropic multi-agent post | LOW | Defer until #B2-1 lands. Don't add a second hallucinator before deterministic checks prove value. |
| **B2-7** | **Quarterly memory-poisoning lint** for mode #16 — scheduled task that greps memory bodies for dead file/skill/issue refs | OWASP ASI06; memory-arch M8 | MED | Already proposed in memory-arch deep-dive M8; called out here for taxonomy completeness. |
| **B2-8** | **PR-template AC-decision rows** — every AC bullet must have `[satisfied | deferred | dropped]` in PR body, GitHub Action rejects empty | #652 L4; AC-walk research | MED | Belt-and-braces with #B2-3. Shifts evidence burden to subagent at PR-creation time. |
| **B2-9** | **Promote three memories to `always_load=true`**: `subagent_fabrication_commit_message_vs_diff`, `subagent_test_coverage_overclaim`, `post_compaction_task_premise_verification` | This doc + #653 owner-decision | LOW | Tier-1 soft-prompt enforcement while Tier-2 hooks (#B2-1, #B2-4) are in flight. Reversible. |

---

## 8. Top surprises and contradictions

### Surprises

1. **MAST already exists, well-validated, and nobody pointed jarvis at it.** κ=0.88 across 7 frameworks is rare in this literature. The "do we need our own taxonomy" question is answered no.
2. **τ-bench's pass^k metric is brutal**: GPT-4o drops from 85% pass^1 to 25% pass^8 on retail tasks. Implication for Jarvis: success on one dispatch is **no evidence** of subagent reliability. The verifier must run on every dispatch, not statistically.
3. **Anthropic explicitly recommends end-state over step-by-step evaluation** — vindicates the row-23 "post-flight" framing over a more ambitious mid-flight monitor. The cheap thing is also the right thing.

### Contradictions with current Jarvis memory / open issues

- **Memory `verify_before_assuming_implemented` is always_load (Tier-1, soft).** The literature consensus (MAST FC3, Anthropic, AgentDebug) is that verification has to be **mechanical** (Tier-2). Tier-1 soft enforcement has known regression patterns (the empty-`memories_used` issue, #532, is the canonical example). The taxonomy says: don't keep Tier-1ifying verification rules — bake them into hooks. **#B2-1 is the canonical example of the right escalation.**
- **#653's mitigation L2 "promote memory to always_load"** is reasonable but the literature says that's a stopgap, not a fix. Mechanical L3/L4 is the load-bearing layer. Don't close #653 on L2 alone.
- **The "enforcement-primitive question" that converges across #650/#651/#652/#653** has a literature answer: **PostToolUse on `Task` for post-hoc verification, PreToolUse on `Edit`/`Write` for pre-prevention.** Not "skill epilogue vs hook" — both, at different stages. The four issues are not asking the same question; they're asking which stage their specific mode fits.

---

## 9. Don't-do list

1. **Don't build an LLM-judge gate.** Anthropic, MAST, and AgentDebug all stop short of using judges as blocking gates. Judges sample; deterministic checks gate. Building a judge-gate adds a hallucinator and ~$0.005/dispatch with no FP-rate ceiling.
2. **Don't merge OWASP/MITRE adversarial frames into the operational taxonomy.** Prompt injection, system-prompt leakage, model poisoning, every ATLAS tactic — all assume an adversary. Jarvis runs solo-dev benign. Mixing the frames inflates the surface and dilutes detection signal.
3. **Don't pursue FC2 (inter-agent misalignment) coverage yet.** All 6 FC2 modes require ≥2 concurrent subagents communicating. Jarvis dispatches one subagent per worktree. Build coverage when the architecture forces it, not before.
4. **Don't extend the verifier to mid-flight monitoring.** The literature consensus is end-state. Mid-flight monitoring adds context-window pressure (the orchestrator has to read every subagent step) and triggers OWASP LLM10 (unbounded consumption) failure modes of its own.
5. **Don't auto-block on Tier-2 checks without 2-week shadow data.** FP-rates on heuristic checks (coverage-lie, repeated-args) are unknown for jarvis specifically. Promoting to blocking before measuring leads to legitimate work being rejected.
6. **Don't roll the verifier into `/delegate` SKILL.md** — that's instruction-based enforcement, the same primitive #316 already tried and the cluster #650/#651/#652/#653 documents as insufficient. Hook layer or it doesn't bind.

---

## Sources

- [MAST — Why Do Multi-Agent LLM Systems Fail?, arxiv:2503.13657](https://arxiv.org/abs/2503.13657)
- [MAST HTML version with full 14-mode list](https://arxiv.org/html/2503.13657v1)
- [Berkeley Sky Computing — MAST project page](https://sky.cs.berkeley.edu/project/mast/)
- [AgentErrorTaxonomy / AgentDebug, arxiv:2509.25370](https://arxiv.org/abs/2509.25370)
- [System-Level Failure Modes Taxonomy, arxiv:2511.19933](https://arxiv.org/abs/2511.19933)
- [Aegis — Agent-Environment Failures, arxiv:2508.19504](https://arxiv.org/html/2508.19504v1)
- [Empirical Study on Failures in Automated Issue Solving, arxiv:2509.13941](https://arxiv.org/html/2509.13941v1)
- [τ-bench paper, arxiv:2406.12045](https://arxiv.org/pdf/2406.12045)
- [τ-bench auto_error_identification source](https://github.com/sierra-research/tau-bench/blob/main/auto_error_identification.py)
- [Sierra τ-bench blog](https://sierra.ai/blog/benchmarking-ai-agents)
- [Cognition AI — Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [OWASP LLM Top 10 (2025) project page](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [OWASP GenAI Security — LLM Top 10 index](https://genai.owasp.org/llm-top-10/)
- [OWASP Top 10 for Agentic Applications (Dec 2025)](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-aiapplications/)
- [Lakera — Progressive Breach model for OWASP Agentic Top 10](https://www.lakera.ai/blog/the-progressive-breach-model-behind-the-owasp-top-10-for-agentic-applications)
- [MITRE ATLAS (referenced, scope-checked out)](https://atlas.mitre.org/)
- [Galileo — 7 reasons multi-agent systems fail](https://galileo.ai/blog/why-multi-agent-systems-fail)
- [Qualitative analysis of LLM failures in agentic simulations, arxiv:2512.07497](https://arxiv.org/html/2512.07497v2)
