---
title: Gap discovery patterns in AI-assisted solo dev workflows
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 40
adjacent_topics_flagged:
  - LLM-as-judge bias profiles within model families
  - Anthropic "dreaming" / auto-consolidation for CLAUDE.md
  - Calibration training apps (Clearer Thinking) as transferable practice
  - Solo founder coaching / "co-creator" advisory structure
  - Bret Victor-style live feedback for agent state
  - Multi-agent adversarial review panels (4-6 personas + supreme judge)
  - Sycophancy benchmarks (SycEval, ELEPHANT, BASIL) as eval scaffold
---

## TL;DR

The user has a strong **lagging-indicator** stack: decision audits, outcome tracking, `/reflect`, `/verify`, `/self-improve`, `memory_calibration_summary`. What is conspicuously thin or absent:

1. **A ground-truth eval harness that runs without him in the loop.** Decision audits ask "did I reason well?" *after* the fact. A 20-50 task golden set (Hamel/Anthropic minimum viable evals) run on a schedule against current skills/prompts would be the leading indicator he is missing — it fires *before* an outcome goes bad. This is the single largest gap.
2. **An out-of-band, cross-model adversarial reviewer that does not share Claude's blind spots.** Self-reflection skills written in Claude, evaluated by Claude, consumed by Claude is the recursion trap. The literature is unambiguous: same-family LLM judges share systematic biases (positional, sycophantic, self-recognition). A cheap second-opinion pass through Codex/Gemini on plans, decisions, and skill diffs is the cheapest insurance policy he is not yet buying.
3. **A behavioral observability layer over session transcripts** (Langfuse self-host, or a simple ccusage-style local script). `/reflect` does per-session pattern extraction but there is no persistent, queryable view of *which prompts cost what*, *which tools fire most*, *where time actually goes*. Without this, "the workflow isn't working" stays a feeling instead of a hypothesis.
4. **Calibration practice on synthetic predictions, not just real decisions.** Real decisions arrive too slowly to calibrate confidence. A weekly 10-question calibration drill (Clearer Thinking-style) on forecasts about *his own work* (will this PR pass CI first try? will this skill trigger correctly? will this milestone close in N days?) would tighten the confidence field of `record_decision` from "guessed number" to "measured number."
5. **Skill/CLAUDE.md bloat audit on a schedule.** He has consolidate-memory; the equivalent for skill files and CLAUDE.md should run automatically (Anthropic just shipped "Auto Dream" for exactly this). Bloated skill files cause invocation failures (Vercel observed 56% non-invocation rates with bloated skills); this is invisible until a skill *should* have fired and didn't.

The pattern: he over-instrumented the *human* side (decisions, reflections, outcomes) and under-instrumented the *system* side (evals, transcripts, cross-model checks).

## Landscape

### The two camps of gap discovery

Practitioners split cleanly into two camps:

**Camp A — Eval-first** ([Hamel Husain](https://hamel.dev/blog/posts/evals/), [Matt Pocock / AI Hero](https://www.aihero.dev/what-are-evals), [Anthropic engineering](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). Position: you do not know your workflow is broken without an objective measurement layer. Hamel's framing — "rigorous and systematic evaluation is the most important part of the whole system, and you should spend most of your time making your evaluation more robust" — is now the orthodoxy. Three-level structure: L1 unit-test-like assertions (cheap, on every change), L2 LLM-as-judge + human review on logged traces (cadenced), L3 A/B in production (rare, expensive). For a solo dev, L1+L2 is the budget; L3 only matters with users.

Anthropic specifically recommends 20-50 tasks sourced from *real failures*, not synthetic; multiple graders (code-based, model-based, human spot-check); calibration of LLM graders against human judgment; and monitoring for **eval saturation** (100% pass = no signal). They also publish pass@k and pass^k for non-determinism — the difference between "succeeds at least once in 5 tries" vs "succeeds all 5 tries" is exactly where reliability lives.

**Camp B — Observability-first** ([Langfuse](https://langfuse.com/), [Helicone](https://www.helicone.ai/blog/the-complete-guide-to-LLM-observability-platforms), [Braintrust](https://www.braintrust.dev/articles/langfuse-alternatives-2026), [Laminar](https://laminar.sh/article/langfuse-alternatives-2026)). Position: you cannot fix what you cannot see. The split inside this camp is interesting:

- **Braintrust** is eval-first (scorers, datasets, CI regression, prompt comparisons; blocks merge on quality drop). Best when you want CI gates.
- **Langfuse** is the OSS-permissive "everything" option (MIT, ClickHouse-backed, prompt mgmt + observability + evals). Self-hostable via docker-compose. Closest to feature parity with Braintrust without vendor lock.
- **Helicone** is request/response logging (proxy-based; eval tooling light; not span-aware out of the box).
- **Laminar** is debug-first (transcript view, Signals, SQL over traces). Strong for "what actually happened in this session."

For Claude Code specifically, [Anthropic's own analytics](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api) plus open-source [ccusage / Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) read local JSONL session logs — enough for "where did my tokens actually go this week."

### The recursion trap is real and well-documented

When a Claude-Code skill (`/reflect`) audits Claude-Code sessions evaluated by Claude with results consumed by Claude, every layer shares the same training distribution. [Collective Intelligence Project's audit of LLM judges](https://www.cip.org/blog/llm-judges-are-unreliable) measured this empirically: same-family models (mistral variants, GPT-4.1 vs 4.1 Nano) "tend to have similar profiles, meaning they often fail in identical ways — problematic for ensemble approaches." Positional bias of 60-69% (vs 50% random) on pairwise comparisons. Scoring variance of a full point on identical content depending on presentation order. A de-biasing prompt that **paradoxically increased** the bias by 5 percentage points.

The mitigation patterns that actually work in the wild:
- **Different model families for review** ([Telefónica blog](https://www.telefonica.com/en/communication-room/blog/multiple-ais-sequence-produce-robust-outputs-identify-blind-spots/), [adversarial-review plugin](https://github.com/robertoecf/adversarial-review)). The disagreement is the signal — "when all three models agree the code is fine, it's usually fine; when two disagree with one, dig deeper."
- **Heterogeneous personas + supreme judge** ([agent-review-panel](https://github.com/wan-huiyan/agent-review-panel)). 4-6 reviewer personas, blind scoring, anti-sycophancy flagging when >50% of position shifts lack new evidence, explicit "correlated-bias warnings" surfaced when unanimous.
- **Adversarial Builder/Critic split** ([ASDLC pattern](https://asdlc.io/patterns/adversarial-code-review/), [Claude Code Ultra's "3 explorers + 1 critic" architecture](https://www.mindstudio.ai/blog/claude-code-ultra-plan-multi-agent-architecture)). Builder optimized for generation, Critic explicitly told to be skeptical to counterbalance helpfulness bias. Distinct sessions, not nested prompts.

### Anti-vibe-coding / drift detection

[VibeDrift](https://www.vibedrift.ai/) and the broader [vibecoding.app anti-drift playbook](https://vibecoding.app/blog/anti-drift-workflows-vibe-coders-guide) frame the problem as architectural contradictions, hidden duplicates, security gaps surfacing over long sessions. The four anti-drift workflows that recur: PRD.md, Spec-Kit, persisted planning files, and the "Ralph Wiggum loop" (automated outer loop running off a spec). Notably the user already has spec-driven discipline (`/to-prd` → `/to-issues` → `/implement`); what's missing is the **architectural-contradiction scanner** that runs over recent sessions and flags "you decided X in session A, then implemented not-X in session B." His existing `record_decision` infrastructure is the substrate for exactly this scan but no scan is running.

### Context rot is observable

[Product Talk's writeup](https://www.producttalk.org/context-rot/) lists the leading indicators: model "reports back that it fixed it, even though it hadn't"; ignored CLAUDE.md rules; mistakes intensify past ~15 messages. The "lost in the middle" effect (Anthropic-confirmed) means information buried past 50% context capacity gets deprioritized. [MindStudio's context rot series](https://www.mindstudio.ai/blog/context-rot-claude-code-skills-bloated-files) extends this to skills specifically — bloated skill files (>60 lines per [alexop.dev's audit](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)) cause **invocation failures**. Vercel observed 79% pass rate with skills vs 100% with a compressed docs index embedded in AGENTS.md. The implication: when a skill *should* have triggered and didn't, you only notice if you're watching for it.

Anthropic shipped ["dreaming" / Auto Dream](https://letsdatascience.com/news/anthropic-introduces-dreaming-for-claude-agent-memory-consol-32a279c9) specifically to prune stale notes, merge duplicates, and resolve contradictions in CLAUDE.md-style memory files between sessions — explicit acknowledgement that this maintenance does not happen on its own and must be scheduled.

### Sycophancy is measurable, not vibes

[SycEval](https://arxiv.org/abs/2502.08177) measured 58.19% sycophantic behavior across ChatGPT-4o, Claude Sonnet, Gemini 1.5 Pro on math and medical advice. [ELEPHANT benchmark](https://arxiv.org/pdf/2505.13995) measures social sycophancy and offers inference-time detection. [BASIL](https://arxiv.org/html/2508.16846v4) takes a Bayesian approach. [Silicon Mirror](https://arxiv.org/html/2604.00478) reduced Claude Sonnet 4 baseline sycophancy from 9.6% to 1.4% (85.7% relative reduction) via dynamic behavioral gating. For a solo dev, the takeaway is not "deploy Silicon Mirror" but "the question 'am I being told what I want to hear?' is now an evaluable property, not a feeling." A 10-prompt sycophancy probe over a skill's outputs is a one-evening project that surfaces the problem.

### The decision-journal tradition predates LLMs

[Farnam Street's decision journal template](https://fs.blog/decision-journal/) and the engineering adaptation ("recording just three decisions per week for 90 days produces enough data to identify your dominant cognitive biases") match almost exactly what `record_decision` is designed for. The crucial part Farnam Street emphasizes that is hard to automate: **the review pass at 3-12 month horizon**. "The record alone has limited value — it is the comparison between recorded reasoning and actual outcomes that produces calibration." The user has the recording; the comparison/review pass is `/reflect` and the decision-audit-90d reports — so this loop *is* closed, but only for the decisions that got recorded in the first place. Post-hoc decision marking (`actor=...:post-hoc`) is a known regression flag in his setup; under-recording is the silent failure.

### Solo-practitioner literature

[Solo founder playbooks](https://entrepreneurloop.substack.com/p/building-a-startup-alone-solo-founder-playbook) converge on a single prescription: build an explicit "co-creator" advisory structure to substitute for the missing team. Bret Victor's [Inventing on Principle](https://jamesclear.com/great-speeches/inventing-on-principle-by-bret-victor) is older but the same principle — "creators need to be able to see what they're doing"; the agent equivalent is live transcripts and dashboards, not delayed reflection. The Feynman approach (self-explanation; "know how to solve every problem that has been solved") is the cognitive-science backbone of [rubber duck debugging](https://en.wikipedia.org/wiki/Rubber_duck_debugging) — and the rubber-ducking literature notes the explainer benefits *regardless* of whether the duck talks back, which is why writing a PRD before implementing tends to outperform asking an LLM directly.

## Concrete patterns / recipes

### 1. Minimum Viable Eval Harness (Hamel + Anthropic synthesis)

- **Source:** [Hamel Husain](https://hamel.dev/blog/posts/evals/), [Anthropic engineering](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- **How it works:** 20-50 tasks sourced from real failure transcripts. Each task: input, success criteria, reference solution. Three grader types per task: code-based (deterministic), model-based (LLM-as-judge with rubric), human spot-check (1 in N). Run on every meaningful change to a skill or prompt. Monitor pass@k (k tries, 1+ success) and pass^k (k tries, all succeed) for non-determinism.
- **What it catches:** Regressions in skill behavior after edits, prompt drift, when a "small" change breaks an unrelated capability. Lags behind decision audits in scope but leads them in time — fires before a real session produces a bad outcome.

### 2. Adversarial Cross-Model Review (Builder/Critic)

- **Source:** [robertoecf/adversarial-review](https://github.com/robertoecf/adversarial-review), [alecnielsen/adversarial-review](https://github.com/alecnielsen/adversarial-review), [ASDLC adversarial code review pattern](https://asdlc.io/patterns/adversarial-code-review/).
- **How it works:** Claude generates a plan/diff/skill. A **different family** (Codex CLI / Gemini / GPT) reviews independently — no shared context window, no peeking. Findings tagged `[cross-validated] | [external-only] | [host-only]`. P0-P3 severity. Fallback chain handles model availability; degraded mode = host reviews itself with banner warning. Round caps (5 max) prevent ping-pong.
- **What it catches:** Logic bugs and design errors a single model misses; sycophantic agreement masquerading as correctness; race conditions and swallowed exceptions that linting can't see ([2-years-of-AI-coding writeup](https://dev.to/yw1975/after-2-years-of-ai-assisted-coding-i-automated-the-one-thing-that-actually-improved-quality-ai-2njh)). The recursion-trap antidote.

### 3. Out-of-Band Ground Truth Test Set

- **Source:** [VentureBeat / silent failure literature](https://venturebeat.com/infrastructure/context-decay-orchestration-drift-and-the-rise-of-silent-failures-in-ai-systems), [arxiv 2511.04032](https://arxiv.org/abs/2511.04032).
- **How it works:** Curated set of inputs with known-correct outputs, run *on a schedule* (cron, not on change) against the live agent. Anomaly detection (XGBoost or SVDD per the paper) flags trajectory drift even without explicit failure codes. Critical property: runs **outside** the conversation the agent is in.
- **What it catches:** Silent quality degradation, goal drift, tool misuse, context loss — failures that "complete workflows and return responses that look correct until downstream consequences reveal the error." Exactly the class of failure that decision audits miss because no decision was visibly bad.

### 4. Multi-Persona Review Panel + Supreme Judge

- **Source:** [agent-review-panel](https://github.com/wan-huiyan/agent-review-panel).
- **How it works:** 4-6 Claude instances with distinct personas (Correctness Hawk, Security Auditor, Devil's Advocate, etc.), blind scoring discipline, 1-3 debate rounds where personas see each other, then blind final commits. Phase 14 supreme judge synthesizes; flags `High|Medium|Low` confidence; triggers human review on low-confidence. Built-in sycophancy detection (>50% position shifts without new evidence → flag).
- **What it catches:** Single-reviewer blind spots; group-think; cases where one persona spots something all others dismissed. The plugin author is explicit: "all reviewers are Claude instances...unanimous agreement may reflect shared model bias rather than ground truth." Use *with* cross-model review, not instead of.

### 5. Calibration Drill (Clearer Thinking-style)

- **Source:** [Clearer Thinking "Calibrate Your Judgment"](https://www.clearerthinking.org/post/2019/10/16/practice-making-accurate-predictions-with-our-new-tool).
- **How it works:** Weekly batch of 10 predictions about your own near-term work — "will PR #X pass CI on first try? 70%". "Will skill Y trigger correctly on this prompt? 85%". "Will milestone Z close in 7 days? 40%". Record. Score at the agreed horizon. Plot Brier score / calibration curve over weeks. When you say 90%, are you right 90% of the time?
- **What it catches:** The confidence field of `record_decision` becomes a measured number, not a guess. Surfaces systematic over- or under-confidence patterns. Pairs with decision audits: if your 80%-confidence decisions resolve at 60%, the calibration error is upstream of the reasoning quality.

### 6. Transcript Observability Layer

- **Source:** [Langfuse self-host](https://langfuse.com/self-hosting), [ccusage / Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor), [phuryn/claude-usage dashboard](https://github.com/phuryn/claude-usage).
- **How it works:** Cheap version — local script parses `~/.claude/projects/*/sessions/*.jsonl`, emits weekly summary: tokens per session, tool-call frequency, skill invocation counts, longest sessions, most-touched files. Expensive version — Langfuse docker-compose locally (Postgres + ClickHouse + web), pipe session traces in via OpenTelemetry, get SQL-over-traces + dashboards + LLM-as-judge scorers.
- **What it catches:** "Where did my Tuesday actually go" — the question solo devs can't answer without instrumentation. Surfaces token cost per prompt class, skill non-invocation, sessions where context rot likely fired (>30 turns), most expensive operations.

### 7. Skill/CLAUDE.md Bloat Audit (Scheduled)

- **Source:** [Anthropic Auto Dream / dreaming](https://letsdatascience.com/news/anthropic-introduces-dreaming-for-claude-agent-memory-consol-32a279c9), [alexop.dev progressive disclosure](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/), [rebelytics/one-skill-to-rule-them-all](https://github.com/rebelytics/one-skill-to-rule-them-all).
- **How it works:** Scheduled (weekly) pass over all skill files + CLAUDE.md, flagging: files >60 lines, duplicate instructions across ≥3 files (consolidation candidates), rules that could be automated (lint/typecheck), historical war stories that no longer trigger anything. Output is a diff proposal, never auto-applied. The rebelytics meta-skill goes further: watches sessions for corrections/adjustments as "skill could be clearer" signals.
- **What it catches:** Bloat-induced non-invocation (the 56% miss rate Vercel reported), duplicated rules drifting out of sync, dead skills consuming context budget. The user has `consolidate-memory` for memory; the equivalent for skills/CLAUDE.md is missing.

## What this user should consider given his context

His instrumentation is human-side-heavy: `record_decision`, `outcome_record`, `memory_calibration_summary`, `decision-audit-90d` reports, `/reflect` for behavioral patterns, `/verify` for outcome closure. All of these are **lagging** — they require an outcome to have happened. Specific gaps:

**Highest priority — minimum viable eval harness (pattern #1).** This is the biggest hole. He has no way to know a skill regressed without re-encountering the regression in the wild. 20-30 tasks per critical skill (`/implement`, `/grill`, `/research`), graded by a mix of code assertions and LLM-as-judge with a rubric, run on every skill edit + nightly via scheduled task. Stop-the-line if pass rate drops below baseline. This is the leading indicator he is missing.

**Second priority — cross-model adversarial review (pattern #2).** Every layer of his current setup is Claude evaluating Claude. The literature on within-family judge bias is unambiguous. A cheap Codex/Gemini pass on PRs before merge, plans before implementation, and skill diffs would catch a class of error his current stack systematically cannot. Even one cross-model check per `/grill` invocation breaks the recursion. Cost: ~$5/month if used judiciously.

**Third priority — transcript observability (pattern #6).** Start with the cheap version: a Python script over `~/.claude/projects/*` JSONL that emits a Sunday-evening summary. He'll discover patterns he didn't know existed within 2 weeks. Upgrade to self-hosted Langfuse only if the cheap version surfaces something worth deeper queries.

**Fourth priority — scheduled skill/CLAUDE.md audit (pattern #7).** He likely has bloat: project CLAUDE.md is already ~200 lines, user-level CLAUDE.md is dense, dozens of skills. The rebelytics-style meta-skill pattern (watch corrections during sessions, propose skill edits at end) fits his existing `/reflect` cadence naturally.

**Lowest priority but high-leverage — calibration drill (pattern #5).** 10 predictions per week, 15 minutes Friday afternoon. After 8 weeks the `confidence` field in `record_decision` becomes a measured property instead of a guess, and `/self-improve` gains a real signal for "the *reasoning* was fine, the *calibration* was off."

What he does **not** need: another self-reflection skill, more memory-store tagging, deeper decision rationale fields. Those are all human-side instrumentation he is already saturated on. The "it's not working" feeling is almost certainly the absence of system-side instrumentation — he cannot see what the agent is actually doing, only what he remembered to record about it.

## Adjacent topics worth deeper research

- **Sycophancy benchmark adoption** — can SycEval-style probes be miniaturized into a 5-prompt regression test for `/grill`? Would catch the "agent agrees with bad plan" failure mode at the skill level.
- **Anthropic Auto Dream rollout** — once GA, can it replace consolidate-memory entirely? What's the trust gap?
- **Calibration tooling specific to dev work** — is there a Prediction Book / Manifold flavor tailored to "will this PR merge?" or do we build it ourselves?
- **Coaching loop with second human** — solo founder literature is emphatic about the "co-creator" structure. Is there a low-friction way to get a human pair on architecture decisions once a month, vs leaning entirely on the agent?
- **Bret Victor live-feedback applied to agent runs** — could a HUD-style live transcript view (running cost, current tool, decision being made) reduce the "I lost track of what it's doing" failure mode? Different from observability dashboards — this is *during* the session.
- **Multi-agent panel cost economics** — is 4-6 reviewer personas + supreme judge cost-justifiable for a solo dev on a Claude Max sub, or does it only pay off for high-stakes single decisions?
- **Out-of-band ground truth set curation** — what's the minimum-viable set size for a personal agent, and how often does it need refreshing before it drifts from current behavior?

## Sources

- [Hamel Husain — Your AI Product Needs Evals](https://hamel.dev/blog/posts/evals/)
- [Anthropic — Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Anthropic — Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [Matt Pocock / AI Hero — Become a Real AI Hero](https://www.aihero.dev/)
- [Langfuse — open-source LLM engineering platform](https://langfuse.com/)
- [Langfuse self-hosting docs](https://langfuse.com/self-hosting)
- [Laminar — Langfuse alternatives 2026](https://laminar.sh/article/langfuse-alternatives-2026)
- [Braintrust — Langfuse alternatives 2026](https://www.braintrust.dev/articles/langfuse-alternatives-2026)
- [Helicone — Complete guide to LLM observability platforms](https://www.helicone.ai/blog/the-complete-guide-to-LLM-observability-platforms)
- [SycEval: Evaluating LLM Sycophancy (arxiv 2502.08177)](https://arxiv.org/abs/2502.08177)
- [ELEPHANT: Measuring social sycophancy in LLMs (arxiv 2505.13995)](https://arxiv.org/pdf/2505.13995)
- [BASIL: Bayesian Assessment of Sycophancy in LLMs (arxiv 2508.16846)](https://arxiv.org/html/2508.16846v4)
- [Silicon Mirror: dynamic behavioral gating for anti-sycophancy (arxiv 2604.00478)](https://arxiv.org/html/2604.00478)
- [Detecting Silent Failures in Multi-Agentic AI Trajectories (arxiv 2511.04032)](https://arxiv.org/abs/2511.04032)
- [Collective Intelligence Project — LLM Judges Are Unreliable](https://www.cip.org/blog/llm-judges-are-unreliable)
- [robertoecf/adversarial-review — review triad plugin for Claude Code](https://github.com/robertoecf/adversarial-review)
- [alecnielsen/adversarial-review — multi-agent code review (Claude + GPT Codex)](https://github.com/alecnielsen/adversarial-review)
- [wan-huiyan/agent-review-panel — multi-agent adversarial review panel](https://github.com/wan-huiyan/agent-review-panel)
- [rebelytics/one-skill-to-rule-them-all — meta-skill for skill improvement](https://github.com/rebelytics/one-skill-to-rule-them-all)
- [ASDLC — Adversarial Code Review pattern](https://asdlc.io/patterns/adversarial-code-review/)
- [Claude Code Ultra plan — 3 explorers + 1 critic architecture](https://www.mindstudio.ai/blog/claude-code-ultra-plan-multi-agent-architecture)
- [Anthropic introduces dreaming for Claude agent memory consolidation](https://letsdatascience.com/news/anthropic-introduces-dreaming-for-claude-agent-memory-consol-32a279c9)
- [alexop.dev — Stop Bloating Your CLAUDE.md (progressive disclosure)](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)
- [MindStudio — Context Rot in Claude Code Skills (bloated files)](https://www.mindstudio.ai/blog/context-rot-claude-code-skills-bloated-files)
- [Product Talk — Context Rot: Why AI Gets Worse the Longer You Chat](https://www.producttalk.org/context-rot/)
- [VentureBeat — Context decay, orchestration drift, and the rise of silent failures in AI systems](https://venturebeat.com/infrastructure/context-decay-orchestration-drift-and-the-rise-of-silent-failures-in-ai-systems)
- [VibeDrift — Detect drift in AI-generated codebases](https://www.vibedrift.ai/)
- [vibecoding.app — Anti-drift workflows for vibe coders](https://vibecoding.app/blog/anti-drift-workflows-vibe-coders-guide)
- [Telefónica — Use multiple AIs in sequence to identify blind spots](https://www.telefonica.com/en/communication-room/blog/multiple-ais-sequence-produce-robust-outputs-identify-blind-spots/)
- [DEV — After 2 years of AI-assisted coding, I automated AI pair programming](https://dev.to/yw1975/after-2-years-of-ai-assisted-coding-i-automated-the-one-thing-that-actually-improved-quality-ai-2njh)
- [Farnam Street — How a Decision Journal Changed the Way I Make Decisions](https://fs.blog/decision-journal/)
- [Clearer Thinking — Practice Making Accurate Predictions](https://www.clearerthinking.org/post/2019/10/16/practice-making-accurate-predictions-with-our-new-tool)
- [Bret Victor — Inventing on Principle (transcript)](https://jamesclear.com/great-speeches/inventing-on-principle-by-bret-victor)
- [Rubber duck debugging — Wikipedia](https://en.wikipedia.org/wiki/Rubber_duck_debugging)
- [Promptfoo — LLM red teaming guide](https://www.promptfoo.dev/docs/red-team/)
- [Promptfoo — How to red team LLM agents](https://www.promptfoo.dev/docs/red-team/agents/)
- [Maciek-roboblog/Claude-Code-Usage-Monitor — real-time usage monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)
- [phuryn/claude-usage — local dashboard for tracking Claude Code usage](https://github.com/phuryn/claude-usage)
- [Anthropic — Claude Code Analytics API](https://platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api)
- [Solo Founder's Playbook 2026](https://entrepreneurloop.substack.com/p/building-a-startup-alone-solo-founder-playbook)
- [Genai.qa — Promptfoo vs DeepEval vs RAGAS: when to use what](https://genai.qa/blog/promptfoo-vs-deepeval-vs-ragas/)
