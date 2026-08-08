---
title: DeepSeek & local models for token cost reduction — 2026 H1
date: 2026-05-18
status: draft
depth: deep-dive
sources_count: 28
adjacent_topics_flagged:
  - embedding-cost-reduction-voyageai-vs-self-hosted
  - claude-code-subagent-auth-leak-issue-39903
  - moa-mixture-of-agents-for-jarvis-grill-skill
  - speculative-decoding-cline-claude-code
  - litellm-vs-claude-code-router-tradeoffs
  - cross-device-3rig-hardware-audit-for-local-llm
---

## TL;DR (≤200 words)

For a Claude Max subscriber, **the token cost of Claude Code itself is effectively $0 at the margin** — Anthropic's policy ([support docs](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)) covers all CC + claude.ai under one shared rate limit. Routing CC traffic to DeepSeek does **not save money** for you; it only saves *Anthropic's* limit budget, and the "$1200 → $60/year" testimonials are aimed at people paying per-token, not Max users.

The real cost-reduction targets are different:

1. **External APIs you pay per-call** — VoyageAI embeddings, any custom MCP-server LLM calls, scheduled tasks that don't run under your Max session. This is your $20/mo bucket.
2. **Burst protection for Max rate limits** — when you hit weekly caps, an OpenRouter/DeepSeek fallback keeps work flowing without paying Anthropic API rates.
3. **Local model for privacy-sensitive bulk transforms** — log scrubbing, repo summarization on PII-touching code.

Patterns worth investigating: `claude-code-router` background-route to DeepSeek (only useful when you're API-billed); self-hosted embeddings via `nomic-embed-code` or `Qwen3-Embedding` to kill the VoyageAI bill; speculative-decoding-style "cheap draft, Claude verify" for `/grill` and `/critic` subagents.

The work-to-savings ratio for routing CC traffic itself is poor while Max covers you. Wire the fallback for the day you outgrow Max.

## Landscape (800-1500 words)

### Models & pricing — 2026 H1 snapshot

**DeepSeek (current line):** DeepSeek shipped **V4-Pro (1.6T params, 49B active)** and **V4-Flash (284B, 13B active)** on 2026-04-24 ([release notes](https://api-docs.deepseek.com/news/news260424)), both MIT-licensed open weights, 1M-token context, 384K max output, dual think/non-think modes. Official API pricing ([api-docs](https://api-docs.deepseek.com/quick_start/pricing)):

| Model | Input (cache miss) | Input (cache hit) | Output |
|---|---|---|---|
| `deepseek-v4-flash` | $0.14/M | $0.0028/M | $0.28/M |
| `deepseek-v4-pro` *(promo until 2026-05-31)* | $0.435/M | $0.003625/M | $0.87/M |
| `deepseek-v4-pro` *(post-promo)* | $1.74/M | — | $3.48/M |

The **cache-hit rate is the killer feature**: input cached tokens are 1/50th of fresh, which is decisive in agentic loops where you re-send the same system prompt + context window every turn. V4-Pro at 80.6% SWE-bench Verified ([benchmark coverage](https://benchlm.ai/compare/claude-sonnet-4-6-vs-deepseek-v4-pro-high)) is within 0.2pp of Claude Opus 4.6 and 2.4pp ahead of Sonnet 4.6 — that gap closed in H1 2026.

Earlier DeepSeek versions remain live and cheaper on third-party routers: V3.2 is $0.27/$0.41 on OpenRouter, V3.1 $0.21/$0.79 ([OpenRouter DeepSeek](https://openrouter.ai/deepseek)).

**Qwen 3 Coder:** Alibaba's Qwen3-Coder line ships in three relevant tiers ([Qwen blog](https://qwenlm.github.io/blog/qwen3-coder/)):
- **Qwen3-Coder-Next** (Feb 2026): $0.11/$0.80, 262K context — best price/quality for FIM
- **Qwen3-Coder-480B-A35B** (Jul 2025): $0.22/$0.90, 262K context — SOTA open-source on SWE-bench, "comparable to Claude Sonnet 4" on agentic tool-use
- **Qwen3-Coder-Plus** (Sep 2025): $0.65/$3.25, 1M context — for long-repo work

**Access paths.** Cheapest direct-API: official DeepSeek and Alibaba/DashScope. Cheapest aggregated: DeepInfra (FP4 quantized DeepSeek V3.1 at $0.35 blended). Fastest: Together.ai (260 t/s, 0.61s TTFT) and SambaNova (181 t/s) for V3.1 ([Artificial Analysis providers](https://artificialanalysis.ai/models/deepseek-v3-1/providers)). OpenRouter is the practical default for jarvis-style hybrid setups — one API key, model swap by string.

**OpenRouter free tier:** `deepseek/deepseek-v4-flash:free` is literally $0/$0 with rate caps, useful for the "always-on background route" pattern.

**Embeddings** are a separate market and the real money-saver for jarvis. VoyageAI ([pricing](https://docs.voyageai.com/docs/pricing)) gives 200M free tokens then $0.02-$0.12/M depending on model. Alternatives: self-hosted `NV-Embed-v2` at ~$0.001/M ([20× cheaper claim, Elephas](https://elephas.app/blog/best-embedding-models)), OpenAI text-embedding-3-small with batch 50% discount, Cohere Embed-v4 for multimodal.

### Hardware reality for local

For a typical Windows dev rig in 2026, expect three viable tiers ([InsiderLLM 2026 guide](https://insiderllm.com/guides/best-local-coding-models-2026/)):

- **8GB VRAM (4060/3070):** Qwen 2.5-Coder 7B for FIM/autocomplete (~5GB Q4), 88% HumanEval. Can serve Continue.dev's autocomplete role. Forget agentic models at this tier.
- **16GB VRAM (4060 Ti/5060 Ti):** Qwen 3.6-35B-A3B MoE at 16-22GB Q4 with 73% SWE-bench Verified. Combo: 14B coder for FIM + 35B-A3B for chat, swapping not co-running.
- **24GB VRAM (3090/4090/5090):** sweet spot. Qwen 3.6-27B dense at 17GB Q4, 77% SWE-bench — strong enough for real agentic work. Qwen 2.5-Coder 32B (20GB Q4) for autocomplete. Around 100 tok/s on 3090.

**Frontier open weights are not local.** DeepSeek V4-Flash needs ~150GB. Qwen3-Coder-480B needs 250GB unified memory in Q4 ([Ollama page](https://ollama.com/library/qwen3-coder:480b)). Aggressive quantization (UD-IQ1) can fit 480B in ~30GB RAM but speed is "experimental" — not a real workflow.

**CPU-only:** 7B Qwen2.5-Coder on a modern desktop CPU runs 5-18 tok/s ([llama.cpp discussion](https://github.com/ggml-org/llama.cpp/discussions/3847)) — fine for batch summarization, painful for chat.

### Routers and proxies — three real options

1. **`claude-code-router`** (musistudio, 26k stars, MIT — [repo](https://github.com/musistudio/claude-code-router)) is the project. Intercepts CC's Anthropic-protocol calls, routes by **scenario tag** (`default` / `think` / `background` / `longContext` / `webSearch` / `image`) to any provider. Subagent-level routing via `<CCR-SUBAGENT-MODEL>provider,model</CCR-SUBAGENT-MODEL>` prompt prefix. Install: `npm install -g @musistudio/claude-code-router`, run as `ccr code` instead of `claude`.
2. **LiteLLM proxy** ([docs](https://docs.litellm.ai/docs/tutorials/claude_responses_api)) is the universal translator — accepts Anthropic-format from CC, translates to OpenAI/OpenRouter/Ollama/anything. Heavier (full proxy daemon), but adds cost logging, load balancing, fallback chains, and 100+ provider integrations out of the box.
3. **Env-var swap** (no extra software): just set `ANTHROPIC_BASE_URL=https://openrouter.ai/api` + `ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek/deepseek-chat-v3` etc. ([techsy guide](https://techsy.io/en/blog/claude-code-use-different-models)). Crude but works for blanket model swaps. **Breaks Max subscription billing** — once you set `ANTHROPIC_API_KEY` or `BASE_URL`, CC routes everything to the API and ignores your Max session.

### The Max-plan billing trap

Per [issue #39903](https://github.com/anthropics/claude-code/issues/39903), a Max-20x user got hit with **$152 in unexpected API charges** in 5 sessions because CC's Agent-tool subagents picked up an `ANTHROPIC_API_KEY` from `~/.env` (set for an unrelated app) and silently bypassed the Max subscription. The bug is open. Practical implication for jarvis: any setup that touches `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL` env vars needs to be **isolated per-shell** (e.g. only set inside a wrapper script that runs the cheap-routed CC, never in user profile).

### Quality gap in 2026 H1

For pure-code generation and "edit this function" work: **DeepSeek V4-Pro and Qwen3-Coder-480B have closed the gap with Sonnet/Opus on benchmarks** (SWE-bench Verified within 2-3pp). For multi-step agentic loops with tool calls, **Claude is still meaningfully ahead** — the gap shows up in tool-call reliability and recovery from bad states, not in single-shot code. DeepClaude users report ([gncrypto coverage](https://www.gncrypto.news/news/deepclaude-claude-code-deepseek-17x-lower-cost/)): "parallel tool calls disabled, MCP server integrations don't pass through, vision unsupported, hard reasoning tasks Claude Opus remains stronger." Translation: **the harder the agent loop, the more you pay for Claude — and it's still worth it.**

### Patterns in the wild

- **Aider** ships a three-role model split: `--model` (main), `--editor-model` (apply changes), `--weak-model` (commit messages, summaries) ([Aider docs](https://aider.chat/docs/config/adv-model-settings.html)). Common pattern: R1 main + V3 editor + V3 weak, or Sonnet main + DeepSeek weak.
- **Cline** has Plan/Act split — many users plan with R1/DeepSeek (cheap thinking) then act with Sonnet ([Cline docs](https://docs.cline.bot/provider-config/deepseek)).
- **Continue.dev** has six roles (chat/autocomplete/edit/apply/embed/rerank) — autocomplete is small/local/FIM, others can be cloud ([Continue docs](https://docs.continue.dev/customize/model-roles/autocomplete)).
- **Mixture-of-Agents (MoA)** ([Together paper](https://github.com/togethercomputer/moa)) — N cheap proposers + 1 aggregator beats GPT-4o on AlpacaEval. Layered architecture; could underwrite `/grill` or `/critic` subagent design but adds latency.
- **Speculative decoding** ([Redis primer](https://redis.io/blog/speculative-decoding-llm/)) — small draft model writes K tokens, big model verifies in parallel, 2-4× speedup on code patterns. Inference-engine feature, not an agent pattern — relevant only if jarvis self-hosts.

## Concrete patterns / recipes (3-7)

### 1. claude-code-router with scenario-based routing
**Source:** [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router), [polyskill guide](https://polyskill.ai/blog/claude-code-router)
**What gets routed where:**
```json
"Router": {
  "default":    "anthropic,claude-sonnet-4-6",
  "background": "deepseek,deepseek-chat",
  "think":      "anthropic,claude-opus-4",
  "longContext":"deepseek,deepseek-chat",
  "webSearch":  "gemini,gemini-2.5-pro"
}
```
File-summarization, repo-mapping, and tab-completion go to DeepSeek; reasoning and Plan-mode stay on Claude. **Cost result:** users on API billing report ~50× reduction on bulk-token routes; **for Max users this is rate-limit relief, not dollar savings**.

### 2. Aider three-role split (main / editor / weak)
**Source:** [aider.chat/docs](https://aider.chat/docs/config/adv-model-settings.html), [issue #3095](https://github.com/Aider-AI/aider/issues/3095)
**Routing:** `--model deepseek-reasoner` (architect plans), `--editor-model deepseek-chat` (executes the diff), `--weak-model deepseek-chat` (commit messages, history summaries). Or Sonnet main + DeepSeek weak — keep Sonnet on the hard work, push the boilerplate to V3/V4-Flash. **Result:** weak-model calls dominate token volume; cutting their cost is most of the savings.

### 3. Cline Plan/Act with DeepSeek-R1 → Sonnet
**Source:** [Cline + DeepSeek setup guide](https://www.knightli.com/en/2026/05/01/use-deepseek-v4-pro-in-cline/), [aisharenet comparison](https://aisharenet.com/en/cline-zuijiazuhe/)
**Routing:** Plan mode uses DeepSeek R1 (chain-of-thought reasoning, cheap), Act mode switches to Sonnet (tool-call reliability). **Quality:** users report Plan quality holds with R1; Act on cheap models causes tool-call failures and hallucinated file paths.

### 4. Background subagent route via CCR-SUBAGENT-MODEL
**Source:** [musistudio/claude-code-router README](https://github.com/musistudio/claude-code-router)
**Pattern:** prepend `<CCR-SUBAGENT-MODEL>deepseek,deepseek-chat</CCR-SUBAGENT-MODEL>` to a subagent's system prompt. CC's Task tool dispatch picks this up, runs the subagent on DeepSeek while keeping the main session on Claude. **Use case for jarvis:** `/grill` proposer pass, `/research` first-pass scan, anything embarrassingly parallel.

### 5. Self-hosted embeddings, kill the VoyageAI bill
**Source:** [Elephas embedding comparison](https://elephas.app/blog/best-embedding-models), [HuggingFace NV-Embed-v2 card]
**Pattern:** swap `voyage-3` or `voyage-code-3` for `nomic-embed-code` or `Qwen3-Embedding-8B` running on a local 16GB GPU. Quality on code-retrieval is within a few percent of voyage-code-3; cost goes from $20/mo to electricity. **Caveat:** requires a host that's always-up across 3 devices — Supabase pgvector still needs the embeddings produced somewhere. One option: nightly batch on the desktop, store embeddings server-side, query from any device.

### 6. Mixture-of-Agents for the `/grill` skill
**Source:** [Together MoA repo](https://github.com/togethercomputer/moa), [MoA paper](https://arxiv.org/html/2406.04692v1)
**Pattern:** N parallel DeepSeek V3.1 proposers generate diverse critiques of a plan; one Claude Opus aggregator distills the strongest objections. Empirical claim: this beats single-Opus by 8pp on AlpacaEval at lower cost. **Fit for jarvis:** `/grill` and `/critic` subagents specifically — both are "many cheap perspectives, one final synthesis" by design.

### 7. OpenRouter free V4-Flash as Max-burst fallback
**Source:** [OpenRouter DeepSeek V4 Flash free](https://openrouter.ai/deepseek/deepseek-v4-flash:free)
**Pattern:** when Max weekly limit alarms hit, flip a feature-flag that routes CC's `BASE_URL` to OpenRouter with `deepseek/deepseek-v4-flash:free` until the weekly window resets. Quality drop is real for agentic work, but it keeps you unblocked for routine edits and explanations.

## What this user should consider given his context

You're a Claude Max subscriber, 3 devices, with a clear $20/mo external-API budget and an explicit "be frugal with external API calls" rule in CLAUDE.md. That changes the math:

**Don't bother routing CC traffic itself.** Every dollar saved on routing-Claude-Code-to-DeepSeek is a dollar Anthropic doesn't bill you that they weren't going to anyway. The "$1200→$60/year" testimonials assume API-pay-as-you-go. The only real win is **rate-limit relief** — when you hit Max's weekly cap, a fallback router into OpenRouter free V4-Flash keeps you working. Wire it as a circuit breaker, not a default route.

**The real targets are in the $20 bucket and the cloud-scheduled-task layer:**
1. **VoyageAI embeddings.** This is your biggest controllable line item. Investigate self-hosted `nomic-embed-code` or `Qwen3-Embedding-8B` running nightly on whichever device has the GPU. Push embeddings into Supabase the same way you do now. Likely cuts $20/mo by 50-80%.
2. **Scheduled tasks running in the cloud** that don't load `.mcp.json` and bill against API. Per your CLAUDE.md, these already use `execute_sql` via Supabase connector — but any LLM calls inside them (e.g. autonomous-loop synthesis) hit your API key directly. Route those through OpenRouter with DeepSeek V3.2/V4-Flash; the quality gap on summarization is invisible.
3. **The Issue #39903 trap is live.** You probably have `ANTHROPIC_API_KEY` in shell profile somewhere across 3 devices. Audit and either remove it or scope it to specific wrappers — otherwise a subagent dispatch under Max can silently bill you triple-digits in a single session.

**MoA for `/grill` and `/critic`** is a genuinely interesting fit — the skill is already "many critical perspectives, one synthesis," and routing the proposers to DeepSeek while aggregating with Opus matches your engineering posture (verify-before-assuming) at much lower per-grill cost — *if* you eventually pay for those calls. For now it's a way to make `/grill` produce more diverse pressure without using more Max budget.

**Local models:** unless one of your 3 devices is a 4090/5090 box, skip. Mid-range Win 11 rigs don't run frontier-equivalent local. The exception is **embeddings** (Qwen3-Embedding-8B fits on 16GB and is fast on batch).

**Engineering cost honest take:** `claude-code-router` install is a 10-minute exercise; tuning the routing thresholds takes a weekend; the env-var/Max-billing audit is the one task you can't skip. Total: maybe a day of work, yielding ~$10-15/mo savings on the $20 external bucket + insurance against weekly-limit lockouts. Not enormous, but not zero, and the audit alone is worth doing.

## Adjacent topics worth deeper research

- **Self-hosted embedding swap for VoyageAI** — concrete recipe with Supabase pgvector, performance comparison on jarvis's actual memory corpus, latency budget for nightly batch vs query-time.
- **Issue #39903 audit** — what env vars across the 3 devices could trigger Max-billing leak; write a `claude doctor`-style script that checks before each session.
- **MoA for `/grill`** — prototype: 3× DeepSeek V3.1 proposers → 1× Claude aggregator, measure objection diversity vs single-Claude baseline on 5 historical grill transcripts.
- **Speculative decoding for local jarvis loops** — only relevant if jarvis ever self-hosts a model, which isn't on roadmap. Park.
- **Provider reliability dataset** — Together vs DeepInfra vs Novita for DeepSeek over a month; tail-latency and retry rates matter more than headline price for agentic loops.
- **Per-skill model selection** — which jarvis skills tolerate DeepSeek quality (`/research`, `/status-record`, `/end --quick`, `/status`) vs which need Claude (`/implement`, `/grill`, `/diagnose`, `/improve-codebase-architecture`).

## Sources

- [DeepSeek API pricing — official docs](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek V4 Preview release notes](https://api-docs.deepseek.com/news/news260424)
- [DeepSeek V3.1 release notes](https://api-docs.deepseek.com/news/news250821)
- [DeepSeek models on OpenRouter](https://openrouter.ai/deepseek)
- [DeepSeek V4 Flash free on OpenRouter](https://openrouter.ai/deepseek/deepseek-v4-flash:free)
- [Artificial Analysis — DeepSeek V3.1 provider comparison](https://artificialanalysis.ai/models/deepseek-v3-1/providers)
- [musistudio/claude-code-router (26k stars, MIT)](https://github.com/musistudio/claude-code-router)
- [Claude Code Router setup guide (PolySkill)](https://polyskill.ai/blog/claude-code-router)
- [Claude Code Router 2026 guide (Get AI Perks)](https://www.getaiperks.com/en/ai/claude-code-router-guide)
- [LiteLLM Claude Code quickstart](https://docs.litellm.ai/docs/tutorials/claude_responses_api)
- [techsy.io — cut Claude Code bill 90%](https://techsy.io/en/blog/claude-code-use-different-models)
- [Anthropic — using Claude Code with Pro/Max plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
- [Issue #39903 — Max-plan subagent billing leak](https://github.com/anthropics/claude-code/issues/39903)
- [DeepClaude (17× cheaper)](https://www.gncrypto.news/news/deepclaude-claude-code-deepseek-17x-lower-cost/)
- [johnrodrigues — Claude Code + DeepSeek $1200→$60/year](https://johnrodrigues.substack.com/p/claude-code-deepseek-from-1200year)
- [Aider — DeepSeek docs](https://aider.chat/docs/llms/deepseek.html)
- [Aider — advanced model settings](https://aider.chat/docs/config/adv-model-settings.html)
- [Aider issue #3095 — three-role model split](https://github.com/Aider-AI/aider/issues/3095)
- [Cline — DeepSeek provider docs](https://docs.cline.bot/provider-config/deepseek)
- [Cline Plan/Act with R1 + Sonnet (aisharenet)](https://aisharenet.com/en/cline-zuijiazuhe/)
- [Continue.dev — autocomplete model role](https://docs.continue.dev/customize/model-roles/autocomplete)
- [Continue.dev — recommended autocomplete models](https://docs.continue.dev/ide-extensions/autocomplete/model-setup)
- [Qwen3-Coder official blog](https://qwenlm.github.io/blog/qwen3-coder/)
- [Qwen3-Coder-480B on Ollama (250GB)](https://ollama.com/library/qwen3-coder:480b)
- [InsiderLLM — best local coding models by VRAM tier (2026)](https://insiderllm.com/guides/best-local-coding-models-2026/)
- [Mixture-of-Agents paper (arXiv)](https://arxiv.org/html/2406.04692v1)
- [Together MoA repo](https://github.com/togethercomputer/moa)
- [VoyageAI pricing](https://docs.voyageai.com/docs/pricing)
- [Elephas embedding comparison (NV-Embed-v2 20× cheaper)](https://elephas.app/blog/best-embedding-models)
- [Speculative decoding primer (Redis)](https://redis.io/blog/speculative-decoding-llm/)
- [SWE-bench Verified leaderboard](https://www.vals.ai/benchmarks/swebench)
- [DeepSeek V4 Pro vs Claude Sonnet 4.6 comparison](https://benchlm.ai/compare/claude-sonnet-4-6-vs-deepseek-v4-pro-high)
