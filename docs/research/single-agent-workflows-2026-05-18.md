---
title: Single-agent workflows — popular patterns 2026
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 28
adjacent_topics_flagged:
  - dreaming-as-a-self-improve-primitive
  - outcomes-rubrics-as-eval-harness
  - claude-code-routines-vs-jarvis-autonomous-loop
  - claude-code-router-multi-provider-routing
  - sandcastle-as-managed-worktree-substrate
  - speckit-extensions-and-presets-system
  - awesome-claude-code-curation-decay
  - context-rot-attention-budget-research
  - auto-mode-permission-classifier
  - checkpointing-vs-git
---

## TL;DR (≤200 words)

You're already on the canonical single-agent direction the field converged on in 2026: small composable skills, explicit `/grill`-style alignment before code, TDD as the feedback loop, vertical slices, plan/execute/clear context discipline, CLAUDE.md + SOUL.md split, and MCP-based memory. This is essentially Matt Pocock's `aihero.dev` thesis verbatim, plus Geoffrey Huntley's "stdlib" pattern, plus Harper Reed's spec → plan → execute loop, plus Anthropic's official best-practices doc.

Three concrete additions would move the needle most: **(1) install `superpowers@claude-plugins-official`** as a separate experiment and steal what it does better — particularly the *Iron Laws + red-flag rationalizations* pattern, the brainstorming gate, and the verification-before-completion skill (your /grill is close; theirs is more enforcement-flavoured). **(2) Adopt Anthropic's "outcomes" rubric** for big tasks — it's the eval/rubric layer your `/verify` is missing. **(3) Adopt Simon Willison's "linear walkthrough" pattern** as a `/zoom-out` upgrade — it forces the agent to extract real code via `sed`/`grep` instead of hallucinating snippets.

Don't bolt on Spec-Kit or Claude-Flow/Ruflo — they're heavier than your stack and would fight your `/to-prd` + `/to-issues` chain.

## Landscape (≈1,400 words)

**Definition.** "Single-agent workflow" in May 2026 means: one Claude Code session (or one Codex/Cursor/Gemini-CLI session), running an agentic loop with tools, augmented by markdown-defined Skills, deterministic Hooks, MCP servers for state/memory, and slash-commands for repeatable rituals. The competing paradigm — multi-agent orchestration (Ruflo, agent-teams, Sandcastle fan-out) — sits on top of the single-agent substrate; almost everyone agrees you master one agent first.

**Anthropic's official posture (May 2026).** The canonical doc is `code.claude.com/docs/en/best-practices` [1] — recently moved from anthropic.com. It crystallises five rules: give Claude verification, explore-then-plan-then-code, provide specific context, configure environment (CLAUDE.md + hooks + skills + subagents + MCP + plugins), manage session aggressively (`/clear`, `/rewind`, subagents-for-investigation). The companion piece is `/effective-context-engineering-for-ai-agents` [2] — the "context rot / attention budget / just-in-time retrieval / structured note-taking / sub-agent architectures" framework that is now the lingua franca for everyone writing about this space. At Code w/ Claude 2026 [3] Anthropic shipped: **outcomes** (rubric grader, +8–10pp task success on document gen), **dreaming** (overnight memory consolidation, research preview), **routines** (higher-order async prompts → wake-up-to-merged-PRs), **multi-agent orchestration** in Managed Agents [4], **auto mode** permission classifier, **CI auto-fix**, and **remote agents** (phone → laptop control). The headline was "no new model" — all leverage is in the harness now.

**Skills marketplaces and the four canonical bundles.** The skill format (`SKILL.md` with YAML frontmatter `name` + `description`, plus optional bundled scripts/refs/assets) was released as an open standard in December 2025; in May 2026 there are four dominant bundles:

1. **`anthropics/skills`** [5] — 136k stars, 16k forks. Official. Ships document skills (`pdf`, `docx`, `pptx`, `xlsx`), `skill-creator`, `consolidate-memory`, `webapp-testing`, `frontend-design`, `mcp-server-dev` family. Source-available for the document set, MIT for the rest.
2. **`obra/superpowers`** [6] — Jesse Vincent's framework. ~174k stars in seven months; ~682k Claude plugin installs [7]. Promoted into the official Anthropic plugin marketplace (Jan 2026). 14 SKILL.md files + a ≤2k-token session-bootstrap hook. The 13 named skills cluster around brainstorming, writing-plans, using-git-worktrees, subagent-driven-development, TDD (red-green-refactor with "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST" Iron Law), systematic-debugging (4-phase), verification-before-completion, requesting-/receiving-code-review, finishing-a-development-branch, dispatching-parallel-agents, writing-skills, and the meta `using-superpowers` dispatcher that runs a skill-check on *every* user message [8].
3. **`mattpocock/skills`** [9] — ~87k stars. 12 skills, explicitly *not* a framework: `grill-me`, `grill-with-docs`, `tdd`, `diagnose`, `to-prd`, `to-issues`, `triage`, `zoom-out`, `improve-codebase-architecture`, `prototype`, `caveman`, `setup-matt-pocock-skills`. Philosophy [10]: "small composable prompts keep the process visible" vs heavyweight Spec-Kit/BMAD/GSD that "take control away from the engineer." Three named failure modes — misalignment, verbosity, broken code — each mapped to one skill.
4. **`github/spec-kit`** [11] — ~90k stars. Now uses prefixed slash commands `/speckit.constitution`, `/speckit.specify`, `/speckit.clarify`, `/speckit.plan`, `/speckit.tasks`, `/speckit.implement`, `/speckit.analyze`, `/speckit.checklist`. Heavier than Pocock's bundle; installed via `uv tool install specify-cli` + `specify init`. Visual Studio Magazine [from search results] frames it as "antidote to piecemeal vibe coding."

Beyond these four, there's a curation tier: `VoltAgent/awesome-agent-skills` (1000+ skills, vendor-organised) [12], `hesreallyhim/awesome-claude-code` (44k stars, currently between organisational systems), `travisvn/awesome-claude-skills`, `ComposioHQ/awesome-claude-skills`. Vendor-published skills worth knowing: Vercel Labs (`web-design-guidelines`, `vercel-react-best-practices`, `composition-patterns` — 57 perf rules), Trail of Bits (21 security skills incl. constant-time analysis, semgrep-rule-creator), Firecrawl (scrape/search/browser), Remotion (programmatic video), Microsoft Azure (133+ across 6 languages), Sentry (30+ SDK skills) [13].

**Practitioner voices (primary sources).** **Geoffrey Huntley** is the source of the *stdlib* idea — a per-developer or per-org library of reusable rules ("how" docs) that compose with project-specific specs ("what" docs). The Ralph Wiggum Loop [14] (`while :; do cat PROMPT.md | claude-code ; done`) is his viral bash pattern; HumanLayer's brief history [15] documents the evolution and Anthropic's official Stop-Hook/Completion-Promise plugin codification (which Huntley criticised for losing the "small bits of work into independent context windows" principle). Huntley's `/dothings/` post [16] crystallises three reinforcement mechanisms: strong type system, high test coverage, fast feedback loops. **Henry's "Driving Claude Code"** [17] generalises this to *stdlib + spec + autonomous-loop* — note the prompt "Implement what is not implemented" and the "spin up a fresh agent with the same prompt on token-limit" pattern.

**Matt Pocock's `aihero.dev`** [18][19] is the methodology the user already adopted. Core concepts: **smart zone** (~100k tokens before degradation), **deep modules** vs ball-of-mud, **Plan/Execute/Clear** as a context-hygiene loop, **PRD-driven tracer-bullet planning**, **CONTEXT.md** for domain terminology that reduces verbosity, the "engineer's path" of 7 higher-order skills. His "Real World Feature Build" video (cohort `claude-code-for-real-engineers-2026-04`) walks the full chain. **Harper Reed's** [20][21] workflow is the canonical three-phase greenfield recipe: idea-honing via conversational LLM asking "one question at a time," planning via reasoning model producing `spec.md` + `prompt_plan.md` + `todo.md`, execution via Claude Code with a meta-prompt that references `@prompt_plan.md` and types "continue/yes" through. The minimal-CLAUDE.md, pre-commit-hook, "robots LOVE TDD" tenets are his.

**Simon Willison's** *Agentic Engineering Patterns* guide [22][23] (5 chapters as of Feb–May 2026): "Writing code is cheap now," "Red/green TDD," "First run the tests," "Linear walkthroughs," "Hoard things you know how to do." The Linear Walkthrough pattern [24] is operationally interesting — agent extracts snippets via shell tools (`sed`, `grep`, `cat`) rather than re-typing from memory, eliminating hallucinated code samples. His April 2026 post on Claude Code quality reports [25] documents the harness-bug-not-model-quality post-mortem and underscores why studying the harness > studying the model.

**The hook ecosystem.** Anthropic ships 12+ hook lifecycle events: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse` (only hook that can block — exit code 2), `PostToolUse`, `PostToolUseFailure`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `Notification`, `PreCompact`, `PermissionRequest` [26][27]. The dominant reference repo is `disler/claude-code-hooks-mastery` [27]. Canonical patterns: auto-format on edit (PostToolUse), block dangerous commands (PreToolUse), inject git/branch context (SessionStart), audit/log every prompt (UserPromptSubmit), transcript backup before compaction (PreCompact). The official doc emphasises hooks for things that "must happen every time with zero exceptions" — i.e. deterministic enforcement vs. advisory CLAUDE.md.

**MCP servers — the canonical short list.** Multiple 2026 round-ups [from search results: nimbalyst, bannerbear, codersera, apidog, data-mania] converge on: GitHub (highest impact, turns Claude into PR/issue participant), Postgres/Supabase (DB exploration + queries), Playwright (browser automation, Figma-to-code-to-test pipelines), Sentry (errors-into-context), filesystem, slack/notion/linear for context bridges. Consensus ceiling: 4–6 servers, not 15 — tool-list bloat degrades selection.

**Substrate tools.** `claude-code-router` [28] proxies CC requests to OpenRouter/DeepSeek/Ollama/Gemini etc — relevant for cost optimisation if you ever break out of Max subscription. `mattpocock/sandcastle` [from search results] is a TS library to orchestrate sandboxed agents (Docker/Podman/Vercel providers, three branch strategies, `sandcastle.run()` API) — but it's for *running* multiple agents, not enhancing one. `claude-flow`/`ruflo` is heavier multi-agent — not relevant for single-agent users.

## Concrete patterns / recipes (7 patterns)

### 1. Superpowers' "Iron Laws + red-flag rationalizations" enforcement pattern
Source: `obra/superpowers` SKILL files [6][8].
**How it works:** Each high-stakes skill (TDD, verification-before-completion, systematic-debugging) declares an Iron Law in caps ("NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"), then enumerates the *specific rationalizations the agent will reach for to bypass it* ("tests passing on first run," "just this once," "this is a trivial change"). The `using-superpowers` meta-skill runs a skill-check on every user message and creates todo lists from skill checklists — i.e. it's a dispatcher that forces other skills to actually fire instead of being theoretically available.
**Who it suits:** Anyone whose agent "knows the right practice but does the shortcut" — i.e. everyone. Particularly useful when the single agent runs unattended.

### 2. Matt Pocock's "skills as failure-mode fixes" mapping
Source: `aihero.dev/5-agent-skills-i-use-every-day` [19].
**How it works:** Don't write a skill until you've named a recurring failure mode. Pocock's three: misalignment (fix with `/grill-me` + `CONTEXT.md` domain terminology), verbosity (fix with `caveman` + shared vocab), broken code (fix with feedback loops — types, `/tdd`, `/diagnose`). Each skill is one paragraph of instruction, no bundled scripts. Composable; skills don't call each other.
**Who it suits:** Solo devs who want a hackable, visible system — explicitly the opposite of Spec-Kit's heavyweight approach.

### 3. Harper Reed's three-doc spec→plan→execute loop
Source: `harper.blog/2025/02/16/my-llm-codegen-workflow-atm/` [20].
**How it works:** (a) Idea-honing with conversational LLM, prompt: *"Ask me one question at a time so we can develop a thorough, step-by-step spec for this idea."* Save as `spec.md`. (b) Reasoning model emits `prompt_plan.md` (executable prompts) and `todo.md` (checklist). (c) Claude Code with meta-prompt that references `@prompt_plan.md`, implements next incomplete prompt, runs tests, commits, updates plan, pauses for user. User mostly types "continue."
**Who it suits:** Greenfield projects. Maps almost 1:1 onto Jarvis's `/grill → /to-prd → /to-issues → /implement` chain — Pocock formalised it as skills, Reed runs it manually.

### 4. Huntley's stdlib + specs composition
Source: `ghuntley.com/dothings/` [16] + Henry's "Driving Claude Code Part 1" [17].
**How it works:** Maintain a personal/org-level *stdlib* of reusable rules independent of any project — code style, framework choices, deployment patterns, common gotchas. Per-project *specs* answer "what." At session start the agent reads both, then "Implement what is not implemented." Token-limit recovery: spin up a fresh agent with the same prompt; the docs reload deterministically.
**Who it suits:** Developers with consistent style/stack across many projects. The user's `.claude-userlevel/CLAUDE.md` mirror IS a stdlib in this sense.

### 5. Simon Willison's Linear Walkthrough
Source: `simonwillison.net/guides/agentic-engineering-patterns/linear-walkthroughs/` [24].
**How it works:** Prompt the agent to *plan* a linear walkthrough of code first, then *extract snippets via shell* (`sed`, `grep`, `cat`) into a `walkthrough.md` — never via memory. Eliminates the "agent confidently quotes code that doesn't exist in the file" failure. Useful for re-onboarding to your own vibe-coded project.
**Who it suits:** Anyone returning to dormant code; anyone whose agent hallucinates exact line content.

### 6. Plan/Execute/Clear context-hygiene loop
Source: Pocock's `claude-code-for-real-engineers` cohort [18] + Anthropic best-practices doc [1].
**How it works:** Three phases per task. **Plan** in plan-mode (`Ctrl+G` opens plan in editor), separating exploration from coding. **Execute** in default mode with verification (tests/screenshots) baked in. **Clear** with `/clear` between unrelated tasks; `/rewind` to restore checkpoints when you go off-track; subagents for investigation so research never pollutes main context. Customise compaction in CLAUDE.md: `"When compacting, always preserve the full list of modified files."`
**Who it suits:** Everyone. The Anthropic doc lists "kitchen sink session" and "correcting over and over" as the two most common failure patterns — both solved by `/clear`.

### 7. Outcomes-as-rubric eval pattern
Source: `claude.com/blog/new-in-claude-managed-agents` [4].
**How it works:** Define explicit success criteria as a rubric; a separate grader model evaluates output; agent self-corrects. Anthropic reports +8.4–10.1pp task-success improvements on document-generation. Lighter version for single-agent CC users: write a `RUBRIC.md` per task, instruct the agent to grade its own output against it before declaring done. Stronger version: pipe through `claude -p` in headless mode with the rubric as judge.
**Who it suits:** Anyone whose `/verify` skill is currently "did tests pass" rather than "did the work meet the spec." This is the eval-harness layer the practitioner ecosystem is still building.

## What this user should consider given his context

You're not behind the field — your `/grill`, `/to-prd`, `/to-issues`, `/implement`, `/verify`, `/diagnose`, `/improve-codebase-architecture`, `/end`, `/zoom-out`, `/caveman` set is essentially Matt Pocock's bundle plus a memory layer. SOUL.md + CONTEXT.md + CLAUDE.md three-way split is *more disciplined* than what most practitioners run. Your Supabase MCP memory is ahead of `consolidate-memory` (which is local-file). Your SessionStart hook with `session-context.py` injecting goals + working-state + memory catalog is the same pattern Anthropic recommends but better-instrumented.

**Where you're behind / would benefit:**

- **No Iron-Laws-style enforcement copy in skill files.** Your `/grill` and `/implement` are contract-flavoured but don't enumerate the *specific rationalizations* the agent uses to bypass them. Steal the Superpowers pattern — for each Tier-1 rule, list the 3–5 rationalizations it will rationalise with. Costs you 50 lines per skill; closes the "knows but doesn't" gap.
- **No outcomes/rubric layer.** `/verify` checks PR merge + test results — that's task-completion, not work-quality. Adopt the rubric grader pattern for slices where capability quality matters (e.g. eval results for the sycophancy harness you just landed in #697).
- **No Linear Walkthrough analog.** `/zoom-out` is a higher-level map; add a `/walkthrough` variant that forces shell-extracted snippets when you return to dormant code (`mcp-memory/server.py` would be a beneficiary).
- **You haven't installed `superpowers@claude-plugins-official`.** Even if you don't adopt it, install it on Lenovo as a one-device experiment and read the skill files — they're well-written and short. Worth 30 minutes.
- **Hook coverage gap.** You have SessionStart, UserPromptSubmit, PreToolUse. Consider `PreCompact` to snapshot working_state before compaction, and `PostToolUseFailure` to log into outcomes — both are deterministic and would feed `/reflect`.

**Where you're ahead:** decision records with UUIDs, brief-mode recall map, milestone-hierarchy memory, the always-load rules pattern, three-way doc split, source_provenance enforcement. Don't undo these to fit anyone else's framework.

**Don't adopt:** Spec-Kit (heavier than your chain, would fight `/to-prd`), Claude-Flow/Ruflo (multi-agent swarm, not your problem), claude-code-router (you're on Max — no cost incentive), Sandcastle (you don't need parallel sandboxed runs).

## Adjacent topics worth deeper research

- **Dreaming as a self-improve primitive** — Anthropic's overnight memory-consolidation feature is conceptually identical to `/self-improve` + `consolidate-memory`. Worth a dedicated research bundle on how to wire it (or its lighter equivalent) into your nightly autonomous-loop.
- **Outcomes rubrics as eval harness** — separate from coding agents, this is the missing piece for evals beyond unit tests. Pair with the sycophancy harness work from #697.
- **Claude Code Routines vs Jarvis autonomous-loop** — Anthropic's routines are higher-order async prompts producing merge-ready PRs by morning. Compare/contrast with your scheduled-task autonomous-loop architecture; potential consolidation.
- **claude-code-router for multi-provider routing** — not relevant *now* but worth a bookmark if you ever exhaust Max or want a cheap-model background path.
- **Sandcastle as managed-worktree substrate** — your `.sandcastle/worktrees/` naming already echoes it; worth checking whether you should adopt the TS lib instead of rolling your own.
- **Spec-Kit's extensions and presets system** — even if you don't adopt Spec-Kit, the extensions/presets concept (override templates without forking) is a pattern your skills could use.
- **`awesome-claude-code` curation decay** — `hesreallyhim`'s 44k-star list is between organisational systems; signal-to-noise across the awesome-* repos is dropping. Worth a curated subset for your CLAUDE.md.
- **Context rot / attention budget research** — Anthropic's "effective context engineering" post [2] references academic work on n² pairwise relationships in transformers; worth surfacing for the `smart zone` operationalisation.
- **Auto mode permission classifier** — Sonnet-4.6-based classifier that gates actions. Could replace some of your hook-based permission logic but has the "may still allow some risky actions" caveat.
- **Checkpointing vs git** — `/rewind` and Claude's pre-edit snapshots aren't a git replacement but are useful mid-session. Doesn't currently integrate with your working_state pattern.

## Sources

1. **Anthropic — Best practices for Claude Code** (May 2026). https://code.claude.com/docs/en/best-practices — official, recently moved from anthropic.com/engineering. Five canonical rules + workflow recipes.
2. **Anthropic — Effective context engineering for AI agents** (2026). https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — context rot, attention budget, just-in-time retrieval, structured note-taking.
3. **Simon Willison — Live blog: Code w/ Claude 2026** (May 6, 2026). https://simonwillison.net/2026/May/6/code-w-claude-2026/ — outcomes, dreaming, routines, remote agents, auto mode announcements.
4. **Anthropic — New in Claude Managed Agents** (2026). https://claude.com/blog/new-in-claude-managed-agents — outcomes (+8.4–10.1pp), dreaming research preview, multi-agent orchestration.
5. **anthropics/skills** (GitHub, 136k stars). https://github.com/anthropics/skills — official skill standard, document skills, skill-creator, template.
6. **obra/superpowers** (GitHub, ~174k stars). https://github.com/obra/superpowers — Jesse Vincent's framework, 13 skills, Iron Laws.
7. **Anthropic plugin marketplace — Superpowers** (2026). https://claude.com/plugins/superpowers — 681,792 installs, featured.
8. **Claude Code Marketplaces — Using Superpowers**. https://claudemarketplaces.com/skills/obra/superpowers/using-superpowers — dispatcher mechanics, skill-check every message.
9. **mattpocock/skills** (GitHub, ~87k stars). https://github.com/mattpocock/skills — 12 skills, anti-framework philosophy.
10. **Implicator.ai — Matt Pocock Skills Repo Passes 45K Stars** (May 2026). https://www.implicator.ai/matt-pocock-skills-repo-jumps-past-45k-stars-with-reusable-ai-instructions/ — Pocock's stated critique of Spec-Kit/BMAD/GSD.
11. **github/spec-kit** (GitHub, ~90k stars). https://github.com/github/spec-kit — `/speckit.*` commands, six phases, `uv tool install specify-cli`.
12. **VoltAgent/awesome-agent-skills** (GitHub). https://github.com/VoltAgent/awesome-agent-skills — 1000+ vendor-organised skills.
13. **Firecrawl blog — Best Claude Code Skills to Try in 2026**. https://www.firecrawl.dev/blog/best-claude-code-skills — Vercel Labs, Trail of Bits, Firecrawl, Remotion, Corey Haines marketing.
14. **Geoffrey Huntley — Ralph Wiggum as a "software engineer"** (Jul 2025). https://ghuntley.com/ralph/ — `while :; do cat PROMPT.md | claude-code ; done`, single-task-per-loop principle.
15. **HumanLayer — A Brief History of Ralph** (2026). https://www.humanlayer.dev/blog/brief-history-of-ralph — evolution, Anthropic Stop-Hook codification, Huntley's critique.
16. **Geoffrey Huntley — The future belongs to people who can just do things**. https://ghuntley.com/dothings/ — stdlib pattern, three reinforcement mechanisms (types/tests/feedback loops).
17. **Henry — How I've Been Driving LLMs Part 1**. https://henryneeds.coffee/blog/driving-claude-code-part-1/ — stdlib + spec + autonomous-loop generalisation, "Implement what is not implemented" prompt.
18. **Matt Pocock — Claude Code for Real Engineers cohort** (Apr 2026). https://www.aihero.dev/cohorts/claude-code-for-real-engineers-2026-04 — smart zone, deep modules, plan/execute/clear, PRD-driven tracer bullets, 7 higher-order skills.
19. **Matt Pocock — 5 Agent Skills I Use Every Day**. https://www.aihero.dev/5-agent-skills-i-use-every-day — `/grill-me`, `/to-prd`, `/to-issues`, `/tdd`, `/improve-codebase-architecture` mapped to failure modes.
20. **Harper Reed — My LLM codegen workflow atm** (Feb 16, 2025). https://harper.blog/2025/02/16/my-llm-codegen-workflow-atm/ — `spec.md` + `prompt_plan.md` + `todo.md`, idea-honing prompt, TDD prompt.
21. **Harper Reed — Basic Claude Code** (May 8, 2025). https://harper.blog/2025/05/08/basic-claude-code/ — minimal CLAUDE.md, pre-commit hooks, "robots LOVE TDD," meta-prompt loop.
22. **Simon Willison — Agentic Engineering Patterns** (substack, Feb 2026). https://simonw.substack.com/p/agentic-engineering-patterns — five chapters: writing-code-is-cheap, red/green TDD, first run the tests, linear walkthroughs, hoard things you know how to do.
23. **Simon Willison — Vibe coding and agentic engineering are getting closer than I'd like** (May 6, 2026). https://simonwillison.net/2026/May/6/vibe-coding-and-agentic-engineering/ — distinction collapse, value of real-world usage over pristine reviewable code.
24. **Simon Willison — Linear walkthroughs guide chapter**. https://simonwillison.net/guides/agentic-engineering-patterns/linear-walkthroughs/ — shell-extracted snippets via sed/grep/cat, no memory-based code quoting.
25. **Simon Willison — An update on recent Claude Code quality reports** (Apr 24, 2026). https://simonwillison.net/2026/Apr/24/recent-claude-code-quality-reports/ — harness-bug-not-model-quality post-mortem, why study the harness.
26. **Anthropic — Hooks reference**. https://code.claude.com/docs/en/hooks — 12+ lifecycle events, exit-code-2 blocking semantics.
27. **disler/claude-code-hooks-mastery** (GitHub). https://github.com/disler/claude-code-hooks-mastery — canonical hook reference repo, 13 hooks demonstrated, popular patterns.
28. **musistudio/claude-code-router** (GitHub). https://github.com/musistudio/claude-code-router — proxies CC to OpenRouter/DeepSeek/Ollama/Gemini etc, cost-optimisation use case.

Bonus (not numbered in body but consulted):
- **Marc Nuri — Superpowers: The Claude Code Skills Framework Shipped as Markdown**. https://blog.marcnuri.com/superpowers-claude-code-skills-framework — Iron Laws + red-flag rationalizations writeup, multi-host portability.
- **mattpocock/sandcastle** (GitHub). https://github.com/mattpocock/sandcastle — TS sandbox orchestration, `sandcastle.run()` API.
- **GitHub Blog — Spec-driven development with AI**. https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/ — Spec-Kit launch context.
- **BuildBetter — AGENTS.md Complete Guide for Engineering Teams 2026**. https://blog.buildbetter.ai/agents-md-complete-guide-for-engineering-teams-in-2026/ — AGENTS.md as cross-tool standard (28k+ repos adopted).
