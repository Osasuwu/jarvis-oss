---
title: Skill discovery and routing for a large skill catalog
date: 2026-05-18
status: working-doc
scope: jarvis (~85 skills, Claude Code native, solo-dev)
sources:
  - anthropic-docs/skill-best-practices
  - claudefa.st/skill-listing-budget
  - alexop.dev/claude-md-bloat
  - rebelytics/one-skill-to-rule-them-all
  - Hermes Agent / Curator pattern
  - AWS Bedrock vector tool selection
  - LMSYS RouteLLM, LLMRouter, NadirClaw
  - apideck, marktechpost (MCP context bloat)
  - Vercel evals (skills-never-invoked 56%)
---

## Executive summary

1. Claude Code routes skills via **description-matching in the system prompt** (~75-150 tok/skill). At 85 skills jarvis is ~6-12k tokens of metadata - already past the 1% default `skillListingBudgetFraction` cap, meaning low-use skills are being silently dropped.
2. The 2026 industry signal is consistent: 8-12 skills is the sweet spot for description-match routing; past ~25 skills triggering becomes unreliable. Vercel evals showed skills never invoked in 56% of cases.
3. The two production patterns for >50-skill catalogs are: **(a) vector-RAG over descriptions** (AWS Bedrock pattern, +6.5pp accuracy, -92% inference cost), and **(b) LLM-router classifier** (RouteLLM-style, 85% cost reduction at 95% of frontier quality).
4. A third pattern emerging in agent ecosystems is **the Curator** (Hermes): auto-archive on staleness (30d stale → 90d archive), LLM-driven consolidation, never auto-delete.
5. Tag/category-based curation works (Cursor MDC `globs:` cut tokens 3x vs monolithic Copilot instructions) but requires a triggering surface other than glob - jarvis would need an intent classifier or explicit category gates.
6. "Skills calling skills" is not an explicit anti-pattern in Anthropic docs - the official line is **composability** (Lego bricks). But the field evidence (Skills Soup, silent conflicts) supports jarvis's `skills_independent_complementary` rule for triggered-on-keyword skills. The composability story works through user/orchestrator routing, not direct invocation.
7. The cheapest available levers for jarvis today, in order: (a) tighten descriptions to <150 chars, (b) audit and archive .bak.orphan cruft, (c) raise `skillListingBudgetFraction` to 0.02 as a stopgap, (d) add usage telemetry to detect dead skills, (e) consider a Curator scheduled task.

---

## 1. State of the art: Skill routing 2025-2026

### Anthropic's three-level loading model

Skills launched Dec 2025 as an open standard. The architecture:

- **Level 1 (always-loaded):** YAML frontmatter `name` + `description` injected into system prompt at session start. ~50-150 tokens per skill.
- **Level 2 (on-trigger):** `SKILL.md` body read when Claude decides skill matches user intent. Up to 500 lines (~5k tokens).
- **Level 3 (on-demand):** Bundled reference files (`reference/*.md`, scripts) read by bash when needed inside the skill.

**Routing is pure description-match against the system prompt.** No classifier, no embedding, no router. Claude's reasoning matches user intent against descriptions and picks ≥0 skills to load Level 2.

Citation: [Anthropic best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [Anthropic engineering post](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).

### The hidden budget

Claude Code v2.1.129+ added two undocumented settings ([claudefa.st](https://claudefa.st/blog/guide/mechanics/skill-listing-budget)):

- `skillListingBudgetFraction` (default `0.01` = 1% of context window)
- `skillListingMaxDescChars` (default `1536`)

On a 200k Sonnet 4.6 context, the default 1% = ~2,000 tokens. At 75-150 tok/skill that fits **15-25 skills before truncation**. When exceeded, Claude Code "drops entire descriptions for low-use skills" - ranked by usage frequency and recency. The skill stays "visible" in `/skills` but Claude cannot match against the missing description.

**Implication for jarvis:** with ~85 skills, somewhere between 60-70 are likely in the dropped-description state in any given session. The active set is whatever was used most recently.

### Description-quality is load-bearing

Anthropic best-practices explicitly:
- Write in **third person** ("Processes Excel files" not "I help with Excel"). POV inconsistency causes discovery failures.
- Include both **what** and **when**: "Use when the user mentions PDFs, forms, or document extraction."
- Be **pushy**: under-triggering is a measured tendency. The skill-creator template literally suggests "Make sure to use this skill whenever the user mentions X, even if they do not explicitly ask for X."
- Front-load keywords - truncation removes the back half silently.

Citation: [Anthropic skill best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [Tort Mario via Medium](https://medium.com/@tort_mario/skills-for-claude-code-the-ultimate-guide-from-an-anthropic-engineer-bcd66faaa2d6).

---

## 2. Vector search over skill descriptions

### AWS Bedrock pattern (production)

The closest production analog to "embed skill descriptions, retrieve top-k" is AWS's [optimized agent tool selection](https://aws.amazon.com/blogs/storage/optimize-agent-tool-selection-using-s3-vectors-and-bedrock-knowledge-bases/) using S3 Vectors + Bedrock KB. Tool docs are embedded with **Amazon Titan Text Embedding v2**, one chunk per tool (name + parameters + description).

Numbers from their eval (422 tools):
- Naive all-tools baseline: **75.8%** accuracy
- Vector top-20 → LLM picks: **82.3%** accuracy (+6.5 pp)
- Recall@20: **91.9%**
- Latency: **4.25s vs 5.41s** (-21%)
- LLM inference cost: **$0.015 vs $0.202** per query (-92%)

The mechanism: the agent's pre-step is "given user query, retrieve top-K tools by cosine similarity over descriptions; only those K are added to the working context for the main reasoning pass."

### Tradeoff vs current description-match

| Dimension | Anthropic description-match | Vector RAG over descriptions |
|---|---|---|
| Setup cost | Zero - works today | Embed each skill, index, retrieval pipeline |
| Cold-start latency | None | One embedding API call per turn |
| Recall on synonyms | Brittle (keyword anchor needed - jarvis already hacks this) | Strong (semantic) |
| Skill count ceiling | ~15-25 reliable | Hundreds (AWS tested 422) |
| Description authoring | Pushy, keyword-stuffed | Natural language sufficient |
| Cost | Tokens in context (high at 85 skills) | Embedding storage + 1 retrieval/turn |
| Composability | Auto via "load multiple" | Need explicit "return top-K with K>1" |

**For jarvis:** vector RAG is the architecturally correct answer but expensive to build for a solo-dev project. A **lightweight middle ground** is feasible: cache embeddings of all 85 SKILL.md descriptions in Supabase (VoyageAI already in budget), run a tiny pre-step before SessionStart that retrieves top-K candidates and injects ONLY those into the description-match pool. This is the "router-before-router" pattern - see §3.

Citation: [AWS](https://aws.amazon.com/blogs/storage/optimize-agent-tool-selection-using-s3-vectors-and-bedrock-knowledge-bases/), agent tool selection +3x accuracy with RAG ([general finding](https://www.junia.ai/blog/mcp-context-window-problem)).

---

## 3. LLM-router classifier pattern

### RouteLLM / LiteLLM / LLMRouter / NadirClaw

[RouteLLM](https://github.com/lm-sys/routellm) (LMSYS, 2024) introduced the **cheap-classifier-first** pattern: a small BERT or causal-LLM classifier picks the destination model. Cost reduced 85% on MT Bench while preserving 95% of GPT-4 quality.

The pattern generalizes to **skill routing**:
1. User turn arrives
2. Cheap classifier (Haiku-tier, ~$0.001/call) sees user query + flat list of all skill names+1-line descs
3. Returns: skill name(s) to load, or "none"
4. Main session loads only those skill bodies

[LLMRouter](https://github.com/ulab-uiuc/LLMRouter) (Dec 2025) ships 16+ routers and a plugin workflow. [NadirClaw](https://github.com/NadirRouter/NadirClaw) is an OpenAI-compatible proxy doing this for Claude Code / Codex / Cursor (40-70% savings).

### Cost/latency math for jarvis

- 85 skills × 75 chars name+desc summary ≈ 6.4k input tokens to classifier
- Haiku call ≈ $0.0008 cached after first run
- Latency: ~400ms added per turn
- Accuracy: based on RouteLLM benchmarks, expect 90%+ recall on intended skill if descriptions are reasonable

**Tradeoff:** every turn pays the router cost. For an autonomous-loop session firing hundreds of turns, that adds up. Vector RAG (§2) is cheaper at steady state because embeddings amortize.

Citation: [RouteLLM repo](https://github.com/lm-sys/routellm), [TianPan production guide](https://tianpan.co/blog/2025-10-19-llm-routing-production), [anyscale on LLM routers](https://www.anyscale.com/blog/building-an-llm-router-for-high-quality-and-cost-effective-responses).

---

## 4. Tag/category-based curation

### Cursor MDC: globs as the routing primitive

Cursor's `.cursor/rules/` directory uses **glob patterns in frontmatter** to scope rules:

```yaml
---
description: TypeScript-only conventions
globs: ["**/*.ts", "**/*.tsx"]
---
```

The rule only loads when the agent is editing a matching file. Benchmark vs monolithic Copilot: **340 tok/request avg with 8 scoped MDC files vs 1,054 tok with monolithic** (3.1x reduction) on identical content [rpdi.us](https://rpdi.us/blog/cursorrules-vs-copilot-instructions-md-benchmark-2026/).

GitHub Copilot caught up July 2025 with `.github/instructions/*.instructions.md` + `applyTo:` glob frontmatter ([scalablehuman](https://scalablehuman.com/2025/08/08/unlock-github-copilots-secret-custom-prompt-rules-file-explained/)).

### Why globs don't directly apply to jarvis skills

Jarvis skills are not file-scoped. `/grill` triggers on intent, not on editing `foo.ts`. But the **principle** transfers: skills could carry category metadata and only the active-category bucket loads.

### Proposed categorization (jarvis-fit)

Looking at the current skill listing:
- **planning** - `/grill`, `/grill-me`, `/reason`, `/to-prd`, `/to-issues`
- **execution** - `/implement`, `/delegate`, `/tdd`, `/diagnose`
- **audit** - `/verify`, `/reflect`, `/self-improve`, `/security-review`, `/review`
- **memory/state** - `/end`, `/status-record`, `/goals`
- **research** - `/research`, `/improve-codebase-architecture`, `/zoom-out`
- **personal** - `/dnd*`, calendar/email/spotify wrappers
- **infra** - `/setup-tasks`, `/update-config`, `/keybindings-help`, hookify
- **comms** - `/caveman`, telegram, claude-md-management

Loading only an active category at session start cuts the metadata budget by ~80%. The category is set by **session intent** (engineering vs personal vs status) - already implicit in user opening behavior.

Citation: [Cursor MDC](https://www.agentrulegen.com/guides/cursorrules-vs-claude-md), [Copilot instructions](https://scalablehuman.com/2025/08/08/unlock-github-copilots-secret-custom-prompt-rules-file-explained/).

---

## 5. Skill bloat measurement

### Telemetry surfaces that already exist

Claude Code emits skill-invocation telemetry to `~/.claude/analytics/skill-usage.jsonl` (timestamp + metadata) and exports OpenTelemetry traces. The command `claude skills --stats` displays usage over last 30 days and flags "my-unused-skill 0 invocations" ([anthropics/claude-code#35319](https://github.com/anthropics/claude-code/issues/35319), [DeepWiki SinghCoder](https://deepwiki.com/SinghCoder/claude-code/12.1-analytics-and-telemetry)).

For jarvis specifically:
- Local transcripts in `~/.claude/projects/C--Users-<user>-GitHub-jarvis/` carry every Skill tool call
- A nightly job parsing JSONL → "skill X not invoked in 30d" is ~50 lines of Python or a one-shot `/reflect` extension
- 0 invocations in 90d = archive candidate per Curator pattern (§7)

### Vercel evals - the canary

Vercel ran agent evals on skill-driven workflows: **skills were never invoked in 56% of test cases** ([alexop.dev](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)). The takeaway: passive description-match is unreliable at scale; explicit instruction (via CLAUDE.md or hooks) outperforms.

This is consistent with jarvis's existing `memory_recall("<skill-name> ...")` keyword hack - the team has already worked around retrieval brittleness.

Citation: [Vercel via alexop.dev](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/), [Datadog AI Agents Console](https://www.datadoghq.com/blog/claude-code-monitoring/).

---

## 6. Hierarchical skills and composability

### Anthropic's official position: composable, not hierarchical

Anthropic frames skills as **Lego bricks** ([engineering post](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)). Composability means Claude can load multiple skills in one turn and the bodies stack in context. There's no "skill A calls skill B" primitive.

### Superpowers (obra) - methodology-stack

[Superpowers](https://github.com/obra/superpowers) ships ~20 skills organized as a **methodology stack**: brainstorming → TDD → debugging → review → meta (writing-skills). The skills reference each other in docs ("after this, run /tdd") but never invoke each other - the orchestration is in user/orchestrator hands.

### Field anti-pattern: silent conflicts at >10 skills

[Skills Soup](https://medium.com/@anup.karanjkar08/skills-soup-why-your-30-skill-claude-code-setup-is-eating-itself-798a9d108e2a) documents the failure mode: at 8-10 mixed skills, Claude "second-guesses its own outputs, produces more verbose preamble, and occasionally surfaces conflicts between skill instructions." Three skills with `commit_first` / `tdd_first` / `quick_fix_first` directives produced silent overrides.

### Implication for jarvis's `skills_independent_complementary`

The jarvis memory is **correct in spirit but precise about scope**:
- ✅ Auto-triggered skills (via description match) MUST be independent - otherwise silent overrides.
- ⚠️ User-invoked skill chains (`/reason → /grill → /to-prd → /to-issues → /implement`) ARE composability. The orchestration is explicit and user-driven; the skills don't call each other.
- ❌ Skills with embedded "after this, also do X" directives that auto-trigger other skills - that's the anti-pattern.

The Anthropic best-practices doc has no direct statement on this. Jarvis is ahead of the curve by having codified the rule.

Citation: [obra/superpowers](https://github.com/obra/superpowers), [Skills Soup](https://medium.com/@anup.karanjkar08/skills-soup-why-your-30-skill-claude-code-setup-is-eating-itself-798a9d108e2a).

---

## 7. Discovery UX for the human

### Slash autocomplete is the floor

Every modern coding-agent CLI (Claude Code, Codex CLI, Copilot CLI, Kiro, Hermes) implements `/` → autocomplete from a command registry. Prefix matching (`/h` → `/help`) with first-match-wins ordering. This is necessary but insufficient at 85 skills - the user has to know what to type.

### Intent classification surfaces

- [Intent Engine](https://mcpmarket.com/tools/skills/intent-engine) (Claude Code skill) analyzes user requests for "temporal signals, autonomy levels, functional requirements" and categorizes into hooks/skills/subagents/MCPs. Effectively a meta-skill router.
- Cursor and Continue.dev surface contextual rule suggestions in the editor based on current file + recent edits.

### Just-in-time recommendation - jarvis-fit pattern

The natural surface in jarvis is the **SessionStart hook** (`scripts/session-context.py`). Today it injects always-load rules + working state. It could additionally:

1. Parse the user's first turn (already arrives in the hook context on some flows; otherwise infer from recent transcripts).
2. Run a cheap classifier (or vector lookup) against skill descriptions.
3. Emit a one-line hint: `"Looks like a planning task - /grill, /to-prd, /reason available."`

This is non-intrusive (one line of context, not pollution) and addresses the "85 skills, can't remember them all" UX problem.

Citation: [Copilot CLI tab completion](https://htek.dev/articles/copilot-cli-weekly-2026-04-24), [Intent Engine](https://mcpmarket.com/tools/skills/intent-engine).

---

## 8. Lifecycle: experiment → graduate → retire

### The Curator pattern (Hermes Agent)

Hermes Agent ships a **Curator** background skill ([blog](https://www.xugj520.cn/en/archives/ai-agent-skill-library-hermes-curator.html), [issue #7816](https://github.com/NousResearch/hermes-agent/issues/7816)):

- Tracks per-skill: views, invocations, patches, last-used timestamp
- **30d unused → "stale"** (warning, not action)
- **90d unused → archived** to `~/.hermes/skills/.archive/` (recoverable, NOT deleted)
- Periodically spawns auxiliary LLM to survey library and propose per-skill decisions: **Keep / Patch / Consolidate / Archive**
- Only operates on agent-authored skills (not vendor/Anthropic-shipped)
- Never auto-deletes - archive is the worst outcome

### Feature-flag analogy

Modern feature-flag systems (Statsig, LaunchDarkly, Harness) treat flags as having a **mandatory lifecycle**: created with a TTL, must be retired or escalated. The lifecycle states map cleanly to skills:

| Flag state | Skill state | Trigger |
|---|---|---|
| Created (off) | New experiment | First commit |
| Rolling out (1% → 100%) | Graduating | Used in ≥5 sessions across 14d |
| Released (on for all) | Load-bearing | Used >10x/month, referenced in CLAUDE.md routing table |
| Stale | Stale | 0 invocations in 30d |
| Retired | Archived | 0 invocations in 90d, moved to `.archive/` |

### Mapping to jarvis cruft

Visible `.bak.orphan.bak.orphan` suffixes in the listing (`dnd-prep.bak.orphan.bak.orphan.bak.orphan`, `dnd.bak.orphan.bak.orphan.bak.orphan.bak.orphan`, `grill-me.bak.orphan.bak.orphan`, `status.bak.orphan.bak.orphan`, `tdd.bak.orphan.bak.orphan`, `gm-craft.bak.orphan.bak.orphan`) are skills that were **renamed/replaced but never deleted**. Either:
1. The replace was lazy (cp old new; edit new) - the orphans are dead weight.
2. The replace was via plugin install - the harness ships orphan-tagged backups.

Either way: ~6 confirmed orphans = ~600-900 tokens of metadata budget pure waste. **First action: delete or move to `.archive/`.**

Citation: [Hermes Curator](https://www.xugj520.cn/en/archives/ai-agent-skill-library-hermes-curator.html), [Statsig flag lifecycle](https://www.statsig.com/perspectives/feature-flag-lifecycle), [CloudBees retirement](https://www.cloudbees.com/blog/feature-flag-retirement).

---

## PROPOSALS

| # | Proposal | Source | Priority hint | Notes |
|---|---|---|---|---|
| P1 | Delete or archive all `.bak.orphan*` skills | direct repo inspection | **HIGH / quick win** | ~6 confirmed orphans, ~600-900 tokens recovered. 5 min of work. |
| P2 | Audit descriptions for length, third-person, keyword density; cap at ~150 chars front-loaded | [Anthropic best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | **HIGH** | Description-match accuracy is load-bearing; pushy + specific descriptions beat verbose. Make pass over all ~80 active skills. |
| P3 | Add category frontmatter (`category: planning\|execution\|audit\|comms\|personal\|infra`) to every skill | Cursor MDC pattern, [Continue.dev](https://www.agentrulegen.com/guides/cursorrules-vs-claude-md) | MED | Foundation for §4 category-gating without breaking current routing. No-op until P4 consumes it. |
| P4 | Extend `scripts/session-context.py` to load only category-matched skills based on inferred session intent | jarvis-internal + Cursor MDC | MED | Cuts metadata budget ~80%. Inference can start dumb (keyword match on first turn) and graduate to embedding lookup. |
| P5 | Build skill-usage telemetry job: parse `~/.claude/projects/.../transcripts/*.jsonl` for `Skill:<name>` invocations; surface "0 invocations in 30d" list in `/reflect` or autonomous-loop | [DeepWiki claude-code analytics](https://deepwiki.com/SinghCoder/claude-code/12.1-analytics-and-telemetry), Curator | MED | Provides evidence-based input for P6. Cheap (~50 LOC Python). |
| P6 | Implement Curator-lite as scheduled task: 30d→stale-flag in skill comment, 90d→move to `~/.claude/skills/.archive/` | [Hermes Curator](https://www.xugj520.cn/en/archives/ai-agent-skill-library-hermes-curator.html) | MED-LOW | Depends on P5. Keep archival not deletion. Manual restore. |
| P7 | Embed all SKILL.md descriptions in Supabase (VoyageAI) and add a pre-routing top-K retrieval step before description-match | [AWS Bedrock pattern](https://aws.amazon.com/blogs/storage/optimize-agent-tool-selection-using-s3-vectors-and-bedrock-knowledge-bases/) | LOW (architectural) | Highest leverage but biggest build. Defer until P1-P5 quantify the actual problem. Cost ~$0/mo at jarvis scale. |
| P8 | Raise `skillListingBudgetFraction` to 0.02 (~4k tok) as stopgap until P1-P3 land | [claudefa.st](https://claudefa.st/blog/guide/mechanics/skill-listing-budget) | LOW | 2x token cost across every session forever. Use only if P1-P3 aren't enough. |
| P9 | Codify lifecycle states in skill frontmatter: `lifecycle: experiment\|active\|deprecated\|archived` + `created: <date>` + `last_audit: <date>` | feature-flag analogy ([Statsig](https://www.statsig.com/perspectives/feature-flag-lifecycle)) | LOW | Light governance. Pair with P5/P6 for enforcement. Tier 1 cousin: existing `paused for experimentation phase until 2026-06-18` memory shows pattern already in use ad-hoc. |
| P10 | Document the routing chain in CLAUDE.md (already mostly there) and add a "skill audit" command/skill that runs P5+P6 on demand | rebelytics one-skill-to-rule-them-all meta-skill | LOW | Meta-skill that maintains other skills. Worth doing only after P1-P6 prove out. |

---

## Don't-do list

1. **Don't build skill-to-skill auto-invocation.** Field evidence (Skills Soup, silent overrides) confirms the jarvis `skills_independent_complementary` rule. Composability via user/orchestrator chain (`/reason → /grill → /to-prd → /implement`) is fine; embedded "after me, run X" auto-triggers are not.
2. **Don't auto-delete archived skills.** Curator pattern is explicit: archive is recoverable, deletion is not. Solo dev = no peer to recover from. Mistakes happen.
3. **Don't raise the budget fraction first.** It's the cheapest button but trades a permanent token tax for a problem that's better solved by cutting cruft (P1) and tightening descriptions (P2). Use only if P1-P3 insufficient.
4. **Don't reinvent vector search from scratch.** If P7 ever happens, use the existing VoyageAI + Supabase stack already in budget. Don't add a new vector DB. The AWS pattern works in 50 lines on top of existing infra.

---

## Sources

- [Anthropic - Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Anthropic - Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Anthropic - Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Claude Code docs - Extend Claude with skills](https://code.claude.com/docs/en/skills)
- [Claude Code's Hidden Skill Budget Setting (May 2026) - claudefa.st](https://claudefa.st/blog/guide/mechanics/skill-listing-budget)
- [Skills Soup: Why Your 30-Skill Claude Code Setup Is Eating Itself - Anup Karanjkar](https://medium.com/@anup.karanjkar08/skills-soup-why-your-30-skill-claude-code-setup-is-eating-itself-798a9d108e2a)
- [Stop Bloating Your CLAUDE.md - alexop.dev](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)
- [How to Keep Your AI Agent's Skill Library Clean: Hermes Curator](https://www.xugj520.cn/en/archives/ai-agent-skill-library-hermes-curator.html)
- [Hermes Curator RFC #16077](https://github.com/NousResearch/hermes-agent/issues/16077)
- [Optimize agent tool selection using Amazon S3 Vectors and Bedrock - AWS](https://aws.amazon.com/blogs/storage/optimize-agent-tool-selection-using-s3-vectors-and-bedrock-knowledge-bases/)
- [RouteLLM - LMSYS](https://github.com/lm-sys/routellm)
- [LLMRouter](https://github.com/ulab-uiuc/LLMRouter)
- [NadirClaw - OpenAI-compatible router for Claude Code](https://github.com/NadirRouter/NadirClaw)
- [LLM Routing in Production - TianPan](https://tianpan.co/blog/2025-10-19-llm-routing-production)
- [Skill invocation tracking GH issue - anthropics/claude-code#35319](https://github.com/anthropics/claude-code/issues/35319)
- [Claude Code Analytics & Telemetry - DeepWiki](https://deepwiki.com/SinghCoder/claude-code/12.1-analytics-and-telemetry)
- [Datadog Claude Code monitoring](https://www.datadoghq.com/blog/claude-code-monitoring/)
- [Superpowers - obra](https://github.com/obra/superpowers)
- [One Skill to Rule Them All - Rebelytics](https://github.com/rebelytics/one-skill-to-rule-them-all)
- [Cursor MDC vs Copilot Instructions benchmark - rpdi.us](https://rpdi.us/blog/cursorrules-vs-copilot-instructions-md-benchmark-2026/)
- [GitHub Copilot custom instructions - scalablehuman](https://scalablehuman.com/2025/08/08/unlock-github-copilots-secret-custom-prompt-rules-file-explained/)
- [Cursor Rules vs CLAUDE.md vs Copilot Instructions - agentrulegen](https://www.agentrulegen.com/guides/cursorrules-vs-claude-md)
- [Intent Engine - Claude Code Skill](https://mcpmarket.com/tools/skills/intent-engine)
- [MCP Context Window Problem - junia.ai](https://www.junia.ai/blog/mcp-context-window-problem)
- [Your MCP Server Is Eating Your Context Window - apideck](https://www.apideck.com/blog/mcp-server-eating-context-window-cli-alternative)
- [Feature flag lifecycle - Statsig](https://www.statsig.com/perspectives/feature-flag-lifecycle)
- [Feature flag retirement - CloudBees](https://www.cloudbees.com/blog/feature-flag-retirement)
- [Skill Authoring Patterns from Anthropic - Generative Programmer](https://generativeprogrammer.com/p/skill-authoring-patterns-from-anthropics)
- [The 8 Skills Every Claude Code Setup Needs in 2026 - Anubhav](https://medium.com/data-science-collective/the-8-skills-every-claude-code-setup-needs-in-2026-eb7e72cbf91f)
- [Skill Development Lifecycle - DeepWiki anthropics/skills](https://deepwiki.com/anthropics/skills/4.2-skill-development-lifecycle)
