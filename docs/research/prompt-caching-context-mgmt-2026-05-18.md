---
title: Prompt caching & context management for Claude Code — deep dive
date: 2026-05-18
status: working-doc
scope: jarvis (solo-dev, Claude Code native, 3 devices, Max subscription)
sources:
  - Anthropic prompt caching docs (platform.claude.com, fetched 2026-05-18)
  - Anthropic Agent Skills docs + skill-authoring best practices (platform.claude.com)
  - Anthropic engineering blog — "Equipping agents for the real world with Agent Skills"
  - Claude Code costs/optimization docs (code.claude.com/docs/en/costs)
  - Matt Pocock — AI Hero workshop ("Smart zone is 100K tokens") + X/Twitter posts
  - Adobe NoLiMa benchmark (arXiv 2502.05167, ICML 2025)
  - NVIDIA RULER benchmark
  - arXiv 2511.13900 — GM-Extract / lost-in-the-middle mitigations
  - diffray.ai context-dilution writeup
  - albertsikkema.com — "Why I Shrunk Claude Code's Context Window Back to 200k" (2026-04-23)
  - claudecodecamp.com — "How prompt caching actually works in Claude Code"
  - github.com/cnighswonger/claude-code-cache-fix — resume-cache regression
  - ccusage docs (ccusage.com)
  - Anthropic Sonnet 4.6 / Opus 4.6 announcements
---

## Executive summary

The "100K smart zone" claim is **directionally correct but the specific number is folklore**, not benchmarked. Matt Pocock's framing rests on (a) the quadratic-attention argument, (b) qualitative observation, and (c) one anecdote about a 93.7K-token subagent call. There is **no published Anthropic benchmark** putting the cliff at exactly 100K.

What benchmarks **do** say:
- NoLiMa (ICML 2025): 11/12 LLMs drop below 50% of base score by **32K tokens**.
- NVIDIA RULER: models reliably use only **50–65%** of advertised window; a 1M-token model is realistically good to ~600–700K on retrieval (not reasoning).
- Anthropic's own Sonnet 4.6 / Opus 4.6 announcements offer **no numbered retrieval curve** — only qualitative "reasons effectively across all that context."
- Sonnet 4.5 community benchmark: 18.5% MRCR accuracy at 1M tokens.
- Aider's Paul Gauthier: every model "gets confused beyond ~25–30K".

Operational takeaway: **the practical reasoning-quality zone is closer to 30–60K than 100K**, and 100K is a generous ceiling for *retrieval*, not *reasoning*. Pocock's qualitative framing is right; treat the number as a soft ceiling, not a hard floor. Anti-pattern: trusting 1M-context marketing for coding sessions.

The five highest-leverage actions for jarvis are listed in the **Proposals** table at the bottom. The single biggest one: **set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` and `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`** to force earlier compaction and cap the window at 200K. Everything else is incremental.

---

## Q1 — The "100K smart zone": primary evidence

**Pocock's claim, exact form** (X post, AI Hero workshop, 2026-04-ish):
> Doing experiments with Opus 4.6's 1M context window. Trying to push coding sessions past 100K tokens. Drop-off in quality is really noticeable. Dumber decisions, worse code, worse instruction-following.

**Evidence Pocock actually cites:**
1. Quadratic-attention argument (theoretical) — analogous to "matches grow quadratically with teams in a league". This explains *why* a cliff should exist; it does not pin the cliff at 100K.
2. Observed: 40–80 grill-me questions ≈ 25K tokens; one exploratory subagent consumed 93.7K. Anecdotal.
3. No benchmarks, no logged regression curves.

**What Anthropic publishes:**
- Sonnet 4.6 / Opus 4.6 announcements: 1M context, qualitative "reasons effectively across all that context", **no retrieval curve**.
- Opus 4.6 announcement claims "90% retrieval accuracy across the full window" — but only on MRCR-style retrieval, not reasoning. This is the marketing baseline.
- System cards: 200K default eval context. Anthropic itself doesn't evaluate reasoning at 1M.

**What independent benchmarks say:**
| Benchmark | Finding | Implication for jarvis |
|---|---|---|
| Adobe NoLiMa (ICML 2025) | 11/12 LLMs drop <50% of base score by 32K tokens | "Effective context" for *reasoning* far below advertised |
| NVIDIA RULER | Models use 50–65% of advertised window reliably; 1M model good to ~600–700K *retrieval* | 1M is for retrieval, not reasoning |
| Sonnet 4.5 MRCR @ 1M | 18.5% accuracy | Confirms cliff well below 1M |
| Paul Gauthier (Aider) | Confusion beyond ~25–30K tokens | Closer to Pocock's claim, much lower than 100K |
| GM-Extract (arXiv 2511.13900) | Positional robustness is relative; instruct-tuned models degrade 12% on QA at 12K of 128K window | "Lost in the middle" is real but context-density dependent (no Claude-specific data) |
| Sikkema (2026-04) | Empirically shrank Claude Code to 200K; cites Gauthier + NoLiMa | Real practitioner using 60–70% autocompact trigger |

**Verdict:**
- Pocock's *direction* is right: there's a soft cliff well before the marketed window.
- The *number* 100K is overgenerous for reasoning. NoLiMa says 32K. Gauthier says 25–30K. A reasonable working ceiling is **50K hot context for hard reasoning, 100K for routine multi-file edits, 200K hard cap for everything**.
- Reasoning vs retrieval matters: Claude Code is *reasoning* work; the 1M window is sold on *retrieval* benchmarks.

**Recommended update to `personal_workflow_aihero_adoption` memory:** keep the 100K rule as a memorable shorthand, but annotate `100K = soft ceiling for routine work; 50K = hard reasoning; primary evidence is NoLiMa (32K) + Gauthier (25–30K), not Anthropic benchmarks`.

---

## Q2 — Prompt caching for long sessions

**Anthropic's mechanics (load-bearing details from the prompt-caching doc):**
- Cache hierarchy: `tools → system → messages` (strict ordering). A change at any level invalidates that level + everything after.
- **Up to 4 cache breakpoints per request**. Each marks a position with `cache_control: {type: ephemeral, ttl: 5m|1h}`.
- **Minimum cacheable tokens** for Opus 4.7 / 4.6 / 4.5 and Haiku 4.5: **4,096**. For Sonnet 4.6 / 4.5: **1,024**. Below threshold → silently not cached.
- **Pricing:** cache write @ 5m = 1.25x base input; cache write @ 1h = 2.0x base; cache read = 0.1x base.
- **Lookback window**: 20 blocks. If your conversation has >20 blocks past the last breakpoint, no cache hit unless an earlier breakpoint is also present.
- **Mixing TTLs**: 1h breakpoint must come *before* 5m breakpoint.

**Math for jarvis:**
- CLAUDE.md + SOUL.md + CONTEXT.md ≈ 10K tokens. Plus tool defs + skill metadata (~85 skills × ~100 tokens metadata) ≈ another 8–10K. Total stable prefix ≈ 20K.
- Break-even for 1h vs 5m: 1h write costs 2.0x base, 5m write costs 1.25x base. Additional cost of 1h write = 0.75x base input. Each cache *miss* avoided (replaced by a read) saves 1.0 - 0.1 = 0.9x base. So one avoided cold-restart pays for itself.
- For a single session that runs >5 min and has >2 cache renewals, 1h TTL is cheaper. For a session entered cold once per hour, 1h pays after the first read past 5 min.

**Claude Code's automatic behavior (per claudecodecamp.com + Anthropic docs):**
- Auto-caching is on; system slides one breakpoint forward as conversation grows.
- **What invalidates cache:** any character change in prefix, MCP add/remove mid-session, model switch (`/model`), CLAUDE.md edit, MCP tool list change, web-search toggle.
- `/clear` clears messages but may preserve prefix cache if reused within 5 min.
- **Subagents have their own cache**, never share parent's. This is structurally important: every Task tool call pays a fresh write for tools + system + CLAUDE.md.
- **Auto-compaction reuses the prefix cache** — the compaction request only processes the new conversation summary, prefix stays warm.

**Known regression (#cnighswonger):**
- v2.1.113+ Bun binary on `--resume` / `/resume` silently breaks the prefix cache via three bugs: (1) attachment blocks drift past `messages[0]`, (2) `cc_version` fingerprint instability, (3) non-deterministic tool ordering.
- Impact reported: up to 20x cost increase on resumed sessions.
- **Action for jarvis**: when resuming a session, the first few turns will rebuild cache; budget for it or start fresh.

**Recommended caching strategy for jarvis:**
1. Keep CLAUDE.md + SOUL.md + CONTEXT.md **stable across the session**. Don't edit mid-session — that nukes the prefix.
2. Stop touching `.mcp.json` mid-session. Adding/removing an MCP server invalidates everything.
3. For long sessions (>30 min) with active model use: rely on automatic 5m caching; it renews on every hit. 1h TTL is for sparse-hit patterns (e.g. scheduled tasks, autonomous loop with idle gaps).
4. For scheduled tasks running every 30–60 min that share the same system prompt: 1h TTL pays.
5. After `/resume` on v2.1.113+, **assume cache cold for the first few turns**.

---

## Q3 — Context compaction strategies

**Claude Code's built-in compaction:**
- Triggers automatically at ~95% capacity (default). Override via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` (or any number).
- Compaction is **lossy summarization**: error messages collapse to "there was an error"; stack traces vanish; exact filenames may drift; specific code snippets get paraphrased.
- Hook: `PreCompact` fires before compaction. Receives transcript path; can write to external store.
- Hook: `SessionStart` runs on resume — perfect place to rehydrate from a PreCompact snapshot.

**What survives compaction well:**
- Numeric facts, exact file paths, exact symbols (if explicitly named in recent turns).
- Recently-touched files (last 5–10 turns get more weight).
- Decisions stated explicitly ("we chose X over Y because Z").

**What survives poorly:**
- Stack traces, exact error strings, exact command output.
- Nuance ("we considered W and rejected it for reasons that turned out wrong later").
- "We *almost* did X but pivoted" — pivots get summarized as the destination, losing the reason.

**Three viable strategies for long work:**

| Strategy | Best for | Cache impact | Lossy? |
|---|---|---|---|
| **Ride auto-compact** | Continuous work, one mental model | Prefix cache survives, conversation cache lost | Yes — every compaction loses detail |
| **PreCompact-to-memory snapshot** (jarvis current) | Cross-session continuity | Same | Less — explicit working-state survives |
| **Plan → execute → /clear** (Pocock) | Discrete tasks with clear handoffs | Cold cache after clear (>5 min) | No (intentional reset) |
| **Hard cap via env var + early auto-compact** | Reasoning quality | Same | Yes — but smaller summaries |

**Jarvis-specific recommendation:**
- The PreCompact hook + Supabase working-state pattern is already correct and well-aligned with the evidence.
- Add: lower the auto-compact trigger to **60–70%** via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`. At 95% the summarization happens deep in the dumb zone — quality of the summary itself degrades. Compacting earlier means a smarter summary.
- Add: **disable 1M context window** for routine sessions via `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`. Caps at 200K. Removes the temptation to soak in dumb-zone tokens. Re-enable explicitly for whole-codebase reads.

---

## Q4 — Plan/Execute/Clear rhythm: evidence base

**Pocock's three-phase loop:**
1. **Plan** (human-led, "Grill Me" 40–100 questions, ~25K conv tokens)
2. **Execute** (fresh session, paste plan + parent issue, AFK)
3. **Review** (human touchpoint — "automating these phases produces slop")

**Evidence Pocock cites:** quadratic-attention math + observation. No RCT. The framework is principled but not benchmarked.

**Alternatives — what 2025–2026 practitioners actually do:**

| Pattern | Source | Trade-off |
|---|---|---|
| Plan/execute/clear (Pocock) | AI Hero workshop | Clean cache wipe; cold-start tax; requires good plan externalization |
| Rolling compaction with PreCompact hook | Most Claude Code users | No cold-start tax; quality drift via summaries |
| Session-resume + explicit memory rehydration | jarvis current model | Memory survives, conversation history reborn — but resume-cache regression bites |
| Multi-session pipelines (orchestrator + child sessions) | Agent teams, subagents | Each child cold-starts cache; 7x cost; but each child stays in smart zone |
| "200K cap + 70% autocompact" | Sikkema 2026-04 | Forces earlier, smarter summaries within smart zone |

**Pocock-style verdict for jarvis:** the canonical `/reason → /grill → /to-prd → /to-issues → /implement` chain in CLAUDE.md is already a Pocock-style rhythm with named phases and explicit externalization (issues, PRDs, working_state). The plan is externalized in PRDs; execute is a fresh `/implement`; review is the PR. **Jarvis is doing this right.** The remaining gap: the `/end` skill should arguably nudge `/clear` more aggressively when a slice completes mid-session, vs continuing into the next slice in the same context.

**Don't-do:** building yet another "rolling context" pattern. The PreCompact + working_state pattern + skill phase boundaries is the right rhythm for solo work; further mechanism is overhead.

---

## Q5 — Skill/tool description bloat

**Anthropic's confirmed mechanic:**
- At session start, **only `name` + `description` of every skill is pre-loaded** into the system prompt. Full SKILL.md is loaded on demand via filesystem read.
- `description` is capped at **1,024 characters**. Realistic average: 200–400 chars.
- Anthropic's own example: "140x efficiency difference — 500 tokens index vs 70K full doc."

**Math for jarvis with 85 skills:**
- Avg description ~250 chars ≈ 60 tokens.
- 85 skills × 60 tokens = **~5,100 tokens** in baseline system prompt just for skill index.
- Plus skill names + frontmatter framing overhead ≈ **6–7K tokens total** for skill metadata.
- This is roughly 3% of a 200K window — non-trivial but not the dominant cost.

**Anti-pattern check (per Anthropic skill best practices):**
- Descriptions must be **third person**. "I can help with X" → "Processes X". Violation causes discovery problems.
- Descriptions must include **what + when** (capability + trigger).
- Vague descriptions ("helps with documents") → skill never activates.

**Action for jarvis:**
1. Audit the 85 skills for: (a) third-person voice, (b) explicit trigger conditions, (c) descriptions under 200 chars where possible.
2. Identify dormant skills (zero invocations in the last 30 days per session logs / ccusage). Move to archive directory. Each removed skill = ~60 tokens of permanent system-prompt savings.
3. CLAUDE.md notes some skills as `.bak.orphan.bak.orphan` — these still occupy metadata slots if their YAML frontmatter parses. Confirm they're excluded.

**Don't-do:** packing skills with implementation detail in the description. The description is for routing only; details belong in SKILL.md body (loaded on demand).

---

## Q6 — Subagent context isolation: cost/benefit

**Confirmed from Anthropic costs doc:**
- Each subagent / Task tool call spawns a fresh context: tools + system + CLAUDE.md + skill metadata loaded again.
- **Agent teams use ~7x more tokens** than single-context sessions (Anthropic's own number).
- "Sub-agents start paying off at 4+ parallel branches; 3–8 is the sweet spot" (community consensus, less authoritative).
- "Orchestrator on Opus + subagents on Haiku/Sonnet → 5–10x cost reduction" (community pattern; matches Anthropic's `model: haiku` config recommendation for subagents).

**Math for jarvis:**
- Single-context cost per turn at 50K context: ~50K read tokens × cache read rate (0.1x) ≈ effective 5K-equivalent.
- Subagent fresh start: ~25K prefix (CLAUDE.md + tools + skill metadata) **paid as cache write at 1.25x** = effective 31K-equivalent. Then conversation grows on top.
- One subagent ≈ 6x the per-turn marginal cost of staying in main context for the same work.

**Breakeven:**
- Use subagents when: (a) the work is **token-verbose** (test runs, log scans, doc reads) and only a summary needs to return, (b) the work is **>500 LOC** of fresh context-soak, (c) parallelism is real (≥3 independent branches).
- Don't use subagents when: the task is <20 turns and the orchestrator already has the relevant context loaded. The cold-start tax exceeds the savings.

**Jarvis-specific guidance:**
- `/delegate` for multi-issue parallel work — correct.
- `/implement` inline for single-issue work — correct.
- The current heuristic ("context-heavy / cross-cutting / safety-critical stay inline") matches the cost math.
- **One gap**: subagent spawn-prompt size. CLAUDE.md says "Keep spawn prompts focused. Teammates load CLAUDE.md, MCP servers, and skills automatically, but everything in the spawn prompt adds to their context from the start." Audit `/delegate` and `/implement` skill templates for bloat in the spawn prompt.

**Don't-do:**
- Single-issue `/delegate`. Pay 7x for no parallelism win.
- Subagent for "explore the codebase" — fresh subagent has no project context loaded yet (CLAUDE.md alone won't surface architecture). Either pre-load context into spawn prompt or do inline.

---

## Q7 — Token budget observability

**ccusage — confirmed capability:**
- Reads local Claude Code JSONL session files (`~/.claude/projects/...`).
- Reports per-block: `Cache Create`, `Cache Read`, input tokens, output tokens, estimated cost.
- **Does NOT compute cache hit rate as a percentage.** Solo dev must compute: `cache_read / (cache_read + cache_create + input_tokens_no_cache)`.
- Block reports show 5-hour billing windows.

**Other tools:**
- `claude-monitor` (Maciek-roboblog/Claude-Code-Usage-Monitor): real-time predictions and warnings, similar metric base.
- `ccflare`: alternative reporter.
- AgentsRoom: hosted dashboard with explicit "cache hit rate" column.
- `/usage` slash command: per-session totals only.
- Status line config: can show context-window usage continuously.

**What "cache-warm enough" means operationally:**
- Healthy hit rate for a sustained session: **80–95%** of input tokens should be `cache_read`.
- <50% sustained: prefix is being invalidated by something (model switch, MCP change, CLAUDE.md edit). Diagnose.
- After `/resume` on v2.1.113+: first 2–3 turns will dip low; recovers if no regression bug triggers.

**Action for jarvis:**
1. Add a status-line element showing context window usage (per Anthropic costs doc `/en/statusline#context-window-usage`).
2. Periodically (weekly?) run ccusage and check 7-day cache-read ratio. Trend down → investigate prefix instability.
3. Wire ccusage output into the existing `/status-record` event recorder so cache health is part of the snapshot.

---

## PROPOSALS

| # | Proposal | Source | Priority | Notes |
|---|---|---|---|---|
| 1 | Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` and `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` in env on all 3 devices (Main PC, Workshop, Laptop) | Sikkema 2026-04 + Anthropic costs doc | **High** | Single-command device-config fix. Forces earlier, smarter compaction; caps at 200K. Test for one week before locking in. |
| 2 | Update `personal_workflow_aihero_adoption` memory to annotate "100K = soft ceiling for routine work, 50K = hard reasoning, primary evidence is NoLiMa (32K) + Gauthier (25–30K), not Anthropic" | This research | **High** | Closes the folklore-vs-evidence gap. One memory edit. |
| 3 | Audit the 85 skills for: third-person voice, explicit trigger, description <200 chars where feasible; archive skills with zero invocations in last 30 days | Anthropic skill best practices | **Medium** | Each removed skill ≈ 60 tokens permanent system-prompt savings. Total potential savings: 1–3K tokens of baseline budget. |
| 4 | Add context-window usage to status line (per `/en/statusline#context-window-usage`) | Anthropic costs doc | **Medium** | Continuous visibility. Zero ongoing cost. |
| 5 | Add ccusage 7-day cache-hit-ratio summary to weekly `/status-record` or `/self-improve` output | ccusage docs | **Medium** | Detects prefix instability before it becomes expensive. |
| 6 | After `/resume` on v2.1.113+ Bun binary: budget first 2–3 turns as cold-cache; explicitly note in `/end` and resume skills | github.com/cnighswonger/claude-code-cache-fix | **Medium** | Awareness fix. Worth flagging in CLAUDE.md until Anthropic patches. |
| 7 | Audit `/delegate` and `/implement` spawn-prompt templates for bloat (per Anthropic costs doc on agent-team prompts) | Anthropic costs doc | **Low** | Each spawn pays the prompt as fresh write. Trim aggressively. |
| 8 | Consider 1h TTL via API cache_control for the autonomous-loop scheduled task (runs hourly, same system prompt) | Anthropic prompt caching doc | **Low** | Only relevant if jarvis ever runs the autonomous loop via direct API; under Max subscription Claude Code may not expose this knob. |
| 9 | Stop editing CLAUDE.md / SOUL.md / CONTEXT.md mid-session. Defer doc edits to end-of-session via `/end` reconciliation | Anthropic prompt caching cache-invalidation table | **Low** | Pure discipline. Mid-session edits nuke prefix cache. |
| 10 | Add a "is this work hot-reasoning or retrieval?" check before invoking a 100K+ context session — bias retrieval-heavy work to subagents | NoLiMa + RULER + Pocock | **Low** | Soft heuristic for goal-routing. |

---

## Don't-do list (anti-patterns)

- **Don't trust the 1M-context marketing for coding.** It's a retrieval window; reasoning quality degrades far earlier. Sonnet 4.5 at 1M = 18.5% MRCR.
- **Don't edit CLAUDE.md / SOUL.md / CONTEXT.md / `.mcp.json` mid-session.** Each edit invalidates the cache prefix. Total per-turn cost spikes 10x.
- **Don't switch models (`/model`) inside a hot session.** Cache invalidates entirely.
- **Don't use `/delegate` for a single issue.** Pay 7x for no parallelism. Use `/implement` inline.
- **Don't pack implementation detail into skill descriptions.** Descriptions are routing-only (1024 char hard cap). Detail belongs in the SKILL.md body, loaded on demand.
- **Don't ride auto-compaction at 95%.** The summarization itself happens deep in the dumb zone; the summary is worse than one taken at 65%.
- **Don't trust agent self-reports on edited files** (already in CLAUDE.md, restating because it interacts with subagent cost: a fabricated edit is a 7x-cost zero-value spawn).
- **Don't add an 86th skill before auditing the 85th.** Skill-metadata bloat is permanent in baseline system prompt.
- **Don't write first-person skill descriptions** ("I can help with X"). Causes discovery failures per Anthropic's own warning.
- **Don't assume `/resume` preserves cache on v2.1.113+ Bun binary.** It silently breaks per known regression. Budget 2–3 cold-start turns or use the cnighswonger proxy fix.
- **Don't manually `/clear` if you're inside the 5-min cache window and only switching topics within the same problem space.** The cache survives `/clear` if reused fast — wasteful clear loses warm prefix unnecessarily.
- **Don't conflate "context window" with "smart zone."** They are different numbers. The window is 200K-1M; the smart zone is 25–60K for reasoning, 100K generous, 200K is hard cap for retrieval.

---

## Sources

- Anthropic — Prompt Caching docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic — Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic — Skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Anthropic engineering — "Equipping agents for the real world with Agent Skills": https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Anthropic — Manage costs effectively (Claude Code): https://code.claude.com/docs/en/costs
- Anthropic — Sonnet 4.6 announcement: https://www.anthropic.com/news/claude-sonnet-4-6
- Anthropic — Opus 4.6 announcement: https://www.anthropic.com/news/claude-opus-4-6
- Matt Pocock X post on 100K dumb zone: https://x.com/mattpocockuk/status/2034572011175907474
- BigGo Finance — "Why AI Coding's 'Smart Zone' Is Only 100K Tokens": https://finance.biggo.com/news/e7209c094224b09c
- Sean Weldon — Full Walkthrough: Workflow for AI Coding — Matt Pocock: https://www.sean-weldon.com/blog/2026-04-27-workflow-for-ai-coding-matt-pocock
- Adobe NoLiMa benchmark paper (ICML 2025): https://arxiv.org/pdf/2502.05167
- NVIDIA RULER benchmark — referenced via diffray and aimultiple summaries (no direct primary fetched)
- GM-Extract paper: https://arxiv.org/html/2511.13900v1
- diffray.ai — Context Dilution: https://diffray.ai/blog/context-dilution/
- Sikkema — "Why I Shrunk Claude Code's Context Window Back to 200k": https://albertsikkema.com/ai/development/tools/2026/04/23/smaller-context-window-better-claude-code.html
- claudecodecamp — "How Prompt Caching Actually Works in Claude Code": https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code
- cnighswonger — claude-code-cache-fix (resume-cache regression): https://github.com/cnighswonger/claude-code-cache-fix
- ccusage block reports: https://ccusage.com/guide/blocks-reports
- Claude-Code-Usage-Monitor: https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor
- developersdigest — "Claude Code Token Burn Is an Observability Problem": https://www.developersdigest.tech/blog/claude-code-token-burn-cache-observability
- claudefa.st — Sub-agent best practices: https://claudefa.st/blog/guide/agents/sub-agent-best-practices
- mindstudio.ai — Agent teams vs subagents: https://www.mindstudio.ai/blog/claude-code-agent-teams-vs-sub-agents
