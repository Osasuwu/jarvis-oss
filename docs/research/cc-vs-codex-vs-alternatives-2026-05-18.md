---
title: Claude Code vs Codex vs alternatives — 2026 H1 honest assessment
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 26
adjacent_topics_flagged:
  - "ACP (Agent Client Protocol) as multi-tool MCP-like layer in Zed"
  - "Skill-to-skill portability: cc2codex / claude2codex tooling maturity"
  - "Cost economics: 3-4x token-efficiency gap on identical tasks"
  - "Postmortem-class incidents as recurring risk (Anthropic April 23 case)"
  - "GPT-5.5 vs Opus 4.7 reasoning depth on ambiguous-spec tasks"
  - "Subagent orchestration models: cloud-async (Codex) vs local-worktree (CC)"
---

## TL;DR (≤200 words)

**Stay on Claude Code, with a hybrid Codex side-channel for two specific slices.** Don't migrate.

Codex genuinely got better in 2026 H1 and a real (not hype) migration is happening — but the migration is driven by users whose investment in Claude Code was shallow (CLAUDE.md + a few MCP servers). Your situation is the opposite: 40+ skills, custom MCP memory server, three-way doc split, hook lattice, SessionStart auto-loader, Telegram bridge, sandcastle worktrees. The migration guides (Blake Crosley, Pasquale Pillitteri, cc2codex tool) all confirm: **Codex hooks are fewer, skill semantics differ, and "wholesale port" is explicitly the wrong pattern**. The author of the most-cited migration guide had 84 hooks + 48 skills + 19 agents and chose to restructure, not port.

What you should actually do: (1) keep Claude Code as primary — Opus 4.7 wins on extensibility, code-quality blind reviews (67% vs 25%), 1M context, and your existing investment; (2) install Codex CLI alongside for **bulk parallel mechanical work** (batch refactors, DevOps scripts, CI fixes) where its 3-4× token efficiency and Terminal-Bench 2.0 lead (82.7% vs 69.4%) matter; (3) treat the April 2026 Anthropic postmortem as a *cost of being on the frontier*, not a reason to defect — it's resolved and the gradual-rollout fix landed.

## Landscape (deep dive)

### The "people are switching to Codex" story — is it real?

**Yes, but smaller than the noise suggests, and the cohort is specific.**

What's actually happening (sourced from HN [46391391](https://news.ycombinator.com/item?id=46391391), the [Anthony Maio essay](https://anthonymaio.substack.com/p/codex-got-better-because-claude-code), the [Reddit-500 dev survey](https://dev.to/_46ea277e677b888e0cd13/claude-code-vs-codex-2026-what-500-reddit-developers-really-think-31pb)):

- **Trigger event**: Anthropic shipped three overlapping product bugs Mar 4 – Apr 20, 2026 that degraded Claude Code quality for ~6 weeks. Detailed in [Anthropic's April 23 postmortem](https://www.anthropic.com/engineering/april-23-postmortem): (1) reasoning effort silently downgraded high→medium Mar 4; (2) caching bug Mar 26 cleared thinking history every turn; (3) Apr 16 system-prompt change capped responses at ≤25/100 words and cost 3% on coding evals. All resolved by Apr 20 (v2.1.116), usage limits reset for all subscribers.
- **Stella Laurenzo's audit** of 6,852 sessions + 234,760 tool calls showed thinking depth fell 67%, read-to-edit ratio dropped 6.6→2.0, and edits-without-prior-read rose 6.2%→33.7% during the incident window.
- **Reddit-500 survey paradox**: 65% of developers say they prefer Codex day-to-day, but blind code reviews rate Claude Code's output cleaner **67%** of the time vs Codex's **25%**. People are choosing the worse code for speed, autonomy, and not hitting rate limits — not because Codex writes better code.
- **HN sentiment is genuinely split ~40/40/20** (Claude / Codex / pragmatist). The "everyone's moving to Codex" narrative is amplified by ~5 loud accounts and the postmortem incident; one HN commenter explicitly called out: *"This blog post is the only place I've seen people 'raving' about codex."*

The real causal story Maio nails (May 6, 2026): "*A coding agent that reads before editing, follows the plan, respects the repo, and fails in boring ways will beat a genius model wrapped in unstable defaults.*" The defection wasn't about Codex catching up to Opus 4.7's capability — it was about Anthropic spending six weeks breaking trust through silent product changes.

### Claude Code (Anthropic, May 2026)

- **Models**: Opus 4.7 (default fast mode), Sonnet 4.6. xhigh reasoning effort on Opus restored Apr 7.
- **Context**: 1M tokens (Opus 4.7).
- **Pricing**: $20 Pro / $100 Max 5x / $200 Max 20x. Claude Max covers heavy daily use; rate limits **doubled** in May 2026.
- **Extensibility**: skills (global at `~/.claude/skills/` and project-local), hooks (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop), MCP (stdio + HTTP), subagents with shared task lists + git worktrees, plugins via marketplace.
- **April–May 2026 ships** ([changelog](https://code.claude.com/docs/en/changelog)): forked subagents on external builds (Apr 22), agent frontmatter MCP loading, persisted model selections, `/resume` summary for stale sessions, plugin marketplace deps handling, worktree background isolation options, stronger plugin dep handling, fast-mode default to Opus 4.7.
- **Differentiator**: deepest extensibility stack of any agent in 2026; tightest IDE+CLI parity; 1M context window for monorepo work.
- **Weakness**: rate-limit hit rate on heavy use (Max needed for >2 hrs/day), centralized control plane (Anthropic can silently change defaults), [postmortem-class incidents are now a known risk profile](https://fortune.com/2026/04/24/anthropic-engineering-missteps-claude-code-performance-decline-user-backlash/).

### OpenAI Codex CLI (May 2026)

- **Repo**: [openai/codex](https://github.com/openai/codex), Rust 96.2%, Apache 2.0, latest stable v0.130.0 (May 8), v0.131.0-alpha.22 (May 15).
- **Models**: GPT-5.5 default (Apr 23, 2026 release), GPT-5.4, GPT-5.3-Codex-Spark (ChatGPT Pro preview), `/model` mid-session switching, `--oss` flag for local Ollama.
- **Context**: 200K – 400K tokens depending on source; smaller than Claude Code.
- **Pricing**: $20 ChatGPT Plus / $200 Pro tiers, **no separate sub cost** if you already have ChatGPT. API: $1.50/M input + $6/M output for `codex` model + 75% prompt-cache discount — significantly cheaper than Opus 4.6's $3/$15.
- **Extensibility**:
  - **Skills** since Dec 2025 (`~/.agents/skills/SKILL.md`, YAML frontmatter, auto-loaded on task match).
  - **AGENTS.md** project rules (Codex's CLAUDE.md equivalent), layered from root down.
  - **MCP** stdio + streaming HTTP, `codex mcp add/list/remove`, OAuth login flow.
  - **Hooks** (since v0.116.0, Mar 19): user-prompt hook is the headline; fewer lifecycle events than Claude Code, no always-on ordering guarantees per migration guides.
  - **Subagents** (GA Mar 2026): manager-worker pattern, parallel up to `agents.max_threads` (default 6 — note: morphllm reports up to 8 cloud sandboxes), `agents.max_depth=1` (no recursive delegation), wait-all-then-consolidate (no pipelining), inherit sandbox + approval choices from parent.
  - **Plugins**: marketplace via `plugin.json`, bundles skills+MCP+agents.
- **Sandbox**: OS-kernel level (Seatbelt on macOS, Landlock on Linux), three modes (`read-only` / `workspace-write` / `danger-full-access`). Codex's sandbox is **architecturally stronger** than Claude Code's app-layer permissions.
- **Headless**: `codex exec` (alias `e`) for CI, `--json` newline-delimited output, `--output-last-message` to file, `--ask-for-approval never` for full automation.
- **Differentiator**: open-source CLI in Rust, kernel-level sandbox, cheaper per-token, GPT-5.5's Terminal-Bench 2.0 lead (82.7% vs 69.4%), strong defaults out of the box.
- **Weakness**: Windows experimental (WSL2 recommended), ecosystem ~2 years behind Claude Code on skills/community, no `Stop`/`PreToolUse`/`SessionStart`-equivalent hook ordering, subagent visibility weak in IDE extension.

### Cursor (cursor.sh, May 2026)

- **Models**: Composer (own model, Oct 2025, 4× faster than peers), Claude Opus 4.x, GPT-5.x, Gemini 2.5, Auto-mode.
- **Pricing**: Free 2K completions / Pro $20 / Pro+ $60 / Business $40/seat / Ultra $200. Credit-pool model since Jun 2025.
- **Agent**: Composer agent mode, up to **8 parallel background agents** in cloud, multi-file editing.
- **MCP**: yes.
- **Differentiator**: best-in-class IDE UX, Composer model is genuinely fast.
- **Why not for you**: you're CLI-first with skills/hooks; Cursor's value is the IDE, which doesn't compose with your invested stack.

### Cline (formerly Claude Dev)

- **Distribution**: VS Code, JetBrains, Cursor, Windsurf, Zed, Neovim, plus preview CLI (macOS/Linux). Apache 2.0, 61.2k stars.
- **Mode split**: **Plan mode** (read-only proposal) + **Act mode** (execute). Cleanest implementation of this pattern; OpenCode and Codex both copied it.
- **MCP**: full marketplace, `cline_mcp_settings.json` (symlinkable to your `.mcp.json`).
- **Pricing**: free; BYO API key, pay only model tokens.
- **Why not for you**: editor-bound; doesn't replace Claude Code's CLI lifecycle.

### opencode (opencode.ai)

- **Repo**: hit 147k GitHub stars by Apr 2026 (up from 100k Feb 2026, growing 4.5× faster than Claude Code in star velocity), 6.5M monthly devs.
- **Stack**: Go + Bubble Tea TUI, terminal-native, **75+ model providers** (mid-session switching).
- **Modes**: Build agent (writes/runs) + Plan agent (read-only) — same split as Cline.
- **MCP**: yes, LSP integration, multi-session.
- **Pricing**: free OSS, BYO key.
- **Differentiator**: most model-portable serious agent in 2026; no vendor lock. The "people are quietly moving here" story is real but smaller than Codex.

### Aider (aider.chat)

- **Mature** (since 2023): repo-map via Tree-sitter + PageRank, 70+ model leaderboard, architect/editor pair pattern, watch-mode `AI!` comments, `/web`, `/voice`, prompt caching, `.aider.conf.yml`.
- **Pricing**: free OSS, BYO key.
- **Why not for you**: lacks the lifecycle hook surface and persistent memory primitives you depend on.

### Zed Agent

- **Model**: ACP (Agent Client Protocol) — universal protocol that lets Claude Agent, Codex, and OpenCode run *inside Zed*. Parallel agents stream to one editor.
- **Differentiator**: multiplayer collab with humans + AI agents in same project. Worth watching — ACP could be the next MCP-like standard layer.

### Windsurf (Codeium, Wave 13 early 2026)

- SWE-1.5 proprietary model (13× faster than Sonnet 4.5), Codemaps (visual annotated code nav), Cascade. Multi-agent + git worktrees added Wave 13.
- $15/month Pro (cheaper than Cursor).
- Generous free tier with unlimited autocomplete.

### Continue.dev, Goose, Plandex, Sourcegraph Cody/Amp

- **Continue.dev**: open-source IDE extension, multi-model, privacy-first. Niche.
- **Goose (Block)**: free Apache 2.0, 25+ providers, CLI + desktop. Real but not catching up to Codex/CC.
- **Plandex**: planning-heavy CLI, smaller ecosystem.
- **Sourcegraph Amp**: enterprise/code-graph focused; not solo-dev oriented.

### Devin (Cognition Labs)

- **Pricing reset Apr 2025**: $20 Core (pay-as-you-go @ $2.25/ACU ≈ 15 min Devin time) / $500 Team (250 ACUs) / Enterprise. Down from previous $500-only.
- **Differentiator**: most autonomous (full sandbox with terminal/editor/browser).
- **Honest take**: ACU pricing remains "genuinely confusing" per [Idlen review](https://www.idlen.io/blog/devin-ai-engineer-review-limits-2026/); quality matches spec quality. Not for solo daily driver.

### Replit Agent, v0, Lovable, Bolt

- Frontend-only / web-app generation. Not relevant for your jarvis+redrobot work.

### GitHub Copilot Workspace

- Autonomous PR-from-issue flow. Embedded in GitHub UI. Not a CLI peer.

## Concrete patterns / recipes

### The dominant 2026 hybrid pattern

From the Reddit-500 survey, Crosley migration guide, and HN consensus:

> **"Claude Code for architecture, Codex for keystrokes."**

Three observed splits people actually run:

1. **Generator + Reviewer**: Claude Code writes the feature → Codex (separate context, no shared state) reviews the diff before merge. Catches assumption-bleed.
2. **Drafter + Refiner**: Codex drafts a fast first cut → Claude Code does the surgical refactor pass with full repo context.
3. **Layer split** (most common for serious teams): Codex owns infra/DevOps/test boilerplate/migrations/lint fixes; Claude Code owns business logic, schema, architectural decisions.

The token-cost gap is large enough to matter: one measured task burned **6.23M Claude tokens vs 1.5M Codex tokens** for identical output ([codersera](https://codersera.com/blog/claude-code-vs-openai-codex-2026/)).

### What single-tool stacks look like

- **Pure Claude Code**: solo devs / small teams with deep customization (your profile). Skills + hooks + MCP investments create high-friction switching cost.
- **Pure Codex**: backend-heavy + test-heavy teams who want default-good behavior without configuration; teams with tight token budgets; teams new to agent CLIs.
- **Pure opencode/Cline/Aider**: privacy-first or multi-model-portable shops; teams that distrust any single vendor.

### Migration tooling that exists

- [cc2codex](https://github.com/ussumant/cc2codex) — beta unofficial CC→Codex migrator.
- [ccode-to-codex](https://github.com/zuharz/ccode-to-codex) — migrates skills + agents with risk classification (MECHANICAL / MANUAL / REFACTOR).
- [claude2codex](https://dev.to/treesoop/claude2codex-migrate-claude-code-config-to-openai-codex-in-one-command-jlj) — single-command attempt.

**All three explicitly warn**: wholesale port is the wrong pattern. Restructure, don't copy.

## What this user should consider given his context

**Your stack is the high-friction case.** 40+ global skills, custom Python MCP memory server (`mcp-memory/server.py` shared with redrobot), three-way doc split (CLAUDE.md / SOUL.md / CONTEXT.md) loaded via SessionStart hook in `scripts/session-context.py`, Telegram bot bridge, sandcastle worktrees, autonomous loops, hook lattice (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop). The Blake Crosley migration guide is written by someone with 744 entries / 84 hooks / 48 skills / 19 agents — and even *they* call wholesale migration the wrong move. Codex has fewer hook lifecycle events and weaker ordering guarantees; you'd lose your SessionStart auto-loader pattern and have to rebuild memory recall around Codex's session model.

**Three concrete reasons to stay:**

1. **The Anthropic incident is resolved and over.** Apr 20 (v2.1.116) fixed all three bugs. Rate limits doubled in May. Anthropic publicly committed to gradual rollouts + soak periods for intelligence-affecting changes. Defecting now is reacting to a closed event.
2. **Your work mix favors Opus 4.7's strengths**: ambiguous design (your `/grill`, `/reason` skills exist *because* you value depth-first reasoning), multi-file MCP server changes, cross-project (jarvis + redrobot) refactors needing 1M context, safety-critical sandcastle orchestration. Codex's 25% blind-review win rate confirms it's the wrong tool for "is this architecturally sound" decisions.
3. **Your ~year of investment is partially unportable.** Skills like `/implement`, `/grill`, `/delegate` lean on Claude Code's specific hook ordering and the `record_decision` MCP contract. Porting requires rewriting, not copying.

**One concrete reason to add Codex as side-channel** (don't replace, augment):

- Codex's **3-4× token efficiency + Terminal-Bench 2.0 lead** is genuinely better for the bulk-mechanical class of work you sometimes hand to `/delegate`: CI fixes, lint sweeps, dependency bumps, doc-comment passes, schema migration boilerplate. Install Codex CLI alongside, give it its own `AGENTS.md` (copy your CLAUDE.md verbatim — it's the same spec), keep your skills/memory in Claude Code. Use Codex via `codex exec --ask-for-approval never -s workspace-write` for fire-and-forget mechanical work; use Claude Code for everything that touches your skills/memory/SOUL.md identity.

**Re-evaluate in Q3 2026** if (a) Anthropic ships another postmortem-class incident, (b) Codex closes the hook lifecycle gap, or (c) ACP becomes the universal agent protocol (then your skills become tool-portable, lowering switching cost). Until then: Claude Code primary + Codex side-channel is the honest answer for your specific situation.

## Adjacent topics worth deeper research

- **ACP (Agent Client Protocol)** — Zed's universal agent protocol. If it gets MCP-like adoption, skills become portable across Claude Code / Codex / opencode and the switching-cost calculus changes fundamentally. Worth a separate deep-dive in 2-3 months.
- **Hook lifecycle parity Codex vs Claude Code** — exact mapping of which CC hooks have Codex equivalents, which don't. Crosley guide implies significant gap; would benefit from hands-on test rather than secondhand summary.
- **Token-cost economics for sustained autonomous work** — your scheduled tasks + autonomous loops are exactly the workload Codex's 3-4× efficiency targets. Real measurement of one week of autonomous-loop runs on both would settle this empirically.
- **Postmortem risk profile as a tool-choice axis** — Anthropic's April incident is one data point. Worth tracking whether OpenAI / Cursor / opencode have comparable silent-degradation incidents over the next 6 months.
- **Subagent orchestration model differences** — Codex's wait-all-consolidate vs Claude Code's shared-task-list-with-messaging. For your `/delegate` skill, which model is actually more reliable for 4-8 parallel issues?
- **The "Claude Code Got Weird" trust narrative** — has implications for any centralized-control-plane agent. Does the same risk apply to Codex if OpenAI ships a comparable incident?

## Sources

### Primary repos / docs
- [openai/codex GitHub repo](https://github.com/openai/codex)
- [openai/codex releases page](https://github.com/openai/codex/releases)
- [Claude Code CHANGELOG (anthropics/claude-code)](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
- [Codex CLI features](https://developers.openai.com/codex/cli/features)
- [Codex CLI command reference](https://developers.openai.com/codex/cli/reference)
- [Codex changelog](https://developers.openai.com/codex/changelog)
- [Codex subagents docs](https://developers.openai.com/codex/subagents)

### Anthropic postmortem + analysis
- [Anthropic April 23 postmortem](https://www.anthropic.com/engineering/april-23-postmortem)
- [Fortune coverage of Claude Code degradation](https://fortune.com/2026/04/24/anthropic-engineering-missteps-claude-code-performance-decline-user-backlash/)
- [VentureBeat: Anthropic reveals harness changes caused degradation](https://venturebeat.com/technology/mystery-solved-anthropic-reveals-changes-to-claudes-harnesses-and-operating-instructions-likely-caused-degradation)
- [InfoQ analysis of three overlapping changes](https://www.infoq.com/news/2026/05/anthropic-claude-code-postmortem/)
- [The Register: Anthropic admits 'upgrades' dumbed down Claude](https://www.theregister.com/2026/04/23/anthropic_says_it_has_fixed/)
- [scortier: 6,852 Sessions Prove Performance Collapse (Laurenzo audit)](https://scortier.substack.com/p/claude-code-drama-6852-sessions-prove)

### Direct comparisons / migration guides
- [Anthony Maio: Codex Got Better Because Claude Code Got Weird (May 6, 2026)](https://anthonymaio.substack.com/p/codex-got-better-because-claude-code)
- [Blake Crosley: Claude Code → Codex migration guide](https://blakecrosley.com/blog/claude-code-to-codex-migration)
- [Pasquale Pillitteri: Codex vs Claude Code honest guide](https://pasqualepillitteri.it/en/news/1578/codex-vs-claude-code-honest-guide-2026)
- [Codersera: 2026 engineering team comparison](https://codersera.com/blog/claude-code-vs-openai-codex-2026/)
- [Morphllm: benchmarks, subagents, limits (May 2026)](https://www.morphllm.com/comparisons/codex-vs-claude-code)
- [Developers Digest: April 2026 which agent for which job](https://www.developersdigest.tech/blog/codex-vs-claude-code-april-2026)
- [Daniel Vaughan: Codex customisation stack (Apr 12, 2026)](https://codex.danielvaughan.com/2026/04/12/codex-cli-customisation-stack-unified-system/)
- [Augment Code: Codex CLI v0.116.0 enterprise features](https://www.augmentcode.com/learn/openai-codex-cli-enterprise)

### Community sentiment / Reddit-HN
- [HN: Codex vs Claude Code today (item 46391391)](https://news.ycombinator.com/item?id=46391391)
- [HN: Reddit-sourced sentiment dashboard (item 45610266)](https://news.ycombinator.com/item?id=45610266)
- [DEV: What 500+ Reddit developers really think](https://dev.to/_46ea277e677b888e0cd13/claude-code-vs-codex-2026-what-500-reddit-developers-really-think-31pb)

### Migration tooling
- [cc2codex GitHub](https://github.com/ussumant/cc2codex)
- [ccode-to-codex GitHub](https://github.com/zuharz/ccode-to-codex)

### Competitive tools
- [Cline GitHub repo](https://github.com/cline/cline)
- [opencode-ai/opencode](https://github.com/opencode-ai/opencode)
- [OpenCode 140k stars analysis](https://dev.to/ji_ai/opencode-hit-140k-stars-why-terminal-agents-won-2026-aci)
- [Cursor pricing 2026 (Vantage)](https://www.vantage.sh/blog/cursor-pricing-explained)
- [Aider repo map docs](https://aider.chat/docs/repomap.html)
- [Zed Agent Panel](https://zed.dev/docs/ai/agent-panel)
- [Zed Agent Client Protocol](https://zed.dev/acp)
- [Devin pricing 2026 (Costbench)](https://costbench.com/software/ai-coding-assistants/devin-ai/)
- [Idlen Devin review 2026](https://www.idlen.io/blog/devin-ai-engineer-review-limits-2026/)
