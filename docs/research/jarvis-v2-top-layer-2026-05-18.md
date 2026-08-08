---
title: Jarvis V2 — top-layer personal AI orchestrator architectures
date: 2026-05-18
status: working-doc
scope: priors for a future "Jarvis above Claude Code" (smart home + news + dev specialist routing); v2 NOT being built yet
time_horizon: 6-18 months
sources:
  - Home Assistant blog + integrations (Ollama, Wyoming, Voice PE)
  - Karpathy LLM Knowledge Base (April 2026) / LLM OS Software-3.0 thesis
  - Simon Willison llm + datasette-llm-accountant
  - OpenClaw (openclaw.ai), secure-openclaw (Composio), psibot (DmacMcgreg), claudeclaw
  - Stanford OpenJarvis / Intelligence Per Watt
  - isair/jarvis (private voice, local MCP router)
  - Magentic-One (Microsoft) / AutoGen 0.4 / Semantic Kernel Magentic
  - LangGraph supervisor pattern, CrewAI, n8n 2026 agent nodes
  - OpenWebUI v0.6.31 native MCP, LibreChat MCP, AnythingLLM
  - Open Interpreter 01 (active May 2026)
  - Manus Telegram agents (March 2026)
  - LLM router cost/latency: 10-50ms rules / 50-200ms embedding / 500-2000ms LLM
  - EU AI Act Article 14 (Aug 2 2026 deadline)
  - pgvector + Supabase memory patterns, OpenBrain personal memory DB
---

## Executive summary

1. **The "personal AI hub" niche is real in 2026 and has converged on a recognisable shape**: single-user, multi-channel (Telegram-first, voice second, web third), supervisor-routes-to-specialists, persistent memory in Postgres/pgvector, runs as a 24/7 daemon on the user's own hardware. OpenClaw and psibot are the reference implementations; both are ≤6 months old and active.
2. **Current Jarvis (= Claude Code with skills + Supabase + Telegram MCP) already IS one of these hubs along the dev-specialist axis.** What v2 would add is non-dev channels (smart home, news, ambient routines), not a new architecture. The "v2 vs CC" framing is misleading — the realistic move is "Jarvis-above grows another head" not "rebuild from scratch".
3. **Home Assistant is the de-facto smart-home anchor**, and as of late 2025 it ships first-class LLM Conversation (Ollama, OpenAI, Anthropic, Google) + Voice PE hardware + Wyoming protocol for STT/TTS/wake-word. HA Assist is the path of least resistance for the smart-home channel; building a parallel HA-equivalent is not justified.
4. **Routing economics are settled**: hybrid rules→embedding→LLM-router is the production pattern. Rules add 10-50ms, embeddings 50-200ms, LLM-as-router 500-2000ms + cost. Jarvis's current skill-description routing in Claude Code is functionally an LLM-as-router; the v2 question is what sits ABOVE that, picking which specialist (CC vs HA vs news-reader vs note-taker) before CC's own router runs.
5. **Memory pattern that survives**: shared semantic memory + per-specialist episodic. Postgres + pgvector is the boring-winning stack (Supabase, OpenBrain, every n8n template). Jarvis's existing Supabase schema fits this shape. No sharding needed at single-user scale.
6. **Safety primitives are non-trivial for unattended top-layer agents**. EU AI Act Article 14 (Aug 2 2026) mandates "effective human oversight" for high-risk systems; smart-home actuation arguably qualifies. Four universal patterns from the 2026 guardrails literature: artifact verification, context rotation, privilege boundaries, rate limiting. None are free.
7. **Trigger conditions for "build v2"** are measurable. Today Jarvis serves ~1 channel actively (dev-via-CC, Telegram MCP as thin pass-through). The v2-pays-for-itself threshold is plausibly ≥3 active channels AND ≥1 unattended actuating decision/day AND ≥2 specialists with their own state. None of those hold yet; below the threshold, growing CC's skill set is cheaper than a new top layer.

---

## 1. The shape of the niche in 2026

### 1.1 What "personal AI hub" means in current practice

By May 2026 the term has settled. A personal AI hub is:

- **Single principal** (one human owner; no multi-tenant, no auth surface beyond pairing).
- **Multi-channel I/O** — at minimum chat (Telegram/WhatsApp/Signal), often voice, increasingly mobile push.
- **Persistent state** that survives sessions and crashes — a database, not transcripts.
- **Tool/specialist routing** — the hub itself doesn't do the work; it picks which sub-agent or tool does.
- **Runs continuously** on hardware the user controls (Mac mini, NUC, Pi, or cheap VPS).

This is distinct from chat UIs (OpenWebUI, LibreChat) — those are interactive surfaces, not always-on agents. And distinct from agent frameworks (LangGraph, CrewAI) — those are libraries you build a hub with, not hubs themselves.

### 1.2 Reference implementations

Three projects are close enough to "Jarvis v2 as described" to study directly:

**OpenClaw** ([openclaw.ai](https://openclaw.ai/), [GitHub](https://github.com/openclaw/openclaw)) — local-first Gateway with a single control plane for sessions, channels, tools, and events. Supports 20+ messaging channels (WhatsApp, Telegram, Signal, iMessage, Slack, Discord, IRC, Matrix, etc.). Pluggable LLM backends (Anthropic, OpenAI, local). Shipped end-2025, active development.

**psibot** ([DmacMcgreg/psibot](https://github.com/DmacMcgreg/psibot)) — purpose-built single-user always-on daemon for Mac, Telegram-first, runs on Claude Max subscription with $0 API spend. Multimodal (voice, images, YouTube, browser automation, scheduled tasks). Inspired by OpenClaw but stripped down. Closest in spirit to Jarvis.

**secure-openclaw** ([ComposioHQ](https://github.com/ComposioHQ/secure-openclaw)) — Composio's hardened fork. Adds 500+ app integrations and persistent memory. Demonstrates the "thin hub + fat tool catalogue" pattern.

All three follow the same architecture: thin local gateway → channel adapters → message bus → LLM call → tool/MCP invocation → memory write. None invented this; they crystallised what was emerging in n8n templates and Home Assistant Assist pipelines throughout 2025.

### 1.3 Why this niche only became viable in late 2025

Two enabling shifts:

1. **MCP standardisation (Anthropic, late 2024 → broad ecosystem 2025)** removed the per-integration cost. A hub no longer needs bespoke adapters for every tool; it consumes any MCP server.
2. **Claude Agent SDK + Max subscription pricing** (Sept 2025) made always-on agents economically realistic for one user. psibot's pitch — "$0 API costs beyond your Max plan" — is the load-bearing claim that wasn't possible 12 months earlier.

Both apply to Jarvis directly. The first is already in use (.mcp.json with telegram, memory, computer-use, etc.). The second is Jarvis's current cost model.

### 1.4 Karpathy's LLM-OS thesis as the abstract frame

In April 2026 Karpathy posted the "LLM Knowledge Base" workflow and reiterated the "Software 3.0 / LLM as kernel" framing. The relevant claim for Jarvis: the LLM is a programmable computational layer that orchestrates deterministic tools below it. The kernel doesn't store all state — it reads/writes to durable surfaces (markdown wiki, Postgres, files). For a personal hub, the wiki + scheduled compaction pattern is a candidate "facts" surface, while Supabase/pgvector remains the "memory" surface. They are complementary, not competing.

---

## 2. Landscape table — 15 candidate architectures/platforms

| # | Platform | Single-user fit | Multi-channel I/O | Smart-home friendly | Can route to CC | Persistence | License | Last active |
|---|---|---|---|---|---|---|---|---|
| 1 | **Home Assistant + Assist + Ollama/Anthropic Conversation** | Excellent (HA is single-household by design) | Voice PE, mobile app, dashboard, Telegram notify | **Native** (2000+ integrations) | Via shell-command or HTTP webhook to a CC daemon; no MCP yet | SQLite + Recorder DB; no LLM-memory primitive built-in | Apache 2.0 | May 2026, monthly releases |
| 2 | **OpenClaw** ([openclaw.ai](https://openclaw.ai/)) | Designed for it | 20+ channels (WA, TG, Signal, Slack, iMessage, IRC, Matrix...) | Via MCP servers / webhooks | Yes — Claude Agent SDK is a first-class backend | Local JSON + pluggable DB | MIT | May 2026 |
| 3 | **psibot** ([DmacMcgreg/psibot](https://github.com/DmacMcgreg/psibot)) | **Purpose-built** single user | Telegram only (deliberate) | Indirect (via MCP servers) | Yes — built on Claude Agent SDK | Local FS + memory MCP | MIT | Apr 2026 |
| 4 | **secure-openclaw** (Composio) | Yes | 500+ apps via Composio | Yes (Composio HA integration) | Yes | Composio-backed | MIT | May 2026 |
| 5 | **n8n + AI Agent nodes + Telegram trigger** | Workable (single-user templates exist) | Triggers for TG, WA, voice (Whisper node), webhooks | Via HA REST node | Yes — via webhook or MCP node (2026 added) | Postgres / SQLite built-in | Sustainable-use license (free self-host) | May 2026, weekly |
| 6 | **LangGraph supervisor pattern** | Library, not a platform — you build the hub | Whatever you wire | Whatever you wire | Yes — subprocess/HTTP | Bring your own (Postgres common) | MIT | May 2026 |
| 7 | **CrewAI** | Library; weak for long-running daemons | DIY | DIY | Yes | DIY | MIT | May 2026 |
| 8 | **AutoGen 0.4 / Magentic-One** | Designed for orchestration but team-shaped | DIY channels | DIY | Yes | DIY | MIT | Active, but solver-focused not hub-focused |
| 9 | **OpenWebUI** | Chat UI not daemon | Web + mobile PWA; not voice-first | Indirect (HA MCP available) | Via MCP (v0.6.31+) | SQLite/Postgres | MIT-like | May 2026 |
| 10 | **LibreChat** | Chat UI | Web; mobile via PWA | Indirect via MCP | Via MCP (native, leading) | Postgres + Mongo | MIT | May 2026 |
| 11 | **AnythingLLM** | Chat UI + workspaces | Web; embed APIs | Via MCP (stdio/SSE/HTTP) | Via MCP | LanceDB/Postgres | MIT | May 2026 |
| 12 | **Open Interpreter / 01** | Single-user voice/desktop | Voice + ESP32 hardware | Via shell + HA REST | Via shell exec | Local | AGPL | May 2026 (active, pre-1.0) |
| 13 | **isair/jarvis** (private voice) | Yes (offline voice asst) | Voice on macOS/Linux | Via MCP | Via MCP | Local | MIT | 2026 |
| 14 | **Stanford OpenJarvis / Intelligence-Per-Watt** | Research; on-device focus | Voice | Indirect | Indirect | Local | Research artefacts | 2026 active |
| 15 | **Manus Agents in Telegram** | SaaS, single-user UX | TG (announced); WhatsApp roadmap | Indirect | No (closed) | Cloud-owned | Closed SaaS | Mar 2026 launch |

**Reading the table for Jarvis:**

- Rows 1, 2, 3 cluster as "what Jarvis v2 would look like".
- Rows 6, 7, 8 are libraries — would replace some of Jarvis's hand-rolled glue, not the architecture.
- Rows 9-11 are chat surfaces — useful if Jarvis grows a web UI, not load-bearing.
- Rows 12-14 are voice-first niches; useful as components, not as the hub.
- Row 15 is the closed-SaaS comparator; ignore for an owner-controlled hub.

---

## 3. Channels — what the I/O surface actually looks like

### 3.1 Telegram-first is the consensus

Every reference implementation (OpenClaw, psibot, the n8n "Angie" / "Personal Life Manager" templates, Manus) starts with Telegram. Reasons consistent across them:

- **Free, push-capable, multi-device** — meets the "I'm AFK, message me when X" requirement at zero marginal cost.
- **Bot API mature** — no app review, no platform politics.
- **Multimodal already** — voice notes, images, files, inline keyboards for confirmations.
- **Group chats = collaboration surface** — a single hub can be invited into a planning chat with humans.

Jarvis already has a Telegram MCP server and uses it. The honest assessment: this is "channel 1 of N" of a v2 architecture, already shipped.

### 3.2 Voice — the realistic path is Home Assistant Voice PE, not bespoke

State of the art for self-hosted voice in 2026 ([Home Assistant blog](https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/), [Joe Karlsson local voice writeup](https://www.joekarlsson.com/blog/local-voice-ai-home-assistant-gpu/)):

- **Wyoming protocol** standardises STT/TTS/wake-word as separate processes, network-addressable.
- **Whisper.cpp / faster-whisper** for STT — sub-second on GPU, ~2-3s on CPU.
- **Piper** for TTS — local, fast, intelligible.
- **Voice PE hardware** (Home Assistant's official puck) is the no-glue option.
- **HA Assist pipeline** wires it all together with LLM Conversation agent (Ollama / Anthropic / OpenAI).

Building a parallel voice stack without HA is reinventing a maintained pipeline. The Jarvis-shaped move is: HA owns the voice pipeline, exposes "user said X" as a webhook/event to Jarvis, Jarvis decides routing. This keeps HA in its lane (devices + voice glue) and Jarvis in its lane (LLM orchestrator).

### 3.3 Desktop control — already covered

Jarvis has computer-use MCP. This channel is solved at the layer below; v2 doesn't add anything here.

### 3.4 Web UI — defer

The literature is consistent: a chat web UI (OpenWebUI, LibreChat) is the LAST channel to add for a personal hub, not the first. Telegram + voice covers ~90% of interactions; the web UI is mainly for debugging/replay. Defer until there's a measured need.

### 3.5 The realistic Jarvis v2 channel sheet

| Channel | Who owns | Today | v2 |
|---|---|---|---|
| Telegram | Telegram MCP / Jarvis | ✅ | unchanged |
| Voice | not present | ❌ | HA Voice PE + HA event → Jarvis |
| Smart home device events | not present | ❌ | HA event bus → Jarvis webhook |
| Desktop | computer-use MCP | ✅ | unchanged |
| Push notifications | Telegram | ✅ | unchanged (TG covers it) |
| Mobile actions | HA companion app | ❌ | HA app → HA → Jarvis |
| Web chat | none | ❌ | deferred |

---

## 4. Routing — supervisor patterns and cost/latency

### 4.1 The three-tier router

Production consensus ([truefoundry](https://www.truefoundry.com/blog/what-is-llm-router), [MindStudio three-tier guide](https://www.mindstudio.ai/blog/set-up-ai-model-router-llm-stack-c2610), [getmaxim top-5 routing](https://www.getmaxim.ai/articles/top-5-llm-routing-techniques/)):

| Tier | Tech | Latency | Cost | When |
|---|---|---|---|---|
| 1 | Rules / regex on intent | 10-50 ms | ~free | Obvious patterns ("turn off X") |
| 2 | Embedding cosine over specialist descriptions | 50-200 ms | tiny (one embedding call) | Ambiguous but topical |
| 3 | LLM-as-router (Haiku-tier) | 500-2000 ms | $0.001-0.005/call | Genuinely novel intent |

The cheap move is **tier 1 first, fall through**: rules catch 60-80% of routine interactions at near-zero latency, embedding catches another ~15%, LLM-router handles the residual. Teams that adopt this report 60-80% inference cost cut and improved p95 latency.

### 4.2 Mapping to Jarvis

Today Jarvis routes via **Claude Code's skill-description matching in the system prompt** — which is Tier 3 with extra steps (full context prompt, not a cheap classifier call). At single-user volume the cost is negligible, but it means every "turn off the kitchen light" message currently has to traverse the same Sonnet prompt that handles "/implement #631".

A v2 supervisor layer above CC would:

1. **Tier 1 rules** handle smart-home verbs ("turn on/off/dim X"), news commands ("brief me on Y"), trivial Q&A from cache.
2. **Tier 2 embedding** matches the user query against a small registry of specialist descriptions: `{cc-dev, ha-smarthome, news-reader, note-keeper, scheduler, ...}`.
3. **Tier 3 LLM-router** (Haiku) only fires for genuinely ambiguous routing — and only to pick the specialist, not to execute the work.
4. The picked specialist runs in its own context with its own tools.

CC remains as one specialist among ~5-7, not as the kernel.

### 4.3 Magentic-One as the supervisor reference

Microsoft's Magentic-One ([Microsoft Research](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/), [Semantic Kernel doc](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-orchestration/magentic)) is the cleanest published supervisor pattern: an Orchestrator maintains a Task Ledger (facts + plan) and Progress Ledger (steps + reflection), assigns subtasks to specialists (WebSurfer, FileSurfer, Coder, Terminal), reflects after each step, replans on stuck-detection. This is heavier than Jarvis needs for one user, but the Task-Ledger / Progress-Ledger split is a transferable pattern — particularly for autonomous-loop work.

### 4.4 isair/jarvis's Tool Router as the lean precedent

[isair/jarvis](https://github.com/isair/jarvis) ships a Tool Router that uses a small LLM to pick which tools are relevant per query, shrinking the catalogue the chat model sees — explicitly avoiding "context rot". This is exactly the pattern jarvis's `skill-discovery-routing-2026-05-18.md` deep-dive recommends (vector RAG + LLM router). isair/jarvis proves it ships at solo-dev scale.

---

## 5. Persistence layer

### 5.1 The pattern that wins

Across every 2026 source (Supabase Agents, Tiger Data, MindStudio AI memory system, OpenBrain, n8n dual-layer template):

- **Shared semantic memory** — facts, preferences, learnt patterns — in Postgres + pgvector. One row per fact + embedding.
- **Per-specialist episodic memory** — what each agent did, in its own table or namespaced rows. Postgres JSONB is the lazy-good default.
- **Working state** — short-lived, can be Redis or just a Postgres row with TTL.
- **Wiki / facts surface** (Karpathy) — Obsidian or a `docs/wiki/` directory if the user wants browsable knowledge.

### 5.2 Jarvis's existing Supabase schema fits

Jarvis already runs Supabase with `memory_store`, `memory_recall`, `record_decision`, `outcome_record`. The schema (cross-device, source-of-truth, MCP-fronted) is **already the v2 persistence layer**. The work to "add v2 persistence" is zero — it's there. What might need adding:

- A `specialist` dimension on memory rows so per-specialist episodic queries are fast (today everything is `project`-scoped).
- A `channel` dimension so "what was said over TG today" is a cheap query.
- A `pgvector` upgrade if not already enabled (memory_recall implies semantic search; assume already done).

Single-user scale (the realistic upper bound: ~10⁵ memory rows, ~10⁴ decisions, ~10⁶ event logs over years) is two orders of magnitude below where pgvector starts to need optimisation. **No sharding needed**.

### 5.3 The Karpathy wiki as a complement, not a replacement

The "LLM compiles raw sources into structured wiki" pattern is good for **facts the user wants to read**. It is not a substitute for episodic memory or decision logs. The Jarvis stance: Supabase = queryable state, wiki/`docs/` = readable knowledge, both are needed. (This already matches today: `CONTEXT.md`, ADRs, decision memory.)

---

## 6. Safety for an unattended top-layer orchestrator

### 6.1 The risk profile changes when actuation enters

Current Jarvis-as-CC has a narrow blast radius: bad calls produce bad PRs, which a human reviews. Adding smart-home actuation (lights, locks, thermostats, security) and unattended scheduled actions changes this — agent errors become physical-world events. Sources consistently flag this as the safety-critical transition ([Frontier Enterprise](https://www.frontier-enterprise.com/ai-agent-autonomy-needs-human-control-and-guardrails/), [Help Net Security 2026](https://www.helpnetsecurity.com/2026/03/03/enterprise-ai-agent-security-2026/)).

### 6.2 The four universal guardrail patterns

From [guardrails.md](https://guardrails.md/) and the 2026 agent-safety literature:

1. **Artifact verification** — human approval for destructive or irreversible operations. For Jarvis v2: actuation that affects others (security, exterior doors) or non-reversible (purchases, sent messages, posted content) gates through Telegram confirmation. Reversible/low-impact (dim a lamp) auto-executes.
2. **Context rotation** — periodic resets so prompt-injection or context pollution doesn't accumulate. Jarvis's session model + `/end` reconciliation already does this for CC; v2 would extend it to the supervisor process.
3. **Privilege boundaries** — explicit per-specialist tool access. The dev specialist (CC) gets shell + git; the smart-home specialist gets HA REST only. No cross-pollination.
4. **Rate limiting** — cap actions per minute/hour. Cheap to implement, blocks the worst failure mode (action cascade / runaway loop).

### 6.3 Regulatory backdrop — EU AI Act Article 14

[EU AI Act Article 14](https://artificialintelligenceact.eu/article/14/) takes binding effect Aug 2 2026. It mandates "effective oversight by natural persons" for high-risk AI systems. A personal hub is not formally "high-risk" under the Act (no commercial supply), but the patterns are still the relevant compliance vocabulary — and if any of Jarvis's components ever ship as a product, this is the bar.

The pragmatic read for a solo-dev personal hub: **adopt the patterns, skip the paperwork**.

### 6.4 What's hard

- **Prompt injection via news channel content** — if Jarvis v2 reads RSS / web pages and an article contains "ignore previous instructions and ...", that's now an input from the open internet to the orchestrator. Sandboxing reader output (treat as data, never as instruction) is the only durable defence.
- **Group chat injection** — anyone added to a TG group could try to talk to the bot. Telegram MCP's access policy (per `~/.claude/CLAUDE.md` notes) already addresses this for the current setup; v2 maintains it.
- **Action cascades** — the "agent loop that bought 50 things" failure mode. Rate limits + confirmation thresholds.

---

## 7. Minimal Jarvis V2 spec (if starting today)

The smallest viable architecture that adds the three claimed v2 capabilities (smart home, news, multi-specialist routing) on top of today's stack.

### 7.1 Components

```
┌────────────────────────────────────────────────────────────┐
│  Channels (input)                                          │
│  • Telegram (existing MCP)                                 │
│  • HA event webhook (voice transcriptions, button presses) │
│  • Scheduled-tasks (existing) for time-triggered tics      │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  Supervisor (new) — thin Node/Python daemon                │
│  • Tier-1 rules router (regex on intent verbs)             │
│  • Tier-2 embedding router (specialist descriptions)       │
│  • Tier-3 Haiku LLM-router (residual)                      │
│  • Privilege gate (which specialist can do what)           │
│  • Rate limiter (per-specialist + global)                  │
│  • Confirmation gate (sends TG inline-keyboard if needed)  │
└────────────────────────────────────────────────────────────┘
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
   ┌───────────────┐ ┌──────────┐ ┌────────────┐
   │ CC (dev)      │ │ HA       │ │ news-read  │
   │ specialist    │ │ specialist│ │ specialist │
   │ Claude Agent  │ │ HA REST   │ │ RSS+LLM    │
   │ SDK            │ │ + Assist  │ │ summariser │
   └───────────────┘ └──────────┘ └────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────┐
│  Persistence — Supabase (existing)                         │
│  • shared semantic memory (pgvector)                       │
│  • per-specialist episodic (specialist column)             │
│  • decision log (existing)                                 │
│  • event log (channel-tagged)                              │
└────────────────────────────────────────────────────────────┘
```

### 7.2 What's actually new code

- **Supervisor daemon** — ~500-1500 lines of TS or Python. Could be built on LangGraph supervisor primitives, OpenClaw forked thin, or hand-rolled. Hand-roll is plausible at this scale.
- **HA integration** — a thin HA add-on / shell command that POSTs HA events to the supervisor webhook. ~50 lines.
- **News specialist** — an MCP server or scheduled task that fetches RSS, summarises with Haiku, writes summaries to memory + Telegram brief. ~200 lines.
- **Embedding index of specialist descriptions** — table in Supabase, populated on supervisor start. ~50 lines.

Everything else (memory, decision log, TG, computer-use, CC itself) is reused as-is.

### 7.3 What v2 deliberately does NOT add

- No new chat UI (web).
- No new LLM provider (Claude Max already covers cost).
- No new database (Supabase scales).
- No multi-user / auth — single-user forever.
- No "kernel rewrite" — CC stays as the dev specialist, not as the hub.

### 7.4 Effort estimate

If actually built: 2-3 weekends of solo-dev time for v0 (TG + HA + supervisor + one specialist beyond CC). Another 1-2 weekends per added specialist. Total to "feels like the v2 from the description": ~6-10 weekends spread over 2-3 months.

This is small enough that the build-or-buy question is real: forking psibot or secure-openclaw might save half of it. The cost of forking is dependence on someone else's API stability; the win is not maintaining the gateway code.

---

## 8. Trigger conditions — when does Jarvis-as-CC stop being enough?

V2 starts paying for itself only when CC's single-specialist shape becomes the bottleneck. Measurable signals, in plausibility order:

1. **≥3 active non-dev channels.** Today: 1 (Telegram, used as thin CC pass-through). When the owner is actively using HA voice + a news brief + Telegram for non-dev intents, the single-specialist routing breaks down.
2. **≥1 unattended actuating decision/day.** Today: 0 (scheduled tasks exist but only nudge the owner; nothing actuates physical world or sends external comms without confirmation). When unattended actuation becomes a daily thing, the supervisor's privilege/rate/confirmation gates earn their keep.
3. **≥2 specialists with their own non-trivial state.** Today: 1 (CC + skills). When a second specialist (HA, news, finance) accumulates its own memory and would benefit from isolation, the shared CC context becomes a liability.
4. **Recurring intent-routing miss rate ≥10%.** Measurable: count cases where CC picked the wrong skill or no skill, divided by total interactions. When this exceeds ~10% sustained, a tiered router pays off.
5. **Cost approaches the threshold where Haiku-router saves money.** With Max subscription, this is currently not a constraint. Becomes relevant if Jarvis ever moves to per-token billing.
6. **Latency on routine intents >3s p50.** Today CC's Sonnet pass adds 2-5s to even trivial "what time is it" — fine for the dev workflow, friction for ambient assistant use. When voice/smart-home use makes this visible, Tier-1 rules win.
7. **Cross-specialist memory queries become common.** "Did I tell HA to turn the heat down before I started coding?" — when this kind of query starts to matter, the unified-supabase-with-specialist-tags model needs to be enforced (today only convention).

**None of these hold today.** Below the threshold the cheaper move is: grow CC's skill set, add the Telegram-pass-through patterns that already work, defer the supervisor layer.

---

## 9. Watch list — projects to track for v2 priors

| Project | Why | Cadence |
|---|---|---|
| **psibot** | Closest "Jarvis-shaped" reference; built on the same CC + Max + TG primitives | Watch monthly; fork if it stabilises |
| **OpenClaw / secure-openclaw** | Most-featured open hub; demonstrates the channel-adapter pattern at scale | Watch quarterly |
| **Home Assistant Voice + Conversation roadmap** | Determines how much voice/smart-home glue Jarvis must own vs reuse | Watch monthly (HA monthly releases) |
| **Open Interpreter 01** | The voice + desktop niche; pre-1.0, may consolidate | Watch quarterly |
| **isair/jarvis Tool Router** | Implementation reference for tier-2 embedding routing in a personal hub | Read on next refactor |
| **Karpathy LLM-OS posts / LLM Knowledge Base evolution** | Shapes the "facts surface" half of the persistence story | Watch monthly |
| **Stanford OpenJarvis / Intelligence-Per-Watt papers** | On-device LLM efficiency — determines if a fully-local v2 ever becomes practical | Watch quarterly |
| **n8n agent-node + MCP-node** | Workflow-y alt-architecture; if it lands a clean MCP+supervisor pattern, may replace some hand-rolled code | Watch monthly |

---

## 10. Proposals

Numbering continues from prior B-topics. Most are **research / watch** items because v2 isn't being built today.

### [B3-1] Defer the v2 build until ≥2 of §8 trigger conditions hold — P0

Don't pre-build the supervisor. Today's Jarvis-as-CC + Telegram MCP + Supabase covers the actual usage. Pre-building costs weekends now, locks in design choices before the requirements crystallise, and is the textbook "speculative generality" anti-pattern. Re-evaluate every 6 months against the §8 conditions.

### [B3-2] Watch psibot and OpenClaw monthly; fork-or-pattern-match decision in 6 months — P1

These are the two closest references. Three things to track per visit: (a) channel adapter quality (do they handle TG well enough that we'd drop our MCP?), (b) supervisor routing pattern (rules-first? embedding? LLM?), (c) Claude Agent SDK integration shape. If either project stabilises with a license + maintainer pattern compatible with Jarvis's stack, fork beats build.

### [B3-3] Stand up a Home Assistant instance in shadow mode for ≥3 months before committing — P1

If smart-home is the v2 entry point, the dependency on HA is load-bearing. Run HA on a Pi or VM, integrate 5-10 devices, use HA Assist + Ollama (or Anthropic Conversation), measure: actuation latency, false-positive rate of voice triggers, intent-classification quality of the LLM Conversation agent. Three months of shadow use generates real numbers; below that we're guessing.

### [B3-4] Prototype the tier-1/2/3 router as a standalone skill INSIDE current Jarvis — P1

Don't wait for v2 to test the routing pattern. Build `/route` as a skill that takes a user query and outputs `(specialist, confidence, tier_used)`. Specialists today are skills, but the routing logic is portable. This validates the embedding-table pattern at zero cost and is reusable in v2.

### [B3-5] Add `channel` and `specialist` columns to Supabase memory rows now — P2

The schema migration is cheap; doing it post-hoc when v2 demands it is painful. Backfill existing rows with `channel='cc'` / `specialist='cc'`. Cost: 1 migration + memory-server tweak. Value: future-proofs the persistence layer without committing to v2.

### [B3-6] Research RSS / news-feed aggregation patterns separately — P2

The "news" capability is the most under-defined part of the v2 description. What does "personal news feed" mean here — daily Telegram brief? RSS reader with LLM summary? Twitter/X firehose filter? Mark Pocock / Simon Willison patterns? Spin up a separate ~200-line research doc when the question becomes concrete. Today: not concrete enough to design against.

### [B3-7] Treat smart-home actuation as a separate safety design exercise — P2

Article 14 / four-pattern guardrails apply specifically once Jarvis can flip physical-world switches. Before that capability ships, run a one-page design review: confirmation thresholds per device class, rate limits, revocation path, audit log. Cheap if done before the integration; expensive if retrofitted.

### [B3-8] Don't build a web UI in v2 — P3 (decision-record)

All evidence points to web chat being a low-value channel for a single-user hub at this stage. TG + voice + scheduled briefs covers the surface. Record the decision now so future-self doesn't drift into building one.

### [B3-9] If v2 builds, prefer fork-psibot over from-scratch — P3 (decision-record)

The single-user TG + Mac daemon + Claude Agent SDK shape is psibot's exact niche. If/when triggers fire, the cheapest path is fork psibot, replace TG MCP wiring with what Jarvis already has, add HA webhook adapter, add Supabase episodic adapter. Build-from-scratch costs ~3× more weekends and gets the same shape.

### [B3-10] Run a Karpathy-style wiki experiment in parallel — P3

Karpathy LLM-Knowledge-Base + Obsidian + Claude-Code is a $0 experiment: point CC at `docs/wiki/` and let it incrementally compile drop-zone material. If it works, it solves the "facts surface" half of the v2 story before v2 exists. Low risk: deletable, doesn't entangle with current architecture.

---

## 11. Don't-do list

1. **Don't conflate Jarvis V2 with "rebuild Claude Code".** V2 is a supervisor ABOVE CC, not a replacement. The current CC + skills + Supabase shape is the most valuable Jarvis-asset; preserving it is the entry-condition.
2. **Don't build the smart-home glue.** Home Assistant exists, is mature, has the device integrations, has voice. Reinventing means months of un-fun yak-shaving and a worse product. V2's smart-home channel = HA + thin webhook.
3. **Don't pick a multi-agent framework before knowing the specialists.** LangGraph, CrewAI, AutoGen, Magentic-One are all defensible *if* you know what 4-6 specialists you have. With only "CC + something" defined, picking the framework is premature commitment.
4. **Don't add a web chat UI as channel #2.** Telegram + voice covers the realistic surface. Web UI is the channel everyone builds first and uses last.
5. **Don't start unattended actuation without the four guardrail patterns.** Artifact verification, context rotation, privilege boundaries, rate limiting. Skipping any of them is the line between "useful" and "newsworthy in a bad way".
6. **Don't try to make CC the supervisor.** CC is excellent at being one specialist (dev). Forcing it to also route 4-6 other specialists pushes it past its competence and past the skill-listing budget (per the [skill-discovery-routing](skill-discovery-routing-2026-05-18.md) deep-dive).
7. **Don't fork OpenClaw for the multi-channel adapter set.** ~20 channels = ~20 things to keep current with platform changes. Jarvis needs 2-3 channels well, not 20 sort-of.
8. **Don't pre-shard Supabase.** Single-user scale is two orders of magnitude under where sharding matters. Plan for it when row counts hit 10⁷, not now.
9. **Don't treat the news channel as a solved problem.** "Personal news feed" is under-specified; designing against the wrong spec wastes more time than waiting for the requirement to firm up.
10. **Don't lose the queryable-decision-log discipline.** The reason Jarvis-as-CC works is the decision/memory layer is queryable and disciplined. V2 maintains that or it breaks the core asset.

---

## 12. Open questions

- **Voice — local or cloud?** HA Assist supports both. The local stack (Whisper.cpp + Piper + Ollama) is private but slower and requires GPU for sub-second. Cloud is faster, leaks audio. Trigger condition: actually use it before deciding.
- **Does the owner want "ambient" actuation (Jarvis decides things) or "responsive" actuation (Jarvis suggests, owner confirms)?** The four-guardrail design changes substantially. Most personal-hub literature defaults to responsive; ambient is a step the owner has to actively want.
- **What's the failure mode that triggers building v2 vs. just adding another CC skill?** Today every gap is solved by a new skill. The article-of-faith claim is that this stops scaling; the measurable test is §8 conditions.
- **Is there a meaningful difference between "specialist agent" and "MCP server with a fat prompt"?** For some specialists (news reader, scheduler) the latter may be sufficient. The supervisor still routes; the specialist is just a fancy MCP. This collapses v2 complexity meaningfully.
- **Does Karpathy's LLM-as-compiler wiki replace some of Jarvis's docs-as-contract discipline, or complement it?** Both have a "structured markdown source of truth" thesis. Experiment in [B3-10] should answer this.

---

## Sources

### Direct fetches (high confidence)
- [Home Assistant — Building the AI-powered local smart home (Sept 2025)](https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant/)
- [Home Assistant — Ollama integration docs](https://www.home-assistant.io/integrations/ollama/)
- [OpenClaw — openclaw.ai](https://openclaw.ai/)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [psibot — DmacMcgreg/psibot](https://github.com/DmacMcgreg/psibot)
- [secure-openclaw — ComposioHQ](https://github.com/ComposioHQ/secure-openclaw)
- [isair/jarvis (private voice + Tool Router)](https://github.com/isair/jarvis)
- [Open Interpreter / 01](https://github.com/openinterpreter/01)
- [Magentic-One — Microsoft Research](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
- [Magentic Orchestration — Semantic Kernel docs](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-orchestration/magentic)
- [Karpathy LLM Knowledge Base — VentureBeat](https://venturebeat.com/data/karpathy-shares-llm-knowledge-base-architecture-that-bypasses-rag-with-an)
- [Simon Willison's weblog](https://simonwillison.net/)
- [guardrails.md — Safety Protocol for Autonomous Agents](https://guardrails.md/)
- [EU AI Act Article 14 — human oversight](https://artificialintelligenceact.eu/article/14/)

### Search-snippet sources (medium confidence — verify before load-bearing use)
- [TrueFoundry — What is LLM Router](https://www.truefoundry.com/blog/what-is-llm-router)
- [MindStudio — Three-Tier LLM Routing](https://www.mindstudio.ai/blog/set-up-ai-model-router-llm-stack-c2610)
- [getmaxim — Top 5 LLM Routing Techniques](https://www.getmaxim.ai/articles/top-5-llm-routing-techniques/)
- [Tiger Data — Building AI Agents with Persistent Memory](https://www.tigerdata.com/learn/building-ai-agents-with-persistent-memory-a-unified-database-approach)
- [Supabase for Agents](https://supabase.com/solutions/agents)
- [MindStudio — OpenBrain personal memory DB](https://www.mindstudio.ai/blog/what-is-openbrain-personal-ai-memory-database)
- [Joe Karlsson — Local Voice AI on Home Assistant](https://www.joekarlsson.com/blog/local-voice-ai-home-assistant-gpu/)
- [Manus Agents in Telegram (Mar 2026)](https://manus.im/blog/manus-agents-telegram)
- [LangGraph vs CrewAI vs AutoGen 2026 — Pooya Golchian](https://pooya.blog/blog/crewai-vs-langgraph-autogen-comparison-2026/)
- [LLM chat UIs that support MCP — ClickHouse blog](https://clickhouse.com/blog/llm-chat-mcp-support)
- [Open WebUI MCP docs](https://docs.openwebui.com/features/extensibility/mcp/)
- [Frontier Enterprise — AI agent autonomy and guardrails](https://www.frontier-enterprise.com/ai-agent-autonomy-needs-human-control-and-guardrails/)
- [Help Net Security — Enterprise AI agent security 2026](https://www.helpnetsecurity.com/2026/03/03/enterprise-ai-agent-security-2026/)
- [Intent Recognition & Auto-Routing in Multi-Agent Systems — gist](https://gist.github.com/mkbctrl/a35764e99fe0c8e8c00b2358f55cd7fa)
- [AGI House — agihouse.ai](https://agihouse.ai/)

### Internal references
- `docs/research/skill-discovery-routing-2026-05-18.md` — routing patterns at the skill layer; reused for v2 supervisor design
- `docs/research/memory-architecture-deep-dive-2026-05-18.md` — persistence patterns
- `docs/research/single-vs-multi-agent-architecture-2026-05-18.md` — supervisor vs single-agent priors
- `CLAUDE.md` §"Engineering posture", §"Skill routing"
- `~/.claude/CLAUDE.md` Memory & decision protocol (decision-log discipline carries through to v2)
