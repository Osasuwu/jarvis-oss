---
title: Single-agent system vs multi-agent from start — 2026 state
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 22
adjacent_topics_flagged: [skill-vs-mcp-decision-rubric, evaluator-optimizer-loop-for-grill, agent-teams-experimental-flag-fit, ralph-loop-vs-orchestrator-worker, context-engineering-as-first-class-discipline, observability-stack-for-cross-context-subagents]
---

## TL;DR (≤200 words)

**Deepen the single-agent system. Do not pivot to peer multi-agent.** The 2026 evidence is unambiguous for a solo developer:

1. **The field consolidated, not fragmented.** Anthropic, Cognition, OpenAI, Microsoft and LangChain all converged on the same dominant pattern: **one orchestrator with full context + ephemeral, isolated subagents that return compressed summaries**. Peer-to-peer "GroupChat" multi-agent lost. Cognition — who wrote "Don't Build Multi-Agents" in mid-2025 — shipped "Devin manages Devins" in March 2026 *as the same orchestrator+subagent pattern*, not as the peer mesh they'd warned against.
2. **The user is already on the winning architecture.** `/delegate` dispatching GitHub issues to Task-tool subagents and `/grill` invoking a CRITIC subagent **is** the dominant 2026 pattern. The remaining work is depth (better skills, harder hooks, sharper evals, cleaner memory contracts) — not switching frameworks.
3. **Multi-agent costs 4–15× more tokens** for typically marginal-to-negative gains on the user's actual workload (sequential, stateful coding with strong context coherence requirements). Stanford 2026 work (Tran & Kiela) showed single agents match or beat multi-agent at **equal thinking-token budgets** on multi-hop reasoning — prior multi-agent wins were partly artifacts of unequal budgets.
4. **LangGraph stays a selective tool**, not a base layer. Pull it in only for the 1–2 workflows with branching + checkpointing + human-in-the-loop that genuinely outgrow Claude Code skills/hooks.

The 75% confidence on "Claude Code native + Agent Teams + Routines + selective LangGraph" from 2026-04-22 holds. Raise it to ~85%.

---

## Landscape (800-1500 words)

### The 2026 convergence

The defining shift between mid-2025 and mid-2026 is **architectural consolidation around orchestrator+isolated-subagents**. Five major vendors — Anthropic, OpenAI, Cognition, Microsoft (AutoGen/AG2), LangChain — all ship variants of this pattern as their flagship multi-agent story. Hub-and-spoke now reportedly accounts for ~66% of the agentic AI market (Innervation AI, 2026), with free-mesh / GroupChat patterns having lost ground after "From Spark to Fire" research showed a single false statement in a peer mesh could infect 100% of agents.

The most consequential pivot is **Cognition's**. In June 2025, Walden Yan's "Don't Build Multi-Agents" essay (cognition.ai/blog/dont-build-multi-agents) argued multi-agents are fundamentally unreliable: "share context, and share full agent traces, not just individual messages" — the Flappy Bird example where parallel subagents made Mario-style backgrounds for a Flappy-style bird because they didn't share intent. Nine months later (March 2026), Cognition shipped "Devin can now Manage Devins" — *and the justification is the same isolation argument Anthropic made in 2025*. Cognition's 2026 post (cognition.ai/blog/multi-agents-working) identifies three patterns that work: code-review loops with **clean-context reviewers** (~2 bugs/PR, 58% severe), capability-router "smart friend" architectures (weak model calls strong model), and manager+managed delegation (live in Devin today). They did not retract the 2025 essay — they reframed: **single-threaded writes, multi-agent reads**. Anthropic's "Building Effective Agents" already said this in late 2024.

### The Anthropic primary-source picture

Two Anthropic engineering posts are the load-bearing primary sources for a Claude Code user:

- **"Building Effective Agents"** (anthropic.com/research/building-effective-agents): names the five canonical patterns — prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer — and the meta-rule: "Success in the LLM space isn't about building the most sophisticated system. It's about building the *right* system for your needs." Workflows (deterministic) beat agents (model-driven) for predictable subtasks; agents are for genuine open-endedness.
- **"How we built our multi-agent research system"** (anthropic.com/engineering/built-multi-agent-research-system): the load-bearing numbers. Multi-agent Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by **90.2%** on internal research evals. But: **15× the token cost vs chat**, **4× vs single-agent**, **80% of performance variance explained by token usage alone**. Multi-agent wins on breadth-first research; loses on shared-context coding. Failure mode: agents spawning 50+ subagents for trivial queries.

The Claude Code best-practices doc (code.claude.com/docs/en/best-practices) is the operational complement. The whole document is built around **one constraint: context window fills fast and performance degrades as it fills**. Every extension primitive (skills load on demand, subagents have isolated context, hooks run deterministically without consuming context) exists to make a single agent's window last longer. Subagents are framed not as "second agent" but as **context-preservation tooling**: "use subagents to investigate X. They explore in a separate context, keeping your main conversation clean for implementation."

### Extension primitives: Skills vs MCP vs Hooks vs Subagents

The 2026 consensus mental model (ByteByteGo EP213, Duet.so guide, IBM, Anthropic) is a layered four-piece toolkit that turns ONE Claude into a system:

| Primitive | Role | When it loads | Token cost when idle |
|---|---|---|---|
| **CLAUDE.md** | Advisory persistent rules | Every session | Full file every turn |
| **Skills** (SKILL.md) | On-demand workflow modules with phase gates | Triggered by description match or `/name` | Near-zero |
| **MCP servers** | Standardized N×M connector to external systems | Tools registered at session start; bodies on call | Tool descriptions in context |
| **Hooks** | Deterministic shell scripts at lifecycle events | Auto-fire on Pre/Post events | Zero LLM tokens |
| **Subagents** (Task tool) | Isolated-context workers returning summaries | Spawned by parent | Parent only sees summary |

Skills "encode *how to do the job right*"; MCP "provides standardized access"; Tools "execute individual actions"; Hooks "enforce deterministically what prompts only suggest". The Shareuhack 2026 finding is the sharpest empirical point: **skill triggers fire only ~20% of the time alone but reach 84% when paired with hooks**. CLAUDE.md is advisory and Claude can ignore it; hooks cannot be skipped.

### Multi-agent frameworks (2026 production reality)

| Framework | Released | Best for | Cost reality | Solo-dev fit |
|---|---|---|---|---|
| **LangGraph** | 2023, mature 2026 | Branching workflows with HITL + checkpointing + time-travel debug | ~$32/day for 10k 3-agent requests; ~800 tok/req | Pull in selectively when graph state + checkpoints genuinely needed |
| **CrewAI** | 2024, 46k stars | Fastest prototyping; role+goal+backstory | ~$50/day same scale; ~1,250 tok/req (per-agent system prompts) | Good for validation, migrates to LangGraph for prod |
| **AutoGen / AG2** | Microsoft, 2024 | Conversational multi-turn code-gen | Higher token cost from chat iterations | Heavy for solo |
| **OpenAI Agents SDK** | March 2025 | Inside OpenAI ecosystem only | Locked to OpenAI models | Skip if Claude-first |
| **Google ADK** | April 2025 | A2A interop, multimodal | Early-stage prod maturity | Skip |
| **Claude Agent SDK** | Anthropic | Computer use, MCP, safety | Native to Claude | This is what the user is on |
| **ruvnet/claude-flow ("Ruflo")** | Active 2026, 52.5k stars, alpha | "Multi-agent swarms" wrapper over Claude Code | 100+ agents, 32 plugins, vector mem | Marketing-heavy; alpha status; opinionated topology |
| **claude-squad** | smtg-ai, 1.0.17 / Mar 2026 | Terminal app for parallel sessions in worktrees | 7.5k stars | Closest analog to user's `/delegate` already |

LangGraph dominates Python multi-agent (~39M monthly PyPI downloads); CrewAI dominates prototyping (~14.8k monthly searches). LangGraph: cycles, branching, checkpoints, full state inspection. CrewAI: 20 lines for working multi-agent but limited debugging (final output + logs only, no intermediate state). The framework-shopping conclusion across every comparison: **framework choice locks you in for 12–24 months**; start with the simplest tool that solves the actual workflow, not the most flexible one.

### Anthropic's Agent Teams (Feb 2026)

Worth a separate paragraph because it sits directly on the user's path. Released with Opus 4.6 (Feb 2026), Agent Teams enable **peer-to-peer messaging between persistent Claude instances** via a SendMessage tool — explicitly more than subagents (which are hub-and-spoke only). Cost: ~2.25–3× single session for code-review and full-stack feature use cases, up to 7× for complex configurations. Enable via env flag `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. Still experimental. Sweet spot per the docs: full-stack features where frontend + backend + tests need cross-talk; parallel code reviews where reviewers build on each other; competing-hypothesis debugging. **Explicit anti-pattern: sequential tasks.** This is the one place where breaking out of single-agent might earn its keep — but the user has not yet hit the workloads where it would.

### Solo-dev field reports

The honest signal from solo-dev write-ups is mixed-to-negative on jumping straight to multi-agent:

- **Vibecoding.app, Augment Code, Innervation AI**: single-agent handles ~80% of standard use cases. Multi-agent coordination overhead degrades sequential reasoning 39–70% (Google Research). Error propagation 17.2× without orchestration; 4.4× with.
- **Stanford 2026 (Tran & Kiela, arXiv 2604.02460)**: at **equal thinking-token budgets**, single agents match or outperform multi-agent on multi-hop reasoning. Prior wins for multi-agent partly came from API-level budget-control artifacts (especially Gemini 2.5) silently giving multi-agent more tokens.
- **Sentry, Galileo, dev.to writeups**: bugs in multi-agent are *state* bugs at handoff boundaries, not logic bugs in any one agent. "The worst failures look fine" — every span succeeds, output quality silently degrades. Debugging requires 100% trace sampling, per-agent metrics, full prompt+response capture at every boundary. This infrastructure debt is real and growing.
- **Amanda Martin (dev.to)**: a solo workflow optimized for "completeness and momentum" actively *breaks* when ported to a team — the solo agent generates a working MVP that leaves no room for parallel contribution. The reverse also holds: a multi-agent team-shaped workflow leaves a solo dev playing five roles simultaneously.

The pattern: solo devs who jumped to multi-agent frameworks (LangGraph/CrewAI/AutoGen) usually report the **debugging tax** as the dominant pain — five minutes to rebuild mental context every time you need to inspect what an agent did. The solo devs who succeeded mostly chose **deeper single agent + selective subagent dispatch** (which is exactly what the user already runs).

---

## Concrete patterns / recipes (3-7)

### 1. Orchestrator + isolated subagents (the dominant 2026 pattern)
- **Source:** Anthropic multi-agent research post; Cognition "Multi-Agents Working"; converged across 5 vendors.
- **How:** One agent owns full conversation context. Spawns ephemeral subagents with bounded objective + tool subset; subagent works in fresh context window; returns compressed summary; orchestrator integrates. No peer-to-peer messaging.
- **Fit:** Solo dev — this is what `/delegate` and Claude Code's Task tool already do. Optimal for breadth-first research, parallel code review, batched independent fixes.

### 2. Evaluator-Optimizer loop (writer + critic)
- **Source:** Anthropic "Building Effective Agents"; Cognition "code review loop with clean context"; user's own `/grill` CRITIC pattern.
- **How:** One LLM generates, one LLM evaluates with rubric, output loops back until rubric passes. Critical detail (Cognition's 2026 finding): **the reviewer must run with completely clean context** — sharing context with the writer destroys the catch rate. ~2 bugs/PR, 58% severe.
- **Fit:** User already has this in `/grill`. Worth deepening with a structured rubric + the clean-context invariant explicit.

### 3. Routing (lightweight classifier dispatches to specialist workflows)
- **Source:** Anthropic "Building Effective Agents"; LangChain's "supervisor-as-tool" 2026 shift.
- **How:** Small cheap model classifies input → routes to specialized prompt/skill/subagent. Avoids the universal-agent compromise.
- **Fit:** User's skill routing table in CLAUDE.md *is* this pattern, but executed by Claude itself rather than a cheap classifier. For trigger reliability, the Shareuhack finding (~20% unaided, ~84% with hooks) suggests promoting a few skill triggers to hook-driven (PreToolUse / UserPromptSubmit) rather than relying on Claude's pattern match.

### 4. Compound Engineering (EveryInc, plan/work/review/compound)
- **Source:** github.com/EveryInc/compound-engineering-plugin; wotai.co/blog/compound-engineering-agents-md
- **How:** Enforces 80/20 split — 80% in planning + review, 20% execution. Uses `AGENTS.md` loaded *before* CLAUDE.md so process rules shape all downstream prompts. `/ce-compound` writes lessons from finished tasks to `docs/solutions/` for future search. Each unit of work makes the next easier.
- **Fit:** Already partly implemented — `/reason` → `/grill` → `/to-prd` → `/to-issues` → `/implement` *is* compound engineering's plan/work/review loop. Missing piece: structured post-task knowledge-capture loop into a queryable lessons store. User's `record_decision` + outcome enrichment partly covers it.

### 5. Phase-gated skills (skill-as-workflow-module)
- **Source:** Shareuhack 2026, Anthropic skills docs.
- **How:** Skills aren't better prompts — they're workflow modules with phase gates (Red must fail before Green). Each skill loads its instructions only when invoked, costing near-zero context when idle. Phase gates make Claude follow the workflow instead of efficiently shortcutting it.
- **Fit:** User already has this in `/grill`, `/to-prd`, `/to-issues`, `/implement`. The hard-trigger gap (skills fire ~20% unaided) is the operational pain point — pair load-bearing skills with PreToolUse / UserPromptSubmit hooks that detect intent keywords and refuse the turn until the skill is invoked.

### 6. Hub-and-spoke worktree fleet (claude-squad pattern)
- **Source:** smtg-ai/claude-squad; Anthropic worktrees docs.
- **How:** N parallel Claude Code sessions in isolated git worktrees, dashboard view, no inter-session communication. Each session is its own single-agent loop. User chooses what each works on.
- **Fit:** Closest analog to user's `/delegate`. claude-squad would replace `/delegate`'s GitHub-issue dispatch with a richer terminal UI. Not obviously a win over the current GH-issue-driven workflow (which has audit trail, PR structure, verification).

### 7. Smart Friend / capability router (weak primary calls strong specialist)
- **Source:** Cognition "Multi-Agents Working" 2026.
- **How:** A weaker default model handles routine work and calls a stronger model (Opus, GPT-5) for high-leverage subtasks. Cost-optimizes most turns. Cognition explicitly notes this works between frontier models acting as peers, less so weak↔strong pairs (weak struggles to know when to escalate).
- **Fit:** Subagents in Claude Code already support `model: opus | sonnet | haiku` per definition. User could route the bulk of `/delegate` workers to Sonnet/Haiku and reserve Opus for `/grill`'s CRITIC and architecture sweeps.

---

## What this user should consider given his context

The user is not facing a "stay single vs. go multi-agent" decision. The user is **already running the 2026 dominant pattern** — single principal with isolated-subagent dispatch via `/delegate` and `/grill` CRITIC. Everything outside that envelope (LangGraph as base layer, CrewAI for the whole workflow, peer-mesh multi-agent) would be a regression in 2026 terms.

Three high-leverage moves consistent with the evidence:

1. **Close the skill-trigger reliability gap with hooks.** The Shareuhack 2026 finding (~20% skill-fire unaided vs 84% with hooks) maps directly onto the user's CLAUDE.md note that "the empty-`memories_used` rate" escalates Tier 1 → Tier 2. Already the right instinct — formalize it. The same Tier 2 escalation logic applies to `/implement`, `/grill`, `/end`. PreToolUse / UserPromptSubmit hooks that detect intent keywords ("реализуй #", "делегируй") and refuse the turn until the right skill is invoked.

2. **Pair the existing `/grill` CRITIC with the clean-context invariant.** Cognition's 2026 result: the critic must run with **no shared context** with the writer to catch real issues. Verify the current `/grill` CRITIC subagent is spawned with a fresh context (Task tool default — confirm). If it inherits the parent's framing, the catch rate is degraded. This is a free quality win.

3. **Selective LangGraph only for graph-state workflows.** Two candidates worth that overhead: (a) the autonomous-day loop if it grows branching + HITL gates; (b) cross-device routine orchestration when scheduled tasks across devices need state-machine semantics with checkpoints. Everything else stays Claude Code native. The 12–24 month framework lock-in cost is real.

What the user should *not* do: pivot to CrewAI, AutoGen, or Ruflo as the base layer; enable Agent Teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) on the assumption that peer-talk is better — the user's current workloads (issue-driven, sequential, context-sensitive coding) are the *exact* anti-pattern for Agent Teams per Anthropic's own docs.

Confidence on the 2026-04-22 Pillar 7 conclusion ("Claude Code native + Agent Teams + Routines + selective LangGraph"): raise from 75% to ~85%. The Agent Teams component is the only piece worth caveating — keep it as a contingent option for the one workload that earns it (probably full-stack feature work that genuinely spans frontend + backend + tests + DB), not a baseline.

---

## Adjacent topics worth deeper research

- **Skill-vs-MCP decision rubric.** Concrete heuristic for when a capability should be a SKILL.md (workflow, instructions, gates) vs MCP server (live system, typed schema, JSON-RPC). The 2026 ByteByteGo/Duet.so frame is "Skills encode how, MCP provides access" — but the user's `mcp-memory` arguably has skill-shaped contracts. Worth a focused pass.
- **Evaluator-Optimizer loop as a generalizable primitive.** `/grill` is one instance. Are there 2–3 other places (PR body checks? memory-write contracts? decision recording?) where a clean-context critic-with-rubric would catch more than the current single-pass approach?
- **Observability stack for cross-context subagents.** The Sentry/Galileo write-ups on multi-agent debugging argue 100% trace sampling + per-agent prompt+response capture is mandatory once you have >1 subagent in play. The user has `/delegate` and `/grill` CRITIC — that's already past 1. What's the minimum-viable trace store?
- **Ralph-loop vs orchestrator-worker comparison.** The user has a deep-dive on ralph-loop already (`docs/research/deep-dive-ralph-loop-backpressure.md`). Worth explicit comparison vs Anthropic's orchestrator-worker: same pattern? Different cost profile? When does ralph-loop's "one agent in a loop" beat dispatching N subagents?
- **Context engineering as first-class discipline.** Both Cognition (Walden Yan: "At the core of reliability is Context Engineering") and Anthropic ("context is your fundamental constraint") treat this as the load-bearing skill. The user's session-context hook + memory contracts are already in this space but could be more deliberate (CLAUDE.md compaction discipline, when to `/clear`, when to spawn subagent for context preservation).
- **Agent Teams experimental flag — which one workload?** If/when the user wants to test Agent Teams, identify the *single* highest-fit workload first. Best candidates per Anthropic docs: full-stack features with frontend+backend coordination; parallel code review across many files; debugging with competing hypotheses tested simultaneously. Avoid sequential / context-coherent tasks.

---

## Sources

1. Anthropic — [How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)
2. Anthropic — [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
3. Anthropic / Claude Code docs — [Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)
4. Anthropic / Claude Code docs — [Create custom subagents](https://code.claude.com/docs/en/sub-agents)
5. Cognition — [Don't Build Multi-Agents (June 2025)](https://cognition.ai/blog/dont-build-multi-agents)
6. Cognition — [Multi-Agents: What's Actually Working (2026 update)](https://cognition.ai/blog/multi-agents-working)
7. Augment Code — [Single-Agent vs Multi-Agent AI: When to Scale Your Dev Workflow](https://www.augmentcode.com/guides/single-agent-vs-multi-agent-ai)
8. Innervation AI — [Single vs Multi-Agent Architecture 2026 Guide](https://www.innervationai.com/blog/single-vs-multi-agent-architecture-2026-guide/)
9. LaoZhang AI — [Claude Code Agent Teams: The Practical Guide 2026](https://blog.laozhang.ai/en/posts/claude-code-agent-teams)
10. Gurusup — [Best Multi-Agent Frameworks in 2026: LangGraph, CrewAI, AutoGen](https://gurusup.com/blog/best-multi-agent-frameworks-2026)
11. NiteAgent — [Multi-Agent in Production 2026: 3 Patterns That Survived](https://niteagent.com/blog/multi-agent-production-2026/)
12. Markaicode — [LangGraph vs CrewAI: Multi-Agent Performance and Cost in Production 2026](https://markaicode.com/vs/langgraph-vs-crewai-multi-agent-production/)
13. Towards Data Science — [Single Agent vs Multi-Agent: When to Build a Multi-Agent System](https://towardsdatascience.com/single-agent-vs-multi-agent-when-to-build-a-multi-agent-system/)
14. ObviousWorks — [Designing CLAUDE.md correctly: The 2026 architecture that finally makes Claude code work](https://www.obviousworks.ch/en/designing-claude-md-right-the-2026-architecture-that-finally-makes-claude-code-work/)
15. DevelopersDigest — [Claude Code Agent Teams, Subagents, and MCP: The 2026 Playbook](https://www.developersdigest.tech/blog/claude-code-agent-teams-subagents-2026)
16. Shareuhack — [5 Claude Code Skills That Actually Work: Lessons from Running an AI Agent Fleet](https://www.shareuhack.com/en/posts/claude-code-community-skills-agent-fleet-guide-2026)
17. Duet.so — [Agent Skills vs Tools vs MCP: The Complete Guide (2026)](https://duet.so/guides/agent-skills-101-tools-vs-mcp-vs-skills)
18. ByteByteGo — [EP213: MCP vs Skills, Clearly Explained](https://blog.bytebytego.com/p/ep213-mcp-vs-skills-clearly-explained)
19. WotAI — [Compound Engineering + AGENTS.md for Claude Code](https://wotai.co/blog/compound-engineering-agents-md)
20. dev.to (Greza) — [Why Single Agents Beat Multi-Agent Systems at Equal Token Budgets (Stanford Tran & Kiela)](https://dev.to/greza_dev/why-single-agents-beat-multi-agent-systems-at-equal-token-budgets-445c)
21. Sentry Blog — [Debugging multi-agent AI: When the failure is in the space between agents](https://blog.sentry.io/debugging-multi-agent-ai-when-the-failure-is-in-the-space-between-agents/)
22. FlowHunt — [Multi-Agent AI Systems in 2026: What the Research Actually Says](https://www.flowhunt.io/blog/multi-agent-ai-system/)
23. Vibecoding.app — [Multi-Agent vs Single-Agent Coding: Data-Driven Comparison (SWE-bench Verified)](https://vibecoding.app/blog/multi-agent-vs-single-agent-coding)
24. dev.to (Amanda Martin) — [Why your solo agent workflow breaks down in a team build](https://dev.to/amandamartindev/why-your-solo-agent-workflow-breaks-down-in-a-team-build-k1m)
25. Firecrawl — [Best Claude Code Skills to Try in 2026](https://www.firecrawl.dev/blog/best-claude-code-skills)
26. Medium (Maureese Williams) — [The Agent Architecture Wars: Why Two AI Giants Completely Disagree on Multi-Agent Systems](https://medium.com/@maureesewilliams/the-agent-architecture-wars-why-two-ai-giants-completely-disagree-on-multi-agent-systems-d19a53364200)
27. GitHub — [ruvnet/claude-flow (Ruflo)](https://github.com/ruvnet/claude-flow)
28. GitHub — [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad)
