---
title: Solo-dev sustainability and cognitive load under multi-stream AI work
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 22
adjacent_topics_flagged:
  - late-night-quality-gating (Q4 rejected hooks; passive signals only)
  - reflect-weekly-digest-content
  - working-state-cross-repo-contamination
  - parallel-session-supervisor-load
  - hyperfocus-recovery-cadence
  - circadian-aware-task-routing
---

## TL;DR (≤200 words)

The evidence is unkind to the "parallel agent operator" self-image. Sophie Leroy's attention-residue work (cited >2,500x) shows that switching tasks under time pressure leaves measurable cognitive residue on the *next* task, not just the abandoned one; the cleanest mitigation is a "ready-to-resume" plan written *before* switching. Mitchell Hashimoto — arguably the best-instrumented solo AI operator in 2025-26 — explicitly **does not run parallel agents** and turns off agent notifications because "context switching is very expensive." Simon Willison runs parallel agents but only for *low-review-cost* spike/maintenance work; high-stakes review is still serial. Wagner-lab follow-ups show heavy media multitaskers underperform on memory and task-switching — and crucially, they *don't know it* (self-rated performance ≈ peers).

For Jarvis: the right intervention is **surface, not steer**. A weekly `/reflect` digest with 6-8 measurable patterns (switch counts, days-on-issue, hyperfocus chains, hyperdrive→crash signatures) lets the owner self-regulate without being nagged. Stop-day-night-routing (Q4 outcome) stands; the digest replaces real-time hooks. Specific proposals B6-B14 below; the "passive sustainability dashboard" spec at the end is the load-bearing artefact.

## Landscape (≈1,400 words)

**Attention residue (Leroy 2009 and replications).** Sophie Leroy's "Why is it so hard to do my work? The challenge of attention residue when switching between work tasks" [1] (Organizational Behavior and Human Decision Processes 109, 168-181) defined attention residue as "the persistence of cognitive activity about a Task A even though one stopped working on Task A and currently performs a Task B." Four experiments showed that performance on Task B is degraded by residue from Task A, especially when Task A is left unfinished under time pressure. Her follow-up work (Leroy & Glomb 2018 [2]) — "Tasks Interrupted: How Anticipating Time Pressure on Resumption of an Interrupted Task Causes Attention Residue and Low Performance on Interrupting Tasks" — replicated the original effect *and* identified the principal mitigation: a **"ready-to-resume" plan** (≤1 minute writing down where you stopped, what's next) eliminates most of the residue. This is the single most actionable finding in the entire literature for a multi-project solo operator.

**Media multitasking and self-awareness (Ophir/Nass/Wagner, replication).** The original Ophir, Nass & Wagner 2009 PNAS paper [3] reported that heavy media multitaskers (HMMs) underperform on tests of cognitive control. The 2017 Wiradhany & Nieuwenstein replication + meta-analysis [4] (Attention, Perception & Psychophysics) is mixed: of 14 replication tests, only 5 reached significance in the predicted direction; only 2 survived a Bayesian correction; the meta-analytic effect on 39 sizes was weak and went nonsignificant after small-study correction. The downstream Wagner-lab 2018 work [5] (covered in Stanford News) recentered on **memory** — HMMs show reduced working-memory and long-term memory performance, with the strongest effect being that **they don't notice it**: self-rated performance tracks light multitaskers'. For an owner who runs 2-3 parallel agent sessions plus passive audio, the load-bearing question is not "can I do this?" but "would I know if I were degrading?"

**Parallel-agent supervision in 2025-26 — what actual practitioners say.** Simon Willison's October 2025 essay "Embracing the parallel coding agent lifestyle" [6] is the most-cited steelman. His pattern is *not* "run N agents in parallel on the same hard problem"; it is "fire off agents for tasks that don't require much review attention (spikes, exploration, fixing deprecated-warning lint sweeps), keep your main agent serial." He explicitly names the bottleneck: "I can only focus on reviewing and landing one significant change at a time." His later "sub-agents" piece [7] and "code research projects with async coding agents" [8] reinforce the same shape — parallelism happens at the *task-firing* layer, not the *cognitive-supervision* layer. **Mitchell Hashimoto's "My AI Adoption Journey" [9] is the explicit counterweight**: "I'm not [yet?] running multiple agents, and currently don't really want to." His safeguards: **turn off agent desktop notifications**, manually check progress during natural breaks, "before every transition... ask: what's a slow thing my agent could do next?" His honesty bar: he runs background agents only 10-20% of a normal workday, and treats that as aspirational not embarrassing. The TeamDay summary [10] and Pragmatic Engineer profile [11] cover his "harness engineering" pattern — build linting/test infrastructure that catches the *class* of mistake so you don't have to supervise individual instances.

**Context switching cost in code work.** Gloria Mark's UC Irvine work (popularised in dozens of secondary sources [12][13]) puts the recovery time after an interruption at ~23 minutes for typical knowledge work, **~45 minutes for complex programming tasks**. A Journal of Systems and Software study cited in [13] reports interrupted programming sessions increase bug-introduction probability by ~50%. The implication is that a hard switch jarvis→redrobot is, in expectation, a 30-45 minute productivity tax on the destination side — and that tax is **paid by the receiving repo whether or not the owner perceives it**. Critically, this measurement is *post-Slack-era* but *pre-parallel-agent-era*; we have no good replication for the case where the interruption is "checking on a background agent in a sibling worktree." Best evidence-informed guess: the residue is smaller (no new domain context), but nonzero, and accumulates over many micro-checks.

**Cal Newport, Slow Productivity (2024).** Newport's [14][15] three principles: **(1) Do fewer things**, **(2) Work at a natural pace**, **(3) Obsess over quality**. Each project carries "overhead tax" (status checks, mental modeling, tool config), so the marginal cost of adding a project is super-linear. Two principles bind directly on jarvis-vs-redrobot routing: "natural pace" argues for accepting that some weeks one repo gets 80% of attention and the other coasts on background agents, *not* trying to split 50/50; "do fewer things" argues against opening a third active milestone when 2 are open. Notably, Newport's prescription is explicitly for "autonomous knowledge workers... freelancers, solopreneurs, small business owners" — i.e. the owner's exact category.

**Solo-founder burnout (Levels et al.).** Levels.io's public commentary [16] and the 2025-2026 indie-hacker surveys [17][18] converge on a uniformly grim baseline: **54% burnout rate** in the past 12 months among solo founders, **75% reporting anxiety episodes**, **46% rating mental health "bad" or "very bad."** Burnout — not strategy, market, or capital — is the #1 reported cause of solo-founder failure. The Levels-adjacent advice cluster is consistent: optimise for happiness not money, work *fewer* hours when burned (multiple founders report dropping 50→30h/week with **no MRR impact**, a finding that maps cleanly onto Newport), automate aggressively to remove non-craft work. Worth noting: the 60-75% "AI fatigue" figure in the Clearing-AI 2025 statistics page [19] is a self-report number and confounds "AI is exhausting" with "AI is changing my craft identity"; treat as directional, not causal.

**Sleep, circadian quality, debugging.** The 2018 Fucci et al. study "Need for Sleep: the Impact of a Night of Sleep Deprivation on Novice Developers' Performance" [20] (45 CS students, 23 sleep-deprived vs 22 control, IEEE TSE) is the canonical reference: **~50% reduction in functional correctness** of implementations after one night of sleep deprivation; engagement degraded; ability to follow TDD discipline degraded; more syntactic-error fix-loops. Caveat: novices, lab task, one-night-acute. We do not have a clean 2024 study on chronically partial-sleep experienced developers using AI agents. Best-evidence-from-adjacent-fields take: short-sleep nights ≈ -30-50% on tasks requiring "tight specification → tight verification" loops (TDD, careful spec writing). Hyperfocus repair tasks (mechanical refactors, big edits with strong agent supervision) seem less affected — but we are inferring, not citing.

**Energy management and ultradian rhythms.** Tony Schwartz's *The Power of Full Engagement* (Loehr & Schwartz) [21] popularised the 90-minute ultradian cycle (originally Kleitman 1950s, BRAC). The empirical basis is older sleep-cycle research; the *workday* extrapolation is more practitioner-tradition than RCT-grade. The robust finding is "breaks every 60-120 min restore performance"; the specific 90-min number is approximate. Useful as a *frame* (energy is the constraint, not time) rather than a *prescription* (set a 90-min timer).

**Decision fatigue / ego depletion.** Baumeister's 1998 strength-model [22] underwent a publicised replication failure: a 23-lab registered replication report [22] found no depletion effect, despite 22 of 23 labs predicting success. The current scientific status is roughly: the *original* protocol does not reliably replicate, but a softer *self-control fatigue* construct (more contextual, harder to operationalise) has partial support. **Practical takeaway for jarvis design**: do not build features that assume "willpower depletes over the day in a measurable way." Do build features that respect the *observed* pattern of the specific operator (e.g. owner's own historical data showing more abandoned tasks after hour N).

## Concrete patterns / recipes (4 patterns)

### 1. Ready-to-resume note before every cross-repo switch
Source: Leroy & Glomb 2018 [2].
**How it works:** Before switching jarvis → redrobot (or session A → session B), write ≤3 lines: "where I stopped / what's next / one open question." The cognitive function isn't the note itself — it's the act of *closing the loop* on Task A. Eliminates most measured attention residue.
**Who it suits:** Anyone with ≥2 parallel projects. This is the highest-leverage single behaviour in the entire literature.

### 2. Parallel-only for low-review-cost tasks (Willison rule)
Source: Willison 2025 [6][7][8].
**How it works:** Fire parallel agents only for: spikes you can throw away, mechanical maintenance (deprecated warnings, lint sweeps), code research / library evaluation. Keep architectural/PR-mergeable work serial. The bottleneck is *your review attention*, not agent throughput.
**Who it suits:** Owner already does this implicitly with sandcastle. Make it explicit so the next decision ("can I parallelise this?") has a checklist.

### 3. Notification-off, check-on-transition (Hashimoto rule)
Source: Hashimoto "My AI Adoption Journey" [9].
**How it works:** Agent desktop/audio notifications off by default. Check agent state only at *natural* transition points (end of pomodoro, just before leaving desk, etc.). Notifications fragment attention worse than they save time.
**Who it suits:** Owner runs jarvis as ambient audio — that's *intentional* passive intelligence, not notifications. The rule applies to *active* agent pings (PR ready, build failed mid-session). Currently jarvis is broadly aligned; verify telegram channel isn't violating this.

### 4. Weekly retrospective digest, not real-time hook (Q4-respecting)
Source: synthesis of Newport [14] + Leroy [1] + indie-hacker burnout data [17][18].
**How it works:** Once a week (Sunday or Monday morning), `/reflect` produces a digest covering switch counts, longest-stuck issue, days-on-issue distribution, hyperfocus-chain length, abandoned-task count. Owner reads, owner adjusts. No interrupts, no quality gates, no "are you sure?" prompts.
**Who it suits:** Q4 explicitly rejected hooks-on-late-night; this is the directly-compatible alternative.

## Failure modes & open questions

**Self-deception about parallel capacity (HIGH confidence).** Wagner-lab finding [5] that HMMs don't perceive their own degradation is the load-bearing risk. Owner's `user_working_pattern_multistream` memory says "hyperfocus capability... parallel projects via parallel Claude Code sessions" — this is owner's self-report. We have no independent measure. The dashboard (below) should let owner check this against actual throughput, not just feel.

**Hyperfocus → crash asymmetry (MEDIUM confidence).** Levels-cluster reporting [16][17] suggests hyperfocus-driven solo founders are over-represented in the burnout-failure bucket. The exact mechanism is contested: chronic short-sleep accumulating across hyperfocus stretches, or social-isolation, or skipped-meal/blood-sugar effects. Jarvis can only measure proxies (session length, commit cadence, gap days).

**Hooks vs nags — Q4 line.** Q4 rejected the late-night quality gate. But the same evidence base (Fucci 2018 sleep [20] + circadian variability) suggests *some* signal would be useful. The line we walk: any feature that *interrupts* a session is a nag; any feature that *aggregates and shows on demand* is a digest. All B6-B14 below stay on the digest side.

**Single-pilot operations analogy is weaker than it looks.** Aviation SPO research [via search results, NASA Flight Cognition Lab, MDPI 2025 scoping review] is mostly about *adaptive automation* taking control when the human is overloaded — pilot incapacitation, EEG-based workload detection. The relevant insight for jarvis is *not* "build EEG monitoring," it's "the human is the supervisory bottleneck; design the system so the agent can keep going safely when the human is briefly absent." This is closer to sandcastle / autonomous-loop than to dashboards.

**Decision fatigue is over-claimed.** Skip features predicated on "willpower runs out at hour N." Do measure owner's *own* abandonment-after-N-hours pattern empirically; that's data, not theory.

## Concrete proposals

### [B6] Ready-to-resume snippet in working_state — HIGH priority
**One-liner:** Extend `working_state_<repo>` memory schema to require a 3-line "where I stopped / what's next / one open question" before session close.
**Why:** Single highest-leverage intervention in the literature [2]. Costs ~30 seconds, eliminates most cross-session attention residue on resume.
**Where:** `/end` and `/end --quick` skills, `working_state` save path.
**Anti-nag:** Triggered by the user-initiated `/end` only; never auto-injected mid-session.

### [B7] Cross-repo switch counter in weekly reflect — HIGH priority
**One-liner:** Count session-context loads per repo per day; surface in `/reflect` weekly: "switched repos N times across M days; longest single-repo stretch was X hours."
**Why:** Lets owner notice the parallel-vs-serial pattern without being told it's wrong. Aligns with Wagner-lab finding that self-perception is unreliable — give owner the data, not the verdict.
**Where:** `scripts/session-context.py` already loads per-project context; emit an event tagged `session-start` with repo name; `/reflect` aggregates.

### [B8] Days-on-issue distribution in weekly reflect — MEDIUM priority
**One-liner:** For each open issue assigned to owner, compute (now - first-commit-touching-it); surface top 5 longest-running.
**Why:** Newport's "do fewer things" + scope-creep early-warning. Surfaces stuck work without judgement. Owner self-decides whether to grill, defer, or kill.
**Where:** `/reflect` digest, GH API query.
**Anti-nag:** A list, not a prompt. No "do you want to grill this?" — owner decides.

### [B9] Hyperfocus chain detector — MEDIUM priority
**One-liner:** Detect "session length > N hours, no break > 15min, single-repo" stretches; record to memory as `hyperfocus_session` events; surface count & avg-length weekly.
**Why:** Hyperfocus is a stated capability but also a known burnout antecedent in the solo-founder cluster [16][17]. Owner self-regulates if they see "you had 4 hyperfocus chains > 5h this week vs avg 1.5."
**Where:** `SessionEnd` hook computes; `/reflect` aggregates.
**Anti-nag:** Reported weekly only; no real-time "you've been at this 4 hours, take a break."

### [B10] Hyperdrive→crash signature scan — MEDIUM priority
**One-liner:** Pattern-match for "N consecutive >4h days, followed by ≥1 day of <30min activity" — the canonical sprint-then-crash signature. Surface in weekly reflect if matched in last 14 days.
**Why:** Indie-hacker data [17][18] identifies this exact pattern as the burnout precursor. Surfacing it after the fact is anti-nag-compliant; surfacing *before* it would be a quality gate (Q4-rejected).
**Where:** `/reflect` digest with rolling 14-day window.

### [B11] Single-pilot mode flag — MEDIUM priority
**One-liner:** A `working_state.single_pilot=true` flag the owner sets manually when they want jarvis to *suppress* all advisory output (skill suggestions, "did you mean", grill nudges) — keep only safety-critical (memory writes, hook failures).
**Why:** Hashimoto rule [9]. When in deep work, ambient suggestions are interrupting cost. Owner toggles, no inference required.
**Where:** SessionStart context loader respects flag; subagent dispatchers skip advisory.

### [B12] Domain-context cross-contamination guard — LOW priority
**One-liner:** When the same Claude Code instance reads files from both `jarvis/` and `redrobot/` in one session, emit a one-time inline note "you're touching 2 repos — confirm intentional or run /end + new session?"
**Why:** Memory cross-contamination is a stated concern. This is the *one* exception to anti-nag — a single, low-key, easily-dismissed note on a detectably risky pattern.
**Where:** `PreToolUse` hook on Read tool checks repo root of touched path.
**Anti-nag:** Once per session, dismissible, never blocking.

### [B13] Sleep-window-aware task routing (advisory only) — LOW priority
**One-liner:** If owner queries the agent during a window labelled "post-midnight" (configurable per device), `/grill` and `/implement` show a one-line header "outside-quality-window: outputs flagged for AM review" — but they still run.
**Why:** Fucci 2018 sleep finding [20] is real, but Q4 rejected hard gates. This is the soft alternative: tag the output, don't block it; let owner ignore or review fresh.
**Where:** All major skills check `is_quality_window()` and inject header.
**Anti-nag:** Header only, no prompt, no gate.

### [B14] Weekly throughput vs subjective-feel cross-check — LOW priority
**One-liner:** `/reflect` includes "this week vs trailing 4-week avg: PRs merged ±X%, issues closed ±Y%, commits ±Z%" alongside owner's own brief self-rating (if they record one).
**Why:** Wagner-lab finding [5] that subjective performance ≠ actual is the strongest evidence for the multi-stream operator. Closing this loop empirically — over months — gives owner a personal calibration on their own meta-cognition.
**Where:** `/reflect` weekly digest + optional `mood: <1-5>` field in `/end`.

## Passive sustainability dashboard spec (the load-bearing artefact)

What `/reflect` surfaces weekly. Each metric: one line, one number, one comparator. No nags. No interrupts. No quality gates.

| Metric | Description | Comparator |
|---|---|---|
| `repo_switches_per_day` | Count of distinct repos touched per calendar day | This week vs trailing 4w avg |
| `longest_single_repo_stretch_h` | Max contiguous hours in one repo within a session | This week vs trailing 4w avg |
| `hyperfocus_chains` | Count of sessions > 4h with no break > 15min | This week vs trailing 4w avg |
| `days_on_top_stuck_issue` | Days since first commit touching the oldest open issue | Raw value |
| `parallel_session_concurrency` | Peak count of simultaneous active Claude Code sessions | This week peak |
| `abandoned_tasks` | Count of branches with no commit in 14d, no PR opened | Raw value |
| `commit_cadence_gap_days` | Largest gap between commit days in last 30 days | Raw value (probes crash-after-sprint) |
| `sleep_window_session_count` | Count of sessions started in owner-defined post-midnight window | Raw value, no flag |
| `ready_to_resume_coverage` | % of `/end` invocations with ready-to-resume snippet filled | Raw % |
| `throughput_pr_count` | PRs merged this week | Vs trailing 4w avg |

**Display rule:** all metrics in one compact table at the top of `/reflect` output. No traffic lights, no thresholds, no recommendations. Owner reads the numbers, owner decides.

## Sources

[1] Leroy, S. (2009). "Why is it so hard to do my work? The challenge of attention residue when switching between work tasks." Organizational Behavior and Human Decision Processes, 109(2), 168-181. https://www.uwb.edu/business/faculty/sophie-leroy/attention-residue / https://ideas.repec.org/a/eee/jobhdp/v109y2009i2p168-181.html
[2] Leroy, S., & Glomb, T. M. (2018). "Tasks Interrupted: How Anticipating Time Pressure on Resumption of an Interrupted Task Causes Attention Residue and Low Performance on Interrupting Tasks." Organization Science. (Identifies "ready-to-resume" plan as mitigation.)
[3] Ophir, E., Nass, C., & Wagner, A. D. (2009). "Cognitive control in media multitaskers." PNAS 106(37). https://www.pnas.org/doi/10.1073/pnas.0903620106
[4] Wiradhany, W., & Nieuwenstein, M. R. (2017). "Cognitive control in media multitaskers: Two replication studies and a meta-analysis." Attention, Perception, & Psychophysics. https://link.springer.com/article/10.3758/s13414-017-1408-4
[5] Stanford Report (2018). "Heavy multitaskers have reduced memory, psychologist says." https://news.stanford.edu/stories/2018/10/decade-data-reveals-heavy-multitaskers-reduced-memory-psychologist-says
[6] Willison, S. (2025). "Embracing the parallel coding agent lifestyle." https://simonwillison.net/2025/Oct/5/parallel-coding-agents/
[7] Willison, S. (2025). "Claude Code sub-agents." https://simonwillison.net/2025/Oct/11/sub-agents/
[8] Willison, S. (2025). "Code research projects with async coding agents." https://simonwillison.net/2025/Nov/6/async-code-research/
[9] Hashimoto, M. (2025). "My AI Adoption Journey." https://mitchellh.com/writing/my-ai-adoption-journey
[10] TeamDay.ai. "Mitchell Hashimoto's New Way of Writing Code." https://www.teamday.ai/ai/hashimoto-new-way-of-writing-code
[11] Pragmatic Engineer (2025). "Mitchell Hashimoto's new way of writing code." https://newsletter.pragmaticengineer.com/p/mitchell-hashimoto
[12] Super Productivity blog. "Context Switching Is Costing Your Team 6+ Hours a Week." https://super-productivity.com/blog/context-switching-costs-for-developers/
[13] PanDev Metrics. "Context Switching Kills Developer Productivity: Real Data on the 40% Loss." https://pandev-metrics.com/docs/blog/context-switching-kills-productivity
[14] Newport, C. (2024). *Slow Productivity: The Lost Art of Accomplishment Without Burnout*. https://calnewport.com/my-new-book-slow-productivity/
[15] Welcome to the Jungle. "Cal Newport's Slow Productivity: Redefining success in a hustle culture." https://www.welcometothejungle.com/en/articles/cal-newport-slow-productivity-hustle-culture
[16] Levels.io blog. https://levels.io/ / https://levels.io/blog/
[17] FastSaaS (2025). "How Pieter Levels Built a $3M/Year Business with Zero Employees." https://www.fast-saas.com/blog/pieter-levels-success-story/
[18] DEV Community. "The Solo-Founder Playbook: Zero to Hero." https://dev.to/truongpx396/the-solo-founder-playbook-zero-hero-3j7d
[19] Clearing-AI (2025). "AI Fatigue Statistics 2025 — Data on Developer Burnout." https://clearing-ai.com/stats.html
[20] Fucci, D., et al. (2018). "Need for Sleep: the Impact of a Night of Sleep Deprivation on Novice Developers' Performance." arXiv:1805.02544 / IEEE TSE. https://arxiv.org/abs/1805.02544
[21] Loehr, J., & Schwartz, T. (2003). *The Power of Full Engagement*. Summary: https://blog.idonethis.com/science-of-better-energy-management/
[22] Inzlicht, M. "The Collapse of Ego Depletion." https://www.speakandregret.michaelinzlicht.com/p/the-collapse-of-ego-depletion (covers Hagger et al. 2016 23-lab RRR failure of Baumeister's original protocol).
