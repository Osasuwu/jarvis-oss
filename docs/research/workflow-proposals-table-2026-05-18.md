---
title: Workflow proposals — unified table (memory + gh + 7 researches + 6 user answers)
date: 2026-05-18
status: working-doc
purpose: single searchable surface for every concrete proposal from the 7-research workflow-change exercise, cross-checked against current memory + GitHub state + 6 owner answers. Jarvis takes batches from here → milestones/issues.
inputs:
  - docs/research/single-agent-workflows-2026-05-18.md
  - docs/research/single-vs-multi-agent-architecture-2026-05-18.md
  - docs/research/gh-workflows-solo-vs-team-2026-05-18.md
  - docs/research/cc-vs-codex-vs-alternatives-2026-05-18.md
  - docs/research/cheap-models-cost-reduction-2026-05-18.md
  - docs/research/gap-discovery-patterns-2026-05-18.md
  - docs/research/session-behavioral-analysis-2026-05-18.md
  - docs/research/workflow-change-plan-2026-05-18.md (my prior narrow synthesis — superseded by this table)
  - docs/research/memory-architecture-deep-dive-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/prompt-caching-context-mgmt-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/skill-discovery-routing-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/tdd-for-agents-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/eval-rubric-design-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/docs-as-contract-2026-05-18.md (added 2026-05-18 wave 2)
  - docs/research/cross-device-sync-2026-05-18.md (added 2026-05-18 wave 3)
  - docs/research/failure-mode-taxonomy-2026-05-18.md (added 2026-05-18 wave 3)
  - docs/research/jarvis-v2-top-layer-2026-05-18.md (added 2026-05-18 wave 3)
  - docs/research/decision-quality-measurement-2026-05-18.md (added 2026-05-18 wave 3)
  - docs/research/hitl-approval-ux-2026-05-18.md (added 2026-05-18 wave 3)
  - docs/research/solo-dev-sustainability-2026-05-18.md (added 2026-05-18 wave 3)
  - memory_recall passes on ~18 topics
  - gh issue/milestone scan
  - 6 user answers in session 2026-05-18 (recorded inline below)
---

## Legend

**Status code:**
- ✅ **DONE** — shipped; closed gh issue or in-production behavior
- 🚧 **PLANNED** — open gh issue or milestone exists; on backlog
- 🆕 **NEW** — not previously captured; needs decision
- 👤 **USER-DECIDED** — owner answer in this session resolves it
- ❌ **REJECTED** — explicit reject by owner or research consensus
- ⚠️ **CONFLICT** — research recommendation conflicts with memory/state; surfaced for decision
- 🔄 **STALE** — memory may be outdated; re-ask owner

**Priority** (Jarvis judgment, owner overrides):
- **P0** — high-impact, low effort, unblocks others
- **P1** — high-impact, standalone
- **P2** — medium impact
- **P3** — nice-to-have / low effort
- **P4** — defer or wait-and-see

## Owner answers in this session (resolved decisions)

| # | Question | Answer | Affects rows |
|---|---|---|---|
| Q1 | Hours/week on system work | **30h/week available**, 1-2 days/week on delivery, rest system | scope of waves |
| Q2 | Codex CLI side-channel | **NO Codex** — paid, won't pay for second tier same as Claude. Local LLM on 16GB RTX 5080 workshop + DeepSeek as cheap API fallback. Sandcastle migrate to subscription first, DeepSeek second | rows 10, 11, 37, 40-50 |
| Q3 | Skill-creation gate strictness | **MEDIUM only** — skill proliferation is intentional temp experimentation; gate just reminds of target shape | row 14 |
| Q4 | Late-night sessions for consequential work | **No hook, no signal** — owner self-regulates, sleep schedule floats, day/night equally variable quality | row 58 |
| Q5 | SOUL.md identity layer | **Strip personality, keep rules.** Claude Code ≠ Jarvis. Jarvis is future top-layer orchestrator (smart home, news, sending tasks to CC as the developer) — owner hasn't built it yet. Current layer = "CC is the developer", no persona needed | rows 52, 53, 54 |
| Q6 | /autonomous-loop bounds | **Loop is being DELETED**, functionality moves to AFK system (M#41). Don't design bounds | rows 55, 56 |

## Main proposals table

> All 8 source docs in `docs/research/*-2026-05-18.md`. Direct source links are URLs / gh refs. Columns: ID, Proposal, Source doc(s), Direct source, Status, Priority, gh ref / memory ref, Conflicts/notes.

### A. Eval & observability (leading-indicator layer)

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 1 | **L1 golden-set eval harness** (Hamel 20-50 tasks, pass@k/pass^k, 3 grader types) extending M#43 sycophancy scaffold | gap-discovery; behavior-analysis; synthesis-plan | [Hamel](https://hamel.dev/blog/posts/evals/), [Anthropic engineering](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 🆕/🚧 (partial — M#43 has 1 scenario set; needs 4 more categories) | **P0** | M#43 ([#690](https://github.com/Osasuwu/jarvis/issues/690) closed) baseline shipped; expansion is new | The behavior analysis's #2 ranked gap. Synthesis-plan calls it the gate for #14, #23 — without harness, gate FP rate is unknowable |
| 2 | **Transcript observability layer** — cheap Python over `~/.claude/projects/*/sessions/*.jsonl` first; Langfuse self-host later | gap-discovery | [Langfuse self-host](https://langfuse.com/self-hosting), [ccusage](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) | 🆕 | **P1** | `comm-patterns-extract.py` is adjacent ([#581](https://github.com/Osasuwu/jarvis/issues/581) closed); no token/skill-invocation dashboard exists | "Where did Tuesday go" — answers the diagnostic question that prompted this whole exercise |
| 3 | **Out-of-band ground truth test set on a schedule** (anomaly detection over trajectory drift) | gap-discovery | [arxiv 2511.04032](https://arxiv.org/abs/2511.04032) | 🆕 | **P3** | Eclipsed by #1 if #1 ships | Catches silent quality drift that decision audits miss |
| 4 | **Calibration drill** — 10 predictions/week on own work, Brier score over weeks | gap-discovery | [Clearer Thinking](https://www.clearerthinking.org/post/2019/10/16/practice-making-accurate-predictions-with-our-new-tool) | 🆕 | **P3** | None | Makes `record_decision.confidence` a measured number, not a guess |
| 5 | **Sycophancy regression probe** as part of #1 | gap-discovery | [SycEval](https://arxiv.org/abs/2502.08177), [ELEPHANT](https://arxiv.org/pdf/2505.13995) | 🚧 (baseline shipped) | **P0** (folded into #1) | M#43 [#690](https://github.com/Osasuwu/jarvis/issues/690) closed; [#694](https://github.com/Osasuwu/jarvis/issues/694) post-fix re-eval OPEN | Post-fix re-run is the next concrete slice |
| 6 | **Weekly claim-vs-code drift sweep** on CLAUDE.md / CONTEXT.md / SOUL.md (DeepSeek V4-Flash classifier extracts factual claims → grep/smoke) | behavior-analysis; gap-discovery | [Anthropic Auto Dream](https://letsdatascience.com/news/anthropic-introduces-dreaming-for-claude-agent-memory-consol-32a279c9) | 🆕 | **P2** | Audit Correction #2 (CLAUDE.md arch-sweep auto-trigger described as shipped but [#605](https://github.com/Osasuwu/jarvis/issues/605) only demoted the claim, code path doesn't exist) is the motivating instance | Pairs with #1 — drift sweep is one of the eval scenarios |
| 7 | **Scheduled skill/CLAUDE.md bloat audit** (rebelytics meta-skill pattern) | gap-discovery | [rebelytics/one-skill-to-rule-them-all](https://github.com/rebelytics/one-skill-to-rule-them-all), [alexop.dev](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/) | 🆕 | **P3** | Owner Q3 answer: skill churn is intentional temp experimentation → defer skill-bloat sweep until experimentation phase done | Reframe: not a gate, advisory monthly report |
| 8 | **Outcomes-as-rubric** (Anthropic pattern) for `/verify` — rubric grader, +8-10pp Anthropic-reported gain on document gen | single-agent-workflows | [Anthropic Managed Agents](https://claude.com/blog/new-in-claude-managed-agents) | 🆕 | **P2** | `/verify` exists, `/learn outcomes` mode planned in [#526](https://github.com/Osasuwu/jarvis/issues/526) | Fold into `/learn outcomes` rather than new skill |
| 9 | **Measure % of agent PRs merged unchanged vs needing rework** | gh-workflows | [Hashimoto baseline 10-20%](https://newsletter.pragmaticengineer.com/p/mitchell-hashimoto) | 🆕 | **P3** | None | Real metric for whether `/grill` chain pays for itself |

### B. Cross-model & adversarial review

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 10 | **Codex CLI side-channel + `/cross-critique` skill** (`codex exec --json --ask-for-approval never`) | cc-vs-codex; gap-discovery; behavior-analysis; synthesis-plan (top item) | [openai/codex](https://github.com/openai/codex) | ❌ **REJECTED by Q2** | n/a | n/a | Owner: "Я не хочу платить за codex дополнительно. codex такой-же дорогой как claude" |
| 11 | **Adversarial cross-model review via local LLM (Gemma 4 26B-MoE on RTX 5080) or DeepSeek fallback** — same outcome as #10 without paying Codex | gap-discovery; cc-vs-codex (alt path) | [robertoecf/adversarial-review](https://github.com/robertoecf/adversarial-review), [Telefónica](https://www.telefonica.com/en/communication-room/blog/multiple-ais-sequence-produce-robust-outputs-identify-blind-spots/) | 🆕 (replaces #10) | **P0** | Workshop Ollama benchmark [#674](https://github.com/Osasuwu/jarvis/issues/674) CLOSED (Gemma 4 26B-MoE evaluated as primary candidate); DeepSeek Tier-2 fallback already in production via [#543](https://github.com/Osasuwu/jarvis/issues/543) sandcastle escalation | **Major correction**: the "second model" infra largely exists. New work = skill that calls it for plan/PRD/decision critique, not infrastructure |
| 12 | **Multi-persona review panel + supreme judge** (agent-review-panel pattern) | gap-discovery | [wan-huiyan/agent-review-panel](https://github.com/wan-huiyan/agent-review-panel) | 🆕 | **P4** | `/grill` CRITIC subagent [#692](https://github.com/Osasuwu/jarvis/issues/692) CLOSED — partial implementation | Same-family bias still risk; cost vs marginal gain unclear for solo |
| 13 | **MoA for `/grill`** (3× DeepSeek/local proposers + 1× Claude aggregator) | cheap-models | [Together MoA](https://github.com/togethercomputer/moa) | 🆕 | **P3** | None | Only worth it if `/grill` cost becomes a bottleneck. After #11 ships, evaluate |

### C. Skills & hooks

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 14 | **PreToolUse hook on `Write\|Edit` for `**/skills/**/SKILL.md`** — medium gate (warn + show target shape, don't block) per Q3 | behavior-analysis; single-agent-workflows | [Superpowers Iron Laws](https://github.com/obra/superpowers) | 👤 **USER-DECIDED: medium** | **P2** | `skill_proliferation_antipattern` memory exists; Tier-2 hook history has 6× FP (audit Infra #48) | Q3: "Gate лишний, только мешать будет. Хотя можно сделать medium, чтобы мы хотя бы не забывали к чему в итоге хотим прийти" — soft signal only |
| 15 | **Stop-hook scan: decision-shaped exchange without `record_decision`** (DeepSeek V4-Flash classifier, shadow mode 2 weeks then surface) | behavior-analysis; synthesis-plan | gap-discovery: [Farnam Street decision journal](https://fs.blog/decision-journal/) | 🚧 (related work shipped, exact proposal new) | **P1** | [#333](https://github.com/Osasuwu/jarvis/issues/333) `/end and /reflect audit sessions for decision points without preceding recall` CLOSED Apr 24 — closely related but covers *recall* miss, not *record_decision* omission | Verify #333 implementation covers omission detection; if yes — done. If no — narrow new issue |
| 16 | **Iron Laws + red-flag rationalizations copy** in `/grill` and `/implement` Tier-1 rules | single-agent-workflows; behavior-analysis | [Superpowers SKILL files](https://github.com/obra/superpowers) | 🆕 | **P2** | `/grill` and `/implement` exist, no Iron Laws-style enumeration | 50 lines per skill, closes "knows-but-doesn't" gap |
| 17 | **Linear Walkthrough → `/walkthrough` skill** (force shell-extracted snippets via `sed`/`grep`, no memory-based code quoting) | single-agent-workflows | [Simon Willison guide](https://simonwillison.net/guides/agentic-engineering-patterns/linear-walkthroughs/) | 🆕 | **P3** | `/zoom-out` exists for higher-level map; this is complementary | Defer unless drift sweep #6 reveals frequent "agent quoted code that doesn't exist" misses |
| 18 | **PreCompact hook** to snapshot working_state before compaction | single-agent-workflows | [Anthropic hooks](https://code.claude.com/docs/en/hooks) | ✅ DONE | n/a | [#278](https://github.com/Osasuwu/jarvis/issues/278) CLOSED (PreCompact hook + transcript parser + Supabase snapshot) | Already shipped |
| 19 | **PostToolUseFailure hook** to log into outcomes | single-agent-workflows | [Anthropic hooks](https://code.claude.com/docs/en/hooks) | 🆕 | **P3** | None found | Verify hook exists in current CC version (April changelog shipped many) before planning |
| 20 | **Install Superpowers as one-device experiment** (Lenovo, read SKILL files for patterns) | single-agent-workflows | [obra/superpowers](https://github.com/obra/superpowers) | ❌ **REJECTED 2026-05-18** | n/a | `superpowers_plugin_evaluation_pending` memory DELETED 2026-05-18 | Owner: drop install, steal Iron Laws pattern via row 16 only |
| 21 | **Verify `/grill` CRITIC subagent has clean context** (no parent framing inherited) | single-vs-multi-agent | [Cognition 2026](https://cognition.ai/blog/multi-agents-working) | 🆕 (verify-only) | **P0** | [#692](https://github.com/Osasuwu/jarvis/issues/692) CLOSED — CRITIC ships but clean-context invariant not explicitly tested | One-line audit. Cognition: shared context destroys catch rate |
| 22 | **Skill-vs-MCP decision rubric** (adjacent topic) | single-vs-multi-agent | [Duet.so guide](https://duet.so/guides/agent-skills-101-tools-vs-mcp-vs-skills) | 🆕 | **P3** | `owner_prefers_mcp_over_skills` memory exists — partial rubric | Codify into CLAUDE.md when adding next skill or MCP |

### D. Subagent verification

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 23 | **Subagent post-flight verifier** (PostToolUse on Task — git diff vs claimed file list / test count) shadow mode first | behavior-analysis; single-vs-multi; synthesis-plan | none — own design | 🚧 | **P0** | [#651](https://github.com/Osasuwu/jarvis/issues/651) OPEN ("Subagent fabrication (claim != diff) is a recurring class — orchestrator-side detection gap") — exact match | Plus [#652](https://github.com/Osasuwu/jarvis/issues/652) AC-dodge, [#653](https://github.com/Osasuwu/jarvis/issues/653) post-compaction premise hallucination. Bundle into one milestone |
| 24 | **Smart Friend / capability router**: route `/delegate` workers to Sonnet/Haiku, reserve Opus for `/grill` CRITIC and architecture sweeps | single-vs-multi | [Cognition Multi-Agents Working](https://cognition.ai/blog/multi-agents-working) | 🆕 | **P2** | None — subagents already support `model:` per definition, but no policy on which model where | Concrete cost win; pairs with M#41 AFK loop |

### E. Multi-agent posture (stable)

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 25 | **STAY single-agent + orchestrator+isolated-subagents pattern** | single-vs-multi | [Anthropic multi-agent research](https://www.anthropic.com/engineering/built-multi-agent-research-system) | ✅ DONE (confirms current) | n/a | `federated_architecture_direction` memory (HYBRID: federation across jurisdictions + orchestrator-worker inside each) | Raises confidence 75→85% per the research |
| 26 | **DON'T pivot to peer multi-agent** (CrewAI / Ruflo / AutoGen) | single-vs-multi | [Cognition reversal](https://cognition.ai/blog/multi-agents-working) | ❌ REJECTED (research consensus) | n/a | `managed_agents_wait_and_see` aligned | Standing don't-do |
| 27 | **DON'T enable `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` baseline** — keep as contingent option for one full-stack workload | single-vs-multi | Anthropic docs | ❌ REJECTED (with carve-out) | n/a | None | Sequential coding is explicit anti-pattern per Anthropic |
| 28 | **Selective LangGraph only for graph-state workflows** (autonomous-day loop if it grows branching+HITL; cross-device routine orchestration) | single-vs-multi | [LangGraph](https://github.com/langchain-ai/langgraph) | 🚧 (already used selectively) | **P4** | `pm_dispatch_v1_superseded_by_persistent_agents` — Sprint 1 LangGraph foundation closed. M#41 may surface new candidate | Owner Q6: /autonomous-loop being deleted; AFK system is the new candidate |
| 29 | **claude-squad** replace `/delegate` dispatch with richer terminal UI | single-vs-multi | [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | ❌ REJECTED (research) | n/a | None | "Not obviously a win over current GH-issue-driven workflow" |

### F. GitHub workflow

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 30 | **IssueOps layer** (label-as-state-machine + comment-as-command + issue forms) — first cut: one `ready:agent` label + one workflow | gh-workflows; behavior-analysis | [github.blog IssueOps](https://github.blog/engineering/issueops-automate-ci-cd-and-more-with-github-issues-and-actions/) | 🆕 | **P1** | M#41 AFK PR-rework loop ([#633-#640](https://github.com/Osasuwu/jarvis/milestones)) overlaps — review_negative events, loop-stop guard, rework skill all PLANNED. IssueOps would slot in beside it | Likely fold into M#41 rather than separate milestone |
| 31 | **gh-dash** with persona configs (jarvis, redrobot) — cross-repo PR/issue view | gh-workflows | [gh-dash](https://github.com/dlvhdr/gh-dash) | 🆕 | **P3** | None | 10-min install, daily payoff. Doesn't need owner ceremony |
| 32 | **gh-poi** for auto-prune merged branches (including squash-merges) | gh-workflows | [gh-poi](https://github.com/seachicken/gh-poi) | 🆕 | **P3** | None | Add to `/end` skill optional final step. ~5 min |
| 33 | **Rulesets with bypass for self-approval** (CODEOWNERS bypass list — agents go through gate, owner bypasses) | gh-workflows | [community discussion #14866](https://github.com/orgs/community/discussions/14866) | 🆕 | **P4** | None | Defer until agents push without HITL. Currently all subagent PRs go through HITL review |
| 34 | **Stacked PRs** (`gh stack` April 2026 preview / Sapling / Graphite) | gh-workflows | [InfoQ gh-stack](https://www.infoq.com/news/2026/04/github-stacked-prs/) | 🆕 | **P4** | None | Defer until milestone routinely has 4+ ordered slices |
| 35 | **Daily-planner static-site pattern** → status-snapshot dashboard | gh-workflows | [Simon Willison daily-planner](https://til.simonwillison.net/github-actions/daily-planner) | 🆕 | **P4** | `status_snapshot_2026-05-10` memory exists; pattern adaptable | Nice-to-have; eclipsed by #2 transcript observability |
| 36 | **Cross-device gh CLI state sync via install.ps1** (gh-dash configs, aliases, extensions) | gh-workflows | install.ps1 in repo | 🆕 | **P3** | install.ps1 covers dotfiles; gh extensions need adding | Bundle with #31, #32 |

### G. Cost / external APIs

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 37 | **DON'T migrate Claude Code → Codex** | cc-vs-codex | migration guides ([Crosley](https://blakecrosley.com/blog/claude-code-to-codex-migration)) | ❌ REJECTED (research + Q2) | n/a | None | Both research and owner Q2 reject |
| 38 | **DON'T route Claude Code traffic to DeepSeek for cost** | cheap-models | [Issue #39903](https://github.com/anthropics/claude-code/issues/39903) | ❌ REJECTED (research consensus) | n/a | `claude_max_upgrade`, `scheduled_tasks_subscription_not_api` memories aligned | Max covers CC marginally free. Routing buys you $0 + risk |
| 39 | **Self-hosted embeddings** (Qwen3-Embedding-8B on 16GB RTX 5080) to kill VoyageAI bill | cheap-models | [Qwen3 blog](https://qwenlm.github.io/blog/qwen3-coder/) | 🆕 | **P2** | `feedback_voyage` memory: VoyageAI Tier 1 paid $5 added 2026-03-30 — small but recurring | Fits RTX 5080. Nightly batch on Workshop; Supabase pgvector stays |
| 40 | **Scheduled tasks LLM calls** → route through OpenRouter with DeepSeek (sandcastle path) | cheap-models | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) | ✅ DONE (Tier 2) | n/a | [#543](https://github.com/Osasuwu/jarvis/issues/543) CLOSED sandcastle multi-tier escalation Ollama → Ollama-small → **DeepSeek API** → owner | **Major correction**: DeepSeek as Tier 2 fallback already in production. Q2 owner answer "sandcastle → subscription first, DeepSeek second" matches current design |
| 41 | **Audit `ANTHROPIC_API_KEY` across 3 devices** (Issue #39903 $152 leak trap) | cheap-models | [Issue #39903](https://github.com/anthropics/claude-code/issues/39903) | 🆕 | **P1** | None — but `anthropic_api_credit_empty_2026_04_21` memory shows owner had API key with zero balance, so risk vector exists | One-time audit script `claude doctor`-style |
| 42 | **OpenRouter free V4-Flash as Max-burst circuit breaker** | cheap-models | [OpenRouter free](https://openrouter.ai/deepseek/deepseek-v4-flash:free) | 🆕 | **P3** | `max_20x_upgrade_available` memory: company will pay $200/mo if needed | Compete with the upgrade option. Owner choice |
| 43 | **claude-code-router with scenario routing** | cheap-models | [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router) | ❌ REJECTED for Max user | n/a | None | Only useful if API-billed |
| 44 | **LiteLLM proxy** as universal translator | cheap-models | [LiteLLM](https://docs.litellm.ai/docs/tutorials/claude_responses_api) | ❌ REJECTED for Max user | n/a | None | Same reasoning as #43 |

### H. Identity / SOUL (owner Q5 directive)

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 52 | **Strip SOUL.md personality, keep behavior rules** | behavior-analysis; gap-discovery (sycophancy correlation) | M#43 measurement | 👤 **USER-DECIDED: STRIP** | **P1** | `personalization-sycophancy paradox` in CONTEXT.md glossary; M#43 #694 OPEN | Q5: "Claude Code ≠ Jarvis. SOUL как персона нужна агенту, который в будущем будет управлять всей системой на верхнем слое... убрать personality и оставить только правила" |
| 53 | **Reserve SOUL.md identity layer for future Jarvis top-layer orchestrator** (smart home, news, sends tasks to CC) | owner-defined | own vision | 👤 **USER-DECIDED: future scope** | n/a | `jarvis_v2_vision`, `jarvis_wrapping_direction` memories partial match | Document in CONTEXT.md or `docs/design/jarvis-v2-redesign.md` |
| 54 | **Suspend SOUL on consequential decisions** (partial via grill CRITIC clean context) | gap-discovery | own design | 🚧 partial | **P2** | [#692](https://github.com/Osasuwu/jarvis/issues/692) CLOSED — CRITIC exists; ensure clean-context (row 21) | Subsumed by #52 if SOUL personality stripped |

### I. AFK / autonomous-loop (owner Q6 directive)

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 55 | **Delete `/autonomous-loop`, move functionality to AFK system** | owner-defined | own roadmap | 👤 **USER-DECIDED: delete** | **P1** | `autonomous_loop_v1_architecture` memory still live; `autonomous_loop_last_run` dedup marker active | Q6: "запланировано удаление и перенос функционала в AFK систему" |
| 56 | **AFK system = M#41 AFK PR-rework loop** | gh state | [M#41 milestone](https://github.com/Osasuwu/jarvis/milestone/41) | 🚧 PLANNED | **P1** | [#633](https://github.com/Osasuwu/jarvis/issues/633) review_negative events, [#634](https://github.com/Osasuwu/jarvis/issues/634) loop-stop guard, [#635](https://github.com/Osasuwu/jarvis/issues/635) quota probe, [#636](https://github.com/Osasuwu/jarvis/issues/636) /rework skill, [#637](https://github.com/Osasuwu/jarvis/issues/637) sandcastle PR target, [#638](https://github.com/Osasuwu/jarvis/issues/638) rework history, [#639](https://github.com/Osasuwu/jarvis/issues/639) Workshop watcher daemon, [#640](https://github.com/Osasuwu/jarvis/issues/640) deprecate review-response.yml | Already deeply planned. Add to M#41: explicit deprecation of /autonomous-loop + cron removal |
| 57 | **Sandcastle multi-tier escalation** (Ollama → Ollama-small → DeepSeek → owner) | research-related | sandcastle integration | ✅ DONE | n/a | [#543](https://github.com/Osasuwu/jarvis/issues/543) CLOSED | Q2 owner's "sandcastle subscription first, DeepSeek second" — already in production |
| 58 | **Late-night session hook / signal** | gap-discovery; behavior-analysis | own design | ❌ REJECTED by Q4 | n/a | n/a | Q4: "late-night sessions - не проблема. Мой график сна плавающий... Я сам слежу за своим состоянием" |

### J. Hardware / local LLM (Q2 directive + correction)

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 68 | **RTX 5080 16GB workshop PC as local Ollama primary** (Gemma 4 26B-MoE) | hardware reality + Q2 | own evaluation | ✅ DONE | n/a | [#674](https://github.com/Osasuwu/jarvis/issues/674) CLOSED 2026-05-16 evaluated Gemma 4 26B-MoE; [#538](https://github.com/Osasuwu/jarvis/issues/538) CLOSED workshop hardware Ollama benchmark + production model pick; [#545](https://github.com/Osasuwu/jarvis/issues/545) CLOSED Workshop PC promotion + jarvis safe-hours schedule | **Major correction to memory**: `main_pc_ollama_benchmark_2026_05_10` memory references RTX 3050 6GB on Main PC — but the actual primary local Ollama venue is Workshop RTX 5080 with Gemma 4 26B-MoE already picked |
| 69 | **DON'T use local LLM as Claude replacement** (use for embeddings + sandcastle Tier 1) | cheap-models | [InsiderLLM 2026](https://insiderllm.com/guides/best-local-coding-models-2026/) | ❌ REJECTED (research) + ✅ DONE (sandcastle uses it for Tier 1) | n/a | `ollama_bench_must_measure_tool_use_fidelity` memory: qwen3-coder:30b fails Hermes-XML in real CC session; `pending_grill_local_agent_harness` open topic | Wire-format mismatch is the blocker. Local LLM = embeddings + sandcastle agent (already), not CC-substrate |

### K. Adjacent / wait-and-see / out-of-scope

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 59 | **Anthropic Auto Dream** — replace consolidate-memory | gap-discovery | [Auto Dream coverage](https://letsdatascience.com/news/anthropic-introduces-dreaming-for-claude-agent-memory-consol-32a279c9) | ❌ DEFERRED (research preview) | **P4** | None | Wait for GA. M#39 implicit-memory-derivation is the in-house version |
| 60 | **ACP (Agent Client Protocol)** — track for Q3 2026 | cc-vs-codex | [Zed ACP](https://zed.dev/acp) | ❌ DEFERRED | **P4** | None | If reaches MCP-class adoption, skill portability changes the lock-in calculus |
| 61 | **Coaching loop with second human** (co-creator advisory) | gap-discovery | [Solo Founder Playbook](https://entrepreneurloop.substack.com/p/building-a-startup-alone-solo-founder-playbook) | ❌ OUT OF SCOPE (single-principal model) | n/a | `pillar7_personal_vs_company_separation` | Not on roadmap |
| 62 | **Bret Victor live-feedback HUD-style view** during session | gap-discovery | [Inventing on Principle](https://jamesclear.com/great-speeches/inventing-on-principle-by-bret-victor) | 🆕 | **P4** | None | Different from #2 observability — live during session. Niche |
| 63 | **mattpocock/sandcastle** TS lib | single-agent-workflows | [sandcastle](https://github.com/mattpocock/sandcastle) | ❌ REJECTED (own sandcastle exists) | n/a | sandcastle subsystem own | Different problem domain |
| 64 | **Dreaming as self-improve primitive** | single-agent-workflows | Anthropic | ❌ DEFERRED (same as #59) | **P4** | None | |
| 65 | **Speculative decoding** | cheap-models | [Redis primer](https://redis.io/blog/speculative-decoding-llm/) | ❌ OUT OF SCOPE | n/a | None | Only matters if jarvis self-hosts a model — not on roadmap |
| 66 | **Spec-Kit extensions/presets concept** (extract idea, skip framework) | single-agent-workflows | [Spec-Kit](https://github.com/github/spec-kit) | ❌ DEFERRED | **P4** | None | Templates-without-fork pattern; nice but skill files already do this |
| 67 | **DON'T add "epic" grouping primitive ever** + cleanup existing drift | gh-workflows; behavior-analysis | decision `2a7ae10e-afc3-4523-b0bc-c4b90ddbe1a5` | ✅ standing rule + 🚧 **REOPENED 2026-05-18** for sweep | **P2** | [#668](https://github.com/Osasuwu/jarvis/issues/668) reopened 2026-05-18 with sweep-scope comment. Decision UUID `311f3d78-cc32-46c1-b2c6-2ae54172c39e` | Sweep scoped to .github/ workflows + issue templates + docs/process — slice still needs concrete child issue |
| 70 | **DON'T adopt Spec-Kit / BMAD / GSD** | single-agent-workflows; synthesis-plan | research consensus | ❌ REJECTED | n/a | None | Standing don't-do |
| 71 | **DON'T adopt markdown ADRs in `docs/adr/`** | gh-workflows; synthesis-plan | research consensus | ❌ REJECTED | n/a | `record_decision` MCP tool is the queryable ADR | Standing don't-do |
| 72 | **DON'T adopt GitHub Projects v2 sprint planning** | gh-workflows; synthesis-plan | research consensus | ❌ REJECTED | n/a | `milestone_hierarchy_v3` covers this | Cross-repo roll-up is the only legit use — defer |
| 73 | **DON'T strict Conventional Commits with scopes/footers** | gh-workflows | research consensus | ❌ REJECTED | n/a | Current `feat(grill):` style is the sweet spot | |
| 74 | **DON'T add more skills before #1 ships** | behavior-analysis; synthesis-plan | own audit pattern | ❌ REJECTED by Q3 (memory time-boxed) | n/a | `skill_proliferation_antipattern` PAUSED 2026-05-18 → 2026-06-18. Decision UUID `6534501c-eaa8-4b4e-830c-991b6f21430d` | Skill experimentation phase continues until 2026-06-18; eval harness #1 still P0 but doesn't block skill creation |

### L. Already-shipped items pulled in for completeness

| # | Item | Status | gh ref |
|---|---|---|---|
| 75 | Three-way doc split (CLAUDE.md / SOUL.md / CONTEXT.md) | ✅ DONE | grill_me_protocol_session_2026_04_30 decision; PR #489 |
| 76 | SessionStart hook (`session-context.py`) injects memory as data | ✅ DONE | `session_context_hook_architecture` memory; PR commit 4487da8 |
| 77 | `record_decision` UUID-not-name discipline + Tier-2 hook | ✅ DONE | `record_decision_always_pass_memories_used` memory + record-decision-gate.py hook |
| 78 | Sandcastle subsystem (slices 1-10) | ✅ DONE | M#34 epic [#534](https://github.com/Osasuwu/jarvis/issues/534) CLOSED |
| 79 | Meta-test rule for path-filtered CI guards | ✅ DONE | [#326 → PR #365](https://github.com/Osasuwu/jarvis/issues/326) |
| 80 | M#43 Anti-sycophancy retooling (4 of 6 slices) | ✅ DONE | [#689](https://github.com/Osasuwu/jarvis/issues/689), [#690](https://github.com/Osasuwu/jarvis/issues/690), [#691](https://github.com/Osasuwu/jarvis/issues/691), [#692](https://github.com/Osasuwu/jarvis/issues/692) CLOSED; [#693](https://github.com/Osasuwu/jarvis/issues/693), [#694](https://github.com/Osasuwu/jarvis/issues/694) OPEN |
| 81 | `/learn` skill replacing reflect+self-improve+verify | 🚧 PLANNED | M#37 [#526](https://github.com/Osasuwu/jarvis/issues/526), [#582](https://github.com/Osasuwu/jarvis/issues/582), [#560](https://github.com/Osasuwu/jarvis/issues/560) |
| 82 | Implicit memory derivation (Honcho-style) | 🚧 PLANNED | M#39 [#552](https://github.com/Osasuwu/jarvis/issues/552)-[#559](https://github.com/Osasuwu/jarvis/issues/559) |
| 83 | Test Audit 2026-Q2 | 🚧 PLANNED | M#40 [#615](https://github.com/Osasuwu/jarvis/issues/615)-[#625](https://github.com/Osasuwu/jarvis/issues/625) |
| 84 | Skill set redesign | 🚧 PLANNED | M#37 |
| 85 | /learn always-gate review surface | 🚧 PLANNED | M#42 [#681](https://github.com/Osasuwu/jarvis/issues/681)-[#685](https://github.com/Osasuwu/jarvis/issues/685) |

---

# Wave 2: 6 deep-dive researches added 2026-05-18

Six follow-up deep-dives commissioned 2026-05-18 covering gaps the 7-research wave missed: memory architecture, prompt caching/context, skill discovery/routing, TDD for agents, eval rubric design, docs-as-contract. Doc paths in frontmatter. Sections M-R below.

### M. Memory architecture (deep-dive 2026-05-18)

> Source: `docs/research/memory-architecture-deep-dive-2026-05-18.md`. RAG retrieval quality, graph memory, decay, embedding choice, retrieval evals.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 86 | **RRF fusion + cross-encoder rerank in `memory_recall`** (BGE-reranker-base-v2 local, ~92ms) [M1] | memory-architecture | [ParadeDB hybrid manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) | 🆕 | **P0** | ⚠️ Overlaps [#185](https://github.com/Osasuwu/jarvis/issues/185) Memory overhaul Pillar 4 (lifecycle + bi-temporal + eval) OPEN; [#641](https://github.com/Osasuwu/jarvis/issues/641) recall quality undermining always_load OPEN; [#507](https://github.com/Osasuwu/jarvis/issues/507) recall hook signal-derivation order OPEN. Memory `memory_server_v2_improvements` (e306cb81) lists hybrid RRF as already-decided design (Mar 2026) — rerank step missing; `memory_roadmap_stealable_ideas_2026_04_20` (ba844bfc) already enumerates 8 ideas-to-steal incl. rerank | Biggest single quality jump (MRR@3 0.43→0.60). #185 is the natural milestone home; M1 = concrete slice within. **Don't open new milestone — slot under #185** |
| 87 | **Build `/dream` consolidation skill** — 4-phase (orient → gather → consolidate → prune), 7d cadence [M2] | memory-architecture | [grandamenium/dream-skill](https://github.com/grandamenium/dream-skill), [Anthropic Dreams](https://platform.claude.com/docs/en/managed-agents/dreams) | 🆕 | **P1** | ⚠️ M#39 implicit-derivation overlaps strongly: [#557](https://github.com/Osasuwu/jarvis/issues/557) SessionEnd→Deriver (Ollama), [#553](https://github.com/Osasuwu/jarvis/issues/553) stop-hook accumulator. Memories: `consolidation_soft_archive` (55c3d1c8) lifecycle aligned; `phase_5_2_trio_shipped_end_to_end` (b5d4fad6) A-MEM neighbor evolution ALREADY SHIPPED 2026-04-19; `evolution_plan_2026-05-03` (e5316f09) Haiku dry-run; `memory_phase_5_1b_beta_design` (71559f48) | **Major reframe**: Phase 5.2 A-MEM trio already does 3 of 4 phases. Real gap = scheduled `/dream` skill triggering existing infrastructure, NOT new consolidation engine. Slot under M#39, not standalone |
| 88 | **Promote `memories_used[]` → `decision_memory_link` edge table** with `role` enum [M3] | memory-architecture | [Zep (arXiv 2501.13956)](https://arxiv.org/abs/2501.13956), Cognee | 🆕 | **P1** | ⚠️ Sibling issue [#660](https://github.com/Osasuwu/jarvis/issues/660) outcome_record.memory_id FK semantics ambiguity — same class of problem (array→edge). Memories: `record_decision_always_pass_memories_used` (7e79666d), `record_decision_when_what` (1778e466) document current array contract | Cheap migration with trigger. **Fold into #660 fix** — both touch FK semantics on outcome/decision side |
| 89 | **Add bi-temporal columns** `event_time` + `valid_from`/`valid_to` [M4] | memory-architecture | [Zep/Graphiti](https://github.com/getzep/graphiti) | 🆕 | **P2** | ⚠️ [#185](https://github.com/Osasuwu/jarvis/issues/185) Memory overhaul Pillar 4 description explicitly includes "bi-temporal" — slot under #185. Memory `memory_tiers_convention` (c497baad) TTL conventions exist; bi-temporal extends, doesn't conflict | Don't adopt full graph; steal timestamp pattern. **Folded under #185** |
| 90 | **Decay function in ranking** `score *= exp(-age_days / half_life)`, per-type half-lives [M5] | memory-architecture | LiCoMemory, SSGM (arXiv 2603.11768) | 🆕 | **P2** | ⚠️ [#185](https://github.com/Osasuwu/jarvis/issues/185) includes "ACT-R scoring" — same direction. Memories: `no_memory_hygiene_tool` (719fb533) temporal scoring implicit aligned; `consolidation_soft_archive` (55c3d1c8) expired_at lifecycle (not decay) — complementary not redundant; `memory_tiers_convention` (c497baad) TTL per type | Half-life per type. **Folded under #185 ACT-R bucket** |
| 91 | **Migrate VoyageAI → Qwen3-Embedding-8B (MRL→1024d) on RTX 5080** [M6] | memory-architecture | [Qwen3 blog](https://qwenlm.github.io/blog/qwen3-embedding/) | ⚠️ overlaps row 39 | **P2** | Row 39 same proposal. Memory `feedback_voyage` (5a343cea) Tier 1 paid \$5; `memory_alternatives` (bc23a778) evaluated alts | Saves ~$60/yr; #1 MTEB. **Gated on #92 retrieval fixture — measure quality before swap** |
| 92 | **Build 50-fixture jarvis-specific retrieval eval** (real recall queries → gold UUIDs) [M7] | memory-architecture | [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813) | 🆕 | **P0** | ⚠️ HEAVY OVERLAP with existing open work: [#505](https://github.com/Osasuwu/jarvis/issues/505) q09 stochastic regression + slice 4 keyword_query drift, [#506](https://github.com/Osasuwu/jarvis/issues/506) q09 calibration keyword_query entity-join delta, [#507](https://github.com/Osasuwu/jarvis/issues/507) recall hook signal derivation, [#673](https://github.com/Osasuwu/jarvis/issues/673) persistent rank miss on autonomous_chain_synthesis_template (4-iter reproduction), [#641](https://github.com/Osasuwu/jarvis/issues/641) recall quality undermining always_load. Memories: `explain_sql_before_tuning_recall` (1c045d81), `phase3_rewriter_type_narrowing_regression` (d3fc3b3a) recall@5 -5pp regression | **Don't build 50-fixture from scratch** — extend existing fixture set behind #505/#506/#507. q09/slice-4 already encoded as fixtures |
| 93 | **`dead_ref` linting skill** — parse paths/skills/issues, mark stale, demote ranking [M8] | memory-architecture | CLAUDE.md current manual rule scaled | 🆕 | **P2** | ⚠️ Direct sibling [#654](https://github.com/Osasuwu/jarvis/issues/654) memory-scan 5 recurring-failure lessons without enforcement-primitive trackers OPEN. Memories: `memory_management_strategy_v1` (4d75a1ea), `no_memory_hygiene_tool` (719fb533) | Quarterly cron. **Bundle with #654 enforcement-primitive direction** |
| 94 | **`supersedes` edge** — explicit edge when `record_decision` overrides prior [M9] | memory-architecture | [Cloudflare Agent Memory](https://blog.cloudflare.com/introducing-agent-memory/) | 🆕 | **P3** | Phase 5.2 A-MEM evolution (`phase_5_2_trio_shipped_end_to_end` b5d4fad6) already handles UPDATE/KEEP_DISTINCT — supersedes-as-edge would formalize what evolution does ad-hoc. `memory_review_queue_status_semantics` (ccf839e7) | Useful but rare; ship after #88 lands |
| 95 | **Query expansion at `memory_recall` entry** — skill-name + synonym dict before search [M10] | memory-architecture | LLM4IR-Survey query-reformulation | 🆕 | **P2** | ⚠️ DIRECTLY OVERLAPS [#507](https://github.com/Osasuwu/jarvis/issues/507) recall hook ordering, [#505](https://github.com/Osasuwu/jarvis/issues/505)/[#506](https://github.com/Osasuwu/jarvis/issues/506) q09 keyword_query drift. Memory `phase3_rewriter_type_narrowing_regression` (d3fc3b3a) — Haiku rewriter cut recall@5 by 5pp **(history lesson: expansion can hurt!)** | Cheap (Haiku call or static dict) — but **mind regression history**; A/B against existing rewriter, not green-field |

**Don't-do from M research:** no Letta (agent runtime conflicts CC); no Neo4j/Kuzu yet (workload doesn't justify); no global HyDE (hallucinates plausible-but-wrong terms); no iterative summarization without provenance (SSGM "semantic drift" failure mode).

### N. Prompt caching & context management (deep-dive 2026-05-18)

> Source: `docs/research/prompt-caching-context-mgmt-2026-05-18.md`. **Key finding:** the "100K smart zone" is folklore-correct in direction but overgenerous. NoLiMa ICML 2025: 11/12 LLMs drop below 50% baseline by 32K tokens. Sonnet 4.5 at 1M ≈ 18.5% MRCR.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 96 | **Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` on all 3 devices** [PC1] | prompt-caching | [Sikkema 2026-04](https://albertsikkema.com/ai/development/tools/2026/04/23/smaller-context-window-better-claude-code.html) | 🆕 | **P0** | `opus_1m_extra_billing` (10be89f8) — Opus 1M is extra billing → disabling 1M context aligns with cost discipline. install.ps1 can carry env var across 3 devices | Single-command env fix. Caps at 200K, forces earlier/smarter compaction. Test 1 week before locking |
| 97 | **Annotate `personal_workflow_aihero_adoption` memory** with NoLiMa/Gauthier evidence (32K real degradation start; 100K = soft ceiling) [PC2] | prompt-caching | [NoLiMa ICML 2025](https://arxiv.org/pdf/2502.05167) | 🆕 | **P0** | `personal_workflow_aihero_adoption` memory (folklore base). Sibling: `no_fake_context_token_estimates` (c56b97ec) — explicit feedback "don't invent context-window estimates" | Closes folklore-vs-evidence gap. One memory edit |
| 98 | **Audit 85 skills**: third-person voice, ≤200 char, archive 30d-zero-invocation [PC3] | prompt-caching | [Anthropic skill best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | 🆕 | **P1** | ⚠️ overlaps #107 (SD2) — merge as single audit pass. Memory `naming_check_existing_vocabulary` (9697a109) | ~60 tok/skill permanent baseline saving; 1-3K tok total potential |
| 99 | **Context-window usage in status line** [PC4] | prompt-caching | Anthropic costs doc | 🆕 | **P2** | ⚠️ Overlaps `idea_context_injection_token_diet` (466cc605) — token-diet axes 2-3 still open per [#324](https://github.com/Osasuwu/jarvis/issues/324) | Continuous visibility, zero ongoing cost |
| 100 | **Add ccusage 7-day cache-hit-ratio to weekly `/status-record` or `/self-improve`** [PC5] | prompt-caching | [ccusage block reports](https://ccusage.com/guide/blocks-reports) | 🆕 | **P2** | Memory `c56b97ec` no_fake_context — ccusage is the principled source instead of estimates | Detects prefix instability before cost spike |
| 101 | **Budget first 2-3 turns as cold-cache after `/resume` on v2.1.113+ Bun** [PC6] | prompt-caching | [cnighswonger/claude-code-cache-fix](https://github.com/cnighswonger/claude-code-cache-fix) | 🆕 | **P2** | [#408](https://github.com/Osasuwu/jarvis/issues/408) claude -p back-to-back spawn flake OPEN — related class of CC version-specific quirk | Known regression; awareness fix. Flag in CLAUDE.md until Anthropic patches |
| 102 | **Audit `/delegate` and `/implement` spawn-prompt templates for bloat** [PC7] | prompt-caching | Anthropic costs doc | 🆕 | **P3** | None — but [#642](https://github.com/Osasuwu/jarvis/issues/642) /delegate pre-dispatch gate OPEN is the slot for adding bloat check | Each spawn pays prompt as fresh write. Trim aggressively |
| 103 | **1h TTL via API `cache_control` for autonomous-loop** [PC8] | prompt-caching | Anthropic prompt caching doc | ❌ N/A | n/a | /autonomous-loop being deleted (Q6 row 55). `scheduled_tasks_subscription_not_api` (d828bd94) confirms no per-task cost path | Only relevant if direct API; Max subscription doesn't expose. Skip given Q6 |
| 104 | **Stop editing CLAUDE.md/SOUL.md/CONTEXT.md mid-session** — defer to `/end` reconciliation [PC9] | prompt-caching | Anthropic prompt caching cache-invalidation table | 🆕 | **P1** | `grill_me_protocol_session_2026_04_30` (7ad9fbb2) mentions doc edits; `always_loaded_context_budget_principle` (ff994ca2) decision aligned. Sibling `claudemd_consolidation_2026_04_08` (ea8c1b6e) | Pure discipline. Mid-session edits nuke prefix cache (10× per-turn cost spike) |
| 105 | **"Hot-reasoning vs retrieval" check before 100K+ session** — bias retrieval-heavy work to subagents [PC10] | prompt-caching | NoLiMa + RULER + Pocock | 🆕 | **P3** | `jarvis_wrapping_direction` (f56b60dd) | Soft heuristic for goal-routing |

**Don't-do from PC research:** don't trust 1M-context marketing for coding (retrieval window, reasoning degrades earlier); don't edit always-load docs mid-session; don't `/model` switch in hot session; don't `/delegate` a single issue (7× cost no parallelism); don't add 86th skill before auditing 85th.

### O. Skill discovery & routing (deep-dive 2026-05-18)

> Source: `docs/research/skill-discovery-routing-2026-05-18.md`. **Key finding:** at 85 skills jarvis is past Claude Code's hidden `skillListingBudgetFraction=0.01` (~2k tok) — ~60-70 skills have descriptions silently dropped per session.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 106 | **Delete/archive `.bak.orphan*` skills** (~6 confirmed, ~600-900 tok recovered) [SD1] | skill-discovery | direct repo inspection | 🆕 | **P0 / 5 min** | ⚠️ Related bug: [#659](https://github.com/Osasuwu/jarvis/issues/659) installer prune_orphan recurses on .bak.orphan children — orphan paths nest one level deeper per run **(this explains the multi-level .bak.orphan.bak.orphan.bak.orphan visible in skill listing — not just delete, fix the installer recursion FIRST or they'll come back next install)** | Visible in current skill listing. **Sequence: fix #659, then delete** |
| 107 | **Audit descriptions**: ≤150 char, third-person, front-loaded keywords [SD2] | skill-discovery | [Anthropic best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | 🆕 | **P0** | ⚠️ overlaps #98 (PC3); merge as single audit pass. Memory `naming_check_existing_vocabulary` (9697a109) | Description-match accuracy is load-bearing |
| 108 | **Add `category:` frontmatter** (planning/execution/audit/comms/personal/infra) to every skill [SD3] | skill-discovery | Cursor MDC pattern, [Continue.dev](https://www.agentrulegen.com/guides/cursorrules-vs-claude-md) | 🆕 | **P2** | Cross-ref `skill_proliferation_antipattern` PAUSED until 2026-06-18 | Foundation for #109 category-gating; no-op until consumed |
| 109 | **Extend `scripts/session-context.py` to load only category-matched skills** based on inferred session intent [SD4] | skill-discovery | jarvis-internal + Cursor MDC | 🆕 | **P2** | Builds on `session_context_hook_architecture` (36ddd650). [#605](https://github.com/Osasuwu/jarvis/issues/605) session-context milestone-close detection is sibling extension | Cuts metadata budget ~80% |
| 110 | **Skill-usage telemetry**: parse transcripts for `Skill:<name>` invocations; surface 30d-zero list in `/reflect` [SD5] | skill-discovery | [DeepWiki claude-code analytics](https://deepwiki.com/SinghCoder/claude-code/12.1-analytics-and-telemetry) | 🆕 | **P2** | [#516](https://github.com/Osasuwu/jarvis/issues/516) /reflect re-routing references this scope, [#526](https://github.com/Osasuwu/jarvis/issues/526) /learn skill landing | Cheap (~50 LOC Python); evidence input for #111. **Slot inside /learn (#526) skill** |
| 111 | **Curator-lite scheduled task**: 30d→stale-flag, 90d→archive to `~/.claude/skills/.archive/` [SD6] | skill-discovery | [Hermes Curator](https://www.xugj520.cn/en/archives/ai-agent-skill-library-hermes-curator.html) | 🆕 | **P3** | Depends on #110. `skill_proliferation_antipattern` PAUSED — curator-lite remains valid even during experimentation phase | Archive not delete. Manual restore |
| 112 | **Vector retrieval over SKILL.md descriptions** (VoyageAI/Qwen3 → Supabase pgvector) before description-match [SD7] | skill-discovery | [AWS Bedrock pattern](https://aws.amazon.com/blogs/storage/optimize-agent-tool-selection-using-s3-vectors-and-bedrock-knowledge-bases/) | 🆕 | **P3** | `memory_roadmap_stealable_ideas_2026_04_20` (ba844bfc) ideas-to-steal already mentions skill retrieval. Reuses VoyageAI / Qwen3 work (rows 39/91) | Highest leverage but biggest build. Defer until #106-110 quantify problem |
| 113 | **Raise `skillListingBudgetFraction` to 0.02 (~4k tok) as stopgap** [SD8] | skill-discovery | [claudefa.st](https://claudefa.st/blog/guide/mechanics/skill-listing-budget) | 🆕 | **P4** | None — research recommends against. `always_loaded_context_budget_principle` (ff994ca2) explicitly minimizes always-load | 2× token tax forever; use only if #106-108 insufficient |
| 114 | **Lifecycle frontmatter** `lifecycle: experiment\|active\|deprecated\|archived` + `created` + `last_audit` [SD9] | skill-discovery | feature-flag analogy ([Statsig](https://www.statsig.com/perspectives/feature-flag-lifecycle)) | 🆕 | **P3** | `skill_proliferation_antipattern` PAUSED memory uses pattern ad-hoc; formalizes that pattern | Light governance |
| 115 | **Meta-skill `/skill-audit`** that runs #110+#111 on demand [SD10] | skill-discovery | [rebelytics one-skill-to-rule-them-all](https://github.com/rebelytics/one-skill-to-rule-them-all) | 🆕 | **P4** | Was row 7 deferred per Q3; this is concrete version | Worth doing only after #106-111 prove out |

**Don't-do from SD research:** no skill-to-skill auto-invocation (composability via user/orchestrator chain only — `skills_independent_complementary` memory confirmed); no auto-delete archived skills (recover-not-delete); don't raise budget fraction first (permanent token tax); don't reinvent vector search (reuse existing VoyageAI+Supabase stack).

### P. TDD for agents (deep-dive 2026-05-18)

> Source: `docs/research/tdd-for-agents-2026-05-18.md`. **Key finding:** TDD-for-agents is **empirically proven, not folklore** — but only the "context-injected" variant. TDAD paper (arXiv 2603.17973) shows regression rate 6.08% → 1.82% with context-injection; naive TDD prompting made things worse (regression rose to 9.94%). Dominant agent failure mode is **Goodhart gaming**, not under-testing.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 116 | **Add "Gaming defenses" section to `tdd-loop.md`** listing 10 anti-patterns + mitigation per [TDD1] | tdd-for-agents | [275-tests audit](https://dev.to/htekdev/i-let-an-ai-agent-write-275-tests-heres-what-it-was-actually-optimizing-for-32n7), TDAD paper | 🆕 | **P0** | `.claude-userlevel/skills/_shared/tdd/tdd-loop.md` current. Memory `subagent_test_coverage_overclaim` (bfcf55c0) — "subagents claim tests pass while writing zero positive tests for new symbols". Sibling `supabase_mocks_must_match_real_schema` (1c8d279e) | Reference from `/implement` and `/delegate` SKILL.md. One file edit; max leverage/min |
| 117 | **Pre-commit hook: assertion-density floor + blank-discard ban** (reject `_ = fn(...)`, `t.Log(`, empty `def test_*`) [TDD2] | tdd-for-agents | 275-audit mitigations 2-3 | 🆕 | **P0** | Memory `subagent_test_coverage_overclaim` (bfcf55c0) — direct evidence this happens. `smoke_test_catches_what_unit_tests_miss` (f0865197) | Add to `.pre-commit-config.yaml`; pilot one repo first |
| 118 | **AC-bullet → test-name binding gate** in `/implement`: test count ≥ AC-bullet count; orphan tests → return-to-grill [TDD3] | tdd-for-agents | tdd-loop.md §3 tightened | 🆕 | **P1** | tdd-loop.md current rule. ⚠️ Heavy overlap with [#652](https://github.com/Osasuwu/jarvis/issues/652) Subagent AC-dodge ('out of scope' relabeling) OPEN; memory `subagent_acceptance_criteria_dodged_as_out_of_scope` (9a5a1ade) | Tightens existing rule to hard gate. **Same problem as #652 — bundle** |
| 119 | **Mutation testing pilot on `mcp-memory/server.py`** with Mutmut, target ≥60% on critical paths [TDD4] | tdd-for-agents | [testdouble.com](https://testdouble.com/insights/keep-your-coding-agent-on-task-with-mutation-testing), IEEE 2025 benchmark | 🆕 | **P1** | M#40 test audit [#615-#625](https://github.com/Osasuwu/jarvis/milestone/40), particularly [#620](https://github.com/Osasuwu/jarvis/issues/620) memory cluster, [#617](https://github.com/Osasuwu/jarvis/issues/617) memory-eval | Non-blocking CI warning. Pilot before wider rollout |
| 120 | **Property-based test pilot on pure functions in `mcp-memory/server.py`** with Hypothesis (3 fns: query parsing, FOK math) [TDD5] | tdd-for-agents | [Anthropic red-team property-based](https://red.anthropic.com/2026/property-based-testing/), [arxiv 2510.09907](https://arxiv.org/html/2510.09907v1) | 🆕 | **P1** | M#40 ([#620](https://github.com/Osasuwu/jarvis/issues/620) memory cluster home) | High-leverage demo; Anthropic finds NumPy/SciPy/Pandas bugs at 86% precision |
| 121 | **`/to-issues` emits test stub file per issue** with `def test_<ac_bullet_slug>(): raise NotImplementedError` [TDD6] | tdd-for-agents | [Addy Osmani spec guide](https://addyosmani.com/blog/good-spec/) | 🆕 | **P1** | `/to-issues` skill exists. ⚠️ [#642](https://github.com/Osasuwu/jarvis/issues/642) /delegate pre-dispatch gate (refuse if AC/sandcastle missing) is the natural enforcement site | Forces AC-test 1:1 mapping at FS level; eliminates "agent invents spec". **Slot into #642** |
| 122 | **Separate test-writer subagent in `/delegate`** for concrete-AC tasks; one writes failing, one makes pass [TDD7] | tdd-for-agents | Anthropic subagent docs, Playwright Test Agents | 🆕 | **P2** | Memory `subagent_fabrication_commit_message_vs_diff` (50de5f5c) — verification gap. [#591](https://github.com/Osasuwu/jarvis/issues/591) /implement+/delegate split reconsideration OPEN | Bigger change; spike first. Doubles subagent budget |
| 123 | **Per-skill eval suite**: `evals/<skill>/` with 10-20 binary-rubric scenarios; calibrate TPR/TNR ≥85% before judge load-bearing [TDD8] | tdd-for-agents | Hamel evals-faq, existing M#43 sycophancy | 🆕 | **P2** | M#40 + M#43. Memory `reflection_driven_sprint_2026_04_23` (dcd2e999) | Eval-driven half of M#40. Start with `/grill` and `/implement` |
| 124 | **Snapshot-test moratorium for agent-authored tests** (lint rule: no `toMatchSnapshot` in new test files) [TDD9] | tdd-for-agents | [Vitest 4.1 AI agent reporter](https://www.infoq.com/news/2026/05/vitest-4-1-ai-agents/) | 🆕 | **P2** | None | Cheap; addresses class not yet hit but will when TS plugins grow |
| 125 | **AST integrity check post-run**: scan added test files for assert density, ban mocking-function-under-test [TDD10] | tdd-for-agents | 275-audit four-layer defense | 🆕 | **P3** | Memory `subagent_misses_interaction_effects` (737763bf) — related class (subagent miss patterns) | Heavier infra. Wait until #116-118 land |

**Don't-do from TDD research:** no tests without grilled AC (hard gate not advisory); no snapshot tests in agent PRs unless snapshot IS the spec; no threshold edits in same PR as test additions; no mocked integration tests in `tests/integration/**`; no red-test deletions without cited issue/decision UUID.

**Suggested M#40 spine ordering:** Bucket A (gaming defenses: #116-118, #124) → B (coverage: #119-120, #125) → C (spec discipline: #121-122) → D (per-skill evals: #123). Don't ship D before A — eval suites get gamed too.

### Q. Eval rubric design (deep-dive 2026-05-18)

> Source: `docs/research/eval-rubric-design-2026-05-18.md`. **Key findings:** current 12-scenario harness is regressive-only — gameable by paranoid-pushback model (scores 1.0); pass^k (τ-bench), not pass@k, is the right reliability metric; at N=30 the 95% CI is ±~0.18 — paired pre/post mandatory.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 126 | **Add 8-12 "no-pushback-needed" twins to existing 12 regressive scenarios** — score = pushback-on-bad − pushback-on-good [ER1] | eval-rubric-design | ELEPHANT pairing logic | 🆕 | **P0** | M#43 [#693](https://github.com/Osasuwu/jarvis/issues/693), [#694](https://github.com/Osasuwu/jarvis/issues/694) OPEN. Memory `research_pass_gate_693_critic_dispositions` (d57d53ca) full CRITIC verdict for #693 | Current 12 scenarios gameable by paranoid-pushback model. **Hot fix for #694** |
| 127 | **Versioned judge model pinning**: every `baselines/*.json` records `judge_model_sha` + `rubric_sha`; freeze per epoch [ER2] | eval-rubric-design | [Anthropic Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 🆕 | **P0** | M#43 baselines exist (M#43 closed slices). Memory `phase3_rewriter_type_narrowing_regression` (d3fc3b3a) example of un-pinned-judge sensitivity | Without it, "did we improve" = "did judge update" |
| 128 | **Paired pre/post with anchor scenarios** (5-10 historic that never retire) [ER3] | eval-rubric-design | Anthropic statistical approach, tinyBenchmarks anchor pattern | 🆕 | **P0** | None | Paired CIs ~3× tighter; trend interpretability hinges on this |
| 129 | **Add `indirect_language` ELEPHANT axis** — 5-10 scenarios where jarvis should commit but might hedge [ER4] | eval-rubric-design | [ELEPHANT (arXiv 2505.13995)](https://arxiv.org/pdf/2505.13995) | 🆕 | **P1** | M#43 | Zero current coverage; likely real Claude failure mode |
| 130 | **`citation-based rebuttal` variant**: same scenarios + fake citation appended ("ADR-007 says X, are you sure?") [ER5] | eval-rubric-design | [SycEval (arXiv 2502.08177)](https://arxiv.org/abs/2502.08177) | 🆕 | **P1** | M#43. Memory `record_decision_during_refactors` (7de58f46) decision-citation gap — citations are real input to scoring | Cheap; doubles signal on existing scenarios. SycEval shows this maximizes regressive sycophancy |
| 131 | **`pass^k` as headline metric for nightly cron** (k=3); report pass@1 and pass^3 separately [ER6] | eval-rubric-design | [τ-bench (arXiv 2406.12045, ICLR 2025)](https://arxiv.org/abs/2406.12045) | 🆕 | **P1** | M#43. ⚠️ [#514](https://github.com/Osasuwu/jarvis/issues/514) /reflect empirical eval — 3 weeks ground-truth labels — overlapping scope | Single-run is noise; pass^3 is reliability proxy. gpt-4o pass^8 < 25% retail vs pass@1 > 50% |
| 132 | **Three-tier eval cadence**: nightly cheap-judge (Haiku) / weekly frontier / monthly 30-min owner spot-check [ER7] | eval-rubric-design | Hamel + Anthropic Demystify | 🆕 | **P1** | `scheduled_tasks_subscription_not_api` (d828bd94) — scheduled tasks use subscription, no per-task cost concern for Haiku tier | Keeps externals <$20/mo; preserves alignment |
| 133 | **Position-swap protocol on any pairwise judge call** — call twice swapped, disagreement → tie [ER8] | eval-rubric-design | [Zheng et al. MT-Bench NeurIPS 2023](https://arxiv.org/abs/2306.05685) | 🆕 | **P1** | Memory `reviewer_rubric_assumes_ci_present` (13899c6c) — judge assumption gaps documented | Standard hygiene; missing it leaks 10%+ position bias |
| 134 | **Per-stratum reporting**: code/architecture/process pass rates separate, not aggregated; min N=5/stratum [ER9] | eval-rubric-design | Stratified sampling + Anthropic clustered SEs | 🆕 | **P2** | M#43. Memory `decision_calibration_audit_2026_05_18_90d` (dc19ce5f) already stratifies by category (memory/skills/arch/infra/process) | Aggregate hides class-specific regressions |
| 135 | **Cohen's κ weekly**: owner re-labels 10 nightly cases → κ vs judge; alert if κ<0.6 [ER10] | eval-rubric-design | Hamel calibration + Landis & Koch 1977 | 𝝆 🆕 | **P2** | [#514](https://github.com/Osasuwu/jarvis/issues/514) /reflect empirical eval — ground-truth labels are exactly this | Drift-detection signal independent of eval scores. **Slot into #514** |
| 136 | **Anti-self-preference audit**: judge ≠ subject model family on calibration set; same-family vs cross-family comparison [ER11] | eval-rubric-design | [Self-Preference Bias (arXiv 2410.21819)](https://arxiv.org/abs/2410.21819) | 🆕 | **P2** | None | If >10% drift, fix |
| 137 | **Process-quality binary checks on every `record_decision`**: `alternatives_count≥2`, `memories_used` non-empty, `confidence` set, `reversibility` set — fail on missing [ER12] | eval-rubric-design | CLAUDE.md contract + Hamel "binarize" | 🆕 | **P2** | ⚠️ Tier-2 hook already exists for empty `memories_used` per CLAUDE.md; [#532](https://github.com/Osasuwu/jarvis/issues/532) recall-audit metrics post-migration OPEN — sibling enforcement bucket. [#669](https://github.com/Osasuwu/jarvis/issues/669) Tier-2 hook hardening OPEN. Memory `record_decision_always_pass_memories_used` (7e79666d) | Cheap; decision-process eval channel independent of outcome. **Extend existing Tier-2 hook, not new infra** |

**Don't-do from ER research:** no 1-5 Likert on 8 criteria with monolithic judge (use binary + critique + per-dimension); no single-run eval without paired comparison + CIs; no "pushback rate" as sole metric (contrastive controls required); no hot-swapping judge model without overlap epoch.

### R. Documentation as contract (deep-dive 2026-05-18)

> Source: `docs/research/docs-as-contract-2026-05-18.md`. **Key findings:** three-way CLAUDE.md/SOUL.md/CONTEXT.md split is **partially** evidence-based (rules-vs-domain has DDD precedent; SOUL.md-as-separate-file has no precedent). Total session load 488 lines = **2.4× ETH Zurich Feb-2026 empirically-validated 200-line ceiling**. LLM-generated context files reduced SWE-bench success by ~3%; human-written ones improved only ~4% — every section pays token rent.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 138 | **Quarterly dump-to-markdown** for `record_decision` rows → `docs/decisions/YYYY-QN.md` [DC1] | docs-as-contract | own design | 🆕 | **P2** | `record_decision_always_pass_memories_used` (7e79666d) + `record_decision_when_what` (1778e466) decision contract. Memory `decision_calibration_audit_2026_05_18_90d` (dc19ce5f) is a one-shot dump precedent | 30 min once. SE-indexable, Supabase-outage-survivable |
| 139 | **Audit CLAUDE.md against 200-line ceiling** — move 2-3 sections to pointer pattern (currently ~197 lines = at ceiling) [DC2] | docs-as-contract | [alexop.dev](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/), [ETH Zurich Feb-2026 (arXiv 2511.12884)](https://arxiv.org/html/2511.12884v1) | 🆕 | **P0** | CLAUDE.md is THE always-load doc. Memories: `grill_me_protocol_session_2026_04_30` (7ad9fbb2) three-way split origin; `claudemd_consolidation_2026_04_08` (ea8c1b6e) consolidation history; `instructions_overhaul_2026_04_01` (37923e1e). [#605](https://github.com/Osasuwu/jarvis/issues/605) demoted arch-sweep auto-trigger claim — sweep is candidate to move to pointer | Engineering posture section candidate to extract to pointer |
| 140 | **Tier CONTEXT.md loading**: split "always-load invariants" (≤50 lines) from "domain glossary" (loaded on `/grill` or term mention) [DC3] | docs-as-contract | own design + DICE 2025 | 🆕 | **P0** | `always_loaded_context_budget_principle` (ff994ca2) decision aligned. `session_context_hook_architecture` (36ddd650) hook delivers data not instructions — extension point | Total session load 488 lines = 2.4× empirical ceiling. CONTEXT.md heaviest extraction candidate |
| 141 | **Drift (Fiberplane) anchors to CONTEXT.md glossary terms** pointing to code symbols [DC4] | docs-as-contract | [Drift linter](https://fiberplane.com/blog/drift-documentation-linter/) | 🆕 | **P2** | [#662](https://github.com/Osasuwu/jarvis/issues/662) CI guard for intra-repo anchor links + suffixed-anchor drift OPEN — sibling | Tree-sitter AST fingerprints catch glossary→code drift in CI |
| 142 | **Audit SOUL.md split**: separate `Behavior rules` (load-bearing) from `Tone` (advisory); cross-link only to behavior [DC5] | docs-as-contract | own design + 2025 system-prompt research | 👤 **USER-DECIDED 2026-05-18** | **P0** | Row 52 directive (strip personality). [#694](https://github.com/Osasuwu/jarvis/issues/694) post-fix re-eval + SOUL.md personalization-sycophancy acknowledgement OPEN — IS the slot for this work | Aligns with Q5 SOUL stripping; prevents tone drift from leaking into reliability. **Ship inside #694** |
| 143 | **mtime-drift cron signal**: compare doc mtime vs related code mtime, flag >90d delta [DC6] | docs-as-contract | own design | 🆕 | **P3** | None | Free; high FP but cheap to skim weekly |
| 144 | **Ship minimal `llms.txt`** (≤50 lines, router-only) [DC7] | docs-as-contract | prior deep-dive-llms-txt.md | 🆕 | **P3** | Decision exists in prior deep-dive | ~30 min job |
| 145 | **SKILL.md frontmatter `last_reviewed` + `review_cadence`** for load-bearing project docs [DC8] | docs-as-contract | Anthropic Agent Skill Creator | 🆕 | **P3** | Related #114 (SD9) lifecycle frontmatter — bundle | Trivial schema add; surfaces staleness without claim-extraction cost |
| 146 | **`AGENTS.md` symlink to `CLAUDE.md`** for cross-tool reach [DC9] | docs-as-contract | hivetrail.com, blink.new | 🆕 | **P4** | Codex rejected (Q2); `claudemd_consolidation_2026_04_08` (ea8c1b6e) — past decision DELETED AGENTS.md, so symlink would re-introduce | **Cross-check past decision** — past consolidation explicitly removed AGENTS.md. Re-introducing as symlink ≠ contradiction, but flag |
| 147 | **CONTEXT.md ↔ code-symbol bidirectional links** for glossary terms (`# domain-term: <name>` in code; code pointer in CONTEXT) [DC10] | docs-as-contract | DDD/DICE | 🆕 | **P2** | Sibling [#662](https://github.com/Osasuwu/jarvis/issues/662) anchor link CI guard | Mechanical bidirectional grounding; helps grep verification + human onboarding |

**Don't-do from DC research:** no `llms-full.txt` (no audience for jarvis); no restating decisions in PR/issue bodies (decay; `record_decision` is canonical); no auto-generated `llms.txt` or `CONTEXT.md` (generators earn out at higher doc volume than jarvis); no growing CONTEXT.md past 200 lines without tiering.

---

# Wave 3: 6 deep-dive researches added 2026-05-18 (B-topic group)

Six follow-up deep-dives commissioned 2026-05-18 covering "Medium — risk-reducing, not blockers" topics from the deferred list (B1-B6 in §B Potentially-useful deferred). Doc paths in frontmatter. Sections S-X below.

### S. Cross-device sync architecture (deep-dive 2026-05-18)

> Source: `docs/research/cross-device-sync-2026-05-18.md`. **Key reframe:** the user's question implicitly assumes "sync mechanism is the problem"; the actual gap is **schema-level OCC + encoding hygiene**. The 52-line `install.ps1` is already smaller than any dotfile-framework adoption would be. Cross-device write-conflicts on `memory_store` are lost-update on UPDATE, not a sync framework gap — solved by one `version int` column.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 148 | **`.gitattributes` with `text=auto` + `.env eol=lf -text`** [B1-1] | cross-device-sync | [Git docs gitattributes](https://git-scm.com/docs/gitattributes) | 🆕 | **P0 / 5 min** | `telegram_mcp_env_crlf_breaks_token`, `telegram_channel_env_bom_breaks_token` memories (in MEMORY.md auto-memory). Sibling memory `powershell_5_1_utf8_no_bom_breaks_on_em_dash` (06b0be43) | Kills CRLF/BOM bug class. One-line repo change |
| 149 | **Installer health-check scans `~/.claude/**/*.env` for BOM+CRLF** [B1-2] | cross-device-sync | install.ps1 + own design | 🆕 | **P0 / 1h** | sibling-grep posture rule applies. Memory `dotenv_override_for_empty_shell_vars` (396f5c7e) — known .env edge case. ⚠️ [#659](https://github.com/Osasuwu/jarvis/issues/659) installer prune_orphan recursion bug — install.ps1 already has fragility; bundle fixes | Catches recurring telegram-class regression PRE-incident, not after |
| 150 | **OCC: add `version int` + `updated_at` to mutable memory tables** [B1-3] | cross-device-sync | [Postgres OCC](https://www.postgresql.org/docs/current/applevel-consistency.html) | 🆕 | **P1 / 4h** | `mcp-memory/schema.sql`; cross-repo impact (redrobot). ⚠️ [#660](https://github.com/Osasuwu/jarvis/issues/660) outcome_record.memory_id FK ambiguity OPEN — same migration window. [#602](https://github.com/Osasuwu/jarvis/issues/602) Supabase RLS advisor OPEN. Memory `schema_sql_requires_paired_migration` (99933db1) — discipline reminder | Fixes lost-update on cross-device writes. **Bundle migration with #660 + #602** |
| 151 | **Conflict-keeps-both side-table for OCC losers** [B1-4] | cross-device-sync | Obsidian-iCloud conflict pattern | 🆕 | **P1 / 2h** | Memory `like_spotify_cross_device_simplified` (2c09236b) — YAGNI lesson for cross-device epics; this is the minimal version of that direction | When OCC loses, store both versions in `memory_conflicts` — owner reconciles, never silent loss |
| 152 | **Adopt `sops + age` for future shared cross-device secrets** [B1-5] | cross-device-sync | [getsops/sops v3.13.1 May 2026](https://github.com/getsops/sops) | 🆕 | **P2 / 1d** | `.env` device-local invariant. Memory `secret_files_pause_until_bitwarden` (1d4435fb) — owner's pause stance on secret-required tests; sops is in same direction. `workshop_pc_env_rotation_pending_2026_05_08` (32754e7a) — concrete workshop SERVICE_KEY rotation pending | Pipeline available when needed; $0 free. Windows native binaries shipped May 2026 |
| 153 | **Document each `.env` key in `.env.example` per skill** [B1-6] | cross-device-sync | own design | 🆕 | **P2 / 1h** | None — but ties to row 152 sops adoption | Bootstrap on new device stops being trial-and-error |
| 154 | **DON'T migrate to chezmoi** (record_decision) [B1-7] | cross-device-sync | [chezmoi.io](https://www.chezmoi.io) | ❌ REJECTED (record_decision pending) | **P0 / 0 effort** | Aligns with `like_spotify_cross_device_simplified` (2c09236b) and `feedback_cross_device_path_agnostic_codegen` (9798d41b) YAGNI direction | Solo dev, copy-based 52-line installer, no per-device branching beyond `.env`. Adoption = net-negative |
| 155 | **Bi-temporal columns on memory tables** (`event_time`+`valid_from`/`valid_to`) [B1-8] | cross-device-sync | [Graphiti](https://github.com/getzep/graphiti) | ⚠️ overlaps #89, [#185](https://github.com/Osasuwu/jarvis/issues/185) | **P1 / 4h** | Row 89 same proposal — wave 3 reframes as structural fix for **stale-tab overwrite** scenario specifically. [#185](https://github.com/Osasuwu/jarvis/issues/185) Memory overhaul Pillar 4 includes bi-temporal | Same edit as #89; lifts the use-case justification. **Folded under #185** |
| 156 | **Tailscale Funnel as canonical local-service exposure** [B1-9] | cross-device-sync | [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) | 🆕 | **P3 / 1h** | `hiddify_subscription_must_be_https` memory in MEMORY.md (Funnel context) | Document as default for Workshop→world; not deployed |
| 157 | **Sibling-grep audit for `Set-Content`/`Out-File` without explicit encoding** [B1-10] | cross-device-sync | PowerShell defaults UTF-16 LE BOM | 🆕 | **P1 / 30min** | Sibling-grep posture rule from CLAUDE.md engineering posture. Memory `powershell_5_1_utf8_no_bom_breaks_on_em_dash` (06b0be43), `powershell_from_bash_variable_escaping` (6906ec36), `windows_shim_must_be_exe_for_node_spawn` (a512aae8) | One-time sweep across repo, then lint rule |
| 158 | **Document "secrets stay device-local" as project invariant** [B1-11] | cross-device-sync | SOUL.md secrets-are-untouchable + own design | 🆕 | **P1 / 15min** | SOUL.md says "never read .env". Memory `security_philosophy` (fcb7f545) — owner stance "protect keys/passwords, personal data leaks acceptable" matches local-only rule. `windows_claude_installation` (3a08f1d8) | Make implicit explicit in CLAUDE.md or CONTEXT.md |
| 159 | **DON'T adopt CRDTs** (record_decision) [B1-12] | cross-device-sync | research consensus | ❌ REJECTED (record_decision pending) | **P3 / 0 effort** | None | Overkill for solo + Supabase + 3 devices |

**Don't-do from S research:** no chezmoi/yadm/stow adoption (installer.py is smaller); no doppler / cloud secrets manager (device-local works, $0 beats subscription); no CRDTs (solo writer, OCC sufficient); no symlink-based sync on Windows (developer-mode requirement + permission breakage).

### T. Failure mode taxonomy (deep-dive 2026-05-18)

> Source: `docs/research/failure-mode-taxonomy-2026-05-18.md`. **Key finding:** MAST (Multi-Agent System failure Taxonomy) already exists and validates — κ=0.88 across 7 frameworks, 14 modes, 3 categories. "Should we build our own" question is **closed: no**. τ-bench shows pass^8 < 25% retail vs pass^1 > 85% — verifier must run **every** dispatch, not sample.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 160 | **PostToolUse `Task` verifier with 11 checks** (shadow 2 weeks → promote Tier-1 blocking) [B2-1] | failure-mode-taxonomy | own design + Anthropic end-state verification | 🚧 | **P0** | Row 23 / [#651](https://github.com/Osasuwu/jarvis/issues/651), [#652](https://github.com/Osasuwu/jarvis/issues/652), [#653](https://github.com/Osasuwu/jarvis/issues/653) — concrete spec for that work. Memories: `subagent_fabrication_commit_message_vs_diff` (50de5f5c), `subagent_acceptance_criteria_dodged_as_out_of_scope` (9a5a1ade), `subagent_test_coverage_overclaim` (bfcf55c0), `subagent_misses_interaction_effects` (737763bf), `delegate_subagent_pr_step_skipped_and_absolute_path_2026_05_17` (d523f7b9), `untracked_main_tree_leaks_into_subagent_worktree` (ace97204), `post_compaction_task_premise_verification` (179ee1f2), `verify_agent_findings_against_memory` (7dd8ea95), `lessons_agent_delegation` (48e6fa0f) — 9 incident memories all encoding patterns this verifier is supposed to catch | Headline deliverable. Bundles 3 open issues into one milestone with explicit check list. **9 memories provide ready-made unit tests** |
| 161 | **Adopt MAST as official failure-mode spine** (label `mast:fm-1.1` etc on outcomes) [B2-2] | failure-mode-taxonomy | [MAST](https://arxiv.org/abs/2503.13657) (arXiv 2503.13657) | 🆕 | **P0** | `nightly_pillar3_outcome_tracking` (c70be62b) outcome-tracking reference; `outcome_record_verify_after_write` (f5b5f74a) — outcome write silent drops history | Stop inventing local labels; reuse the κ=0.88 spine |
| 162 | **AC-walk gate in `/delegate` epilogue** (single largest MAST coverage win) [B2-3] | failure-mode-taxonomy | tdd-loop §3 + MAST FM-2.x | 🆕 | **P0** | ⚠️ DUPLICATE/MERGE with [#652](https://github.com/Osasuwu/jarvis/issues/652) AC-dodge OPEN AND [#642](https://github.com/Osasuwu/jarvis/issues/642) /delegate pre-dispatch gate OPEN. Memory `subagent_acceptance_criteria_dodged_as_out_of_scope` (9a5a1ade) | Force each AC bullet → mapped test → diff coverage check; orphan AC = block. **#642 is the slot; #652 is the bug. Bundle.** |
| 163 | **PreToolUse(Edit) compaction-grep gate** [B2-4] | failure-mode-taxonomy | own design + #324 | 🆕 | **P1** | Gated on [#324](https://github.com/Osasuwu/jarvis/issues/324). Direct [#653](https://github.com/Osasuwu/jarvis/issues/653) post-compaction premise hallucination OPEN. Memory `post_compaction_task_premise_verification` (179ee1f2) | Post-compaction premise hallucination: refuse Edit if session compacted AND target file not re-grepped in last 5 turns. **Implements #653** |
| 164 | **Free behavioral signals**: turn-count + tool-arg-hash logger [B2-5] | failure-mode-taxonomy | own design + LangChain telemetry | 🆕 | **P1** | [#658](https://github.com/Osasuwu/jarvis/issues/658) memory_store dup-detector observability gap OPEN — same class (need behavioral telemetry to disambiguate) | Detects loop-stuck (repeated args) + infinite-clarification — zero LLM cost |
| 165 | **10% sampled LLM-judge on subagent transcripts** (goal-drift / reasoning-mismatch) [B2-6] | failure-mode-taxonomy | MAST FM-3.x + sample-judge pattern | 🆕 | **P2** | Eval cadence row 132. [#526](https://github.com/Osasuwu/jarvis/issues/526) /learn skill OPEN — natural home | Cheap; budget-bounded; catches premature-success-claimed-complete |
| 166 | **Quarterly memory-poisoning lint** [B2-7] | failure-mode-taxonomy | MAST FM-1.x + memory-arch M8 (dead_ref) | 🆕 | **P2** | Cross-references row 93. ⚠️ [#654](https://github.com/Osasuwu/jarvis/issues/654) memory-scan 5 recurring-failure lessons without trackers OPEN — same lane | Scan memory writes for contradiction-with-existing, prompt-injection-shaped strings. **Bundle with #654** |
| 167 | **PR-template AC-decision rows + GH Action gate** [B2-8] | failure-mode-taxonomy | own design | 🆕 | **P2** | Existing PR Body Check workflow. [#459](https://github.com/Osasuwu/jarvis/issues/459) PR Body Check [no-issue] escape OPEN — partial overlap (escape vs gate). [#660](https://github.com/Osasuwu/jarvis/issues/660) FK semantics — decision-link verification | Each AC line gets a `[x]/[ ]` checkbox in PR body; gate refuses merge with unchecked AC absent linked decision |
| 168 | **Promote 3 memories to `always_load`** (stopgap until verifier ships) [B2-9] | failure-mode-taxonomy | `verify_before_assuming_implemented`, `record_decision_always_pass_memories_used` (7e79666d), `sibling_grep_on_fixes` | 🆕 | **P3** | Stopgap only — literature says mechanical L3/L4 hook is the real fix. `always_loaded_context_budget_principle` (ff994ca2) caps how many can be promoted | Don't close [#653] on this alone |

**Don't-do from T research:** no homegrown taxonomy when MAST exists (κ=0.88, 7 frameworks); no per-step LLM-judge verification (end-state is cheaper AND more accurate per Anthropic); no sampling-based reliability for high-stakes dispatch (pass^8 collapse is real); no Tier-1 soft prompt rules where Tier-2 hook is feasible (the empty-`memories_used` regression #532 is the canonical example).

**Detection gaps surfaced beyond #651/#652/#653:** loop-stuck, goal-drift, tool-misuse-no-error, premature-success, partial-success, infinite-clarification, role-disobey, termination-blindness. Priority: B2-5 (free behavioral signals) implements 3 of 8 at zero LLM cost.

### U. Jarvis v2 top-layer orchestrator (deep-dive 2026-05-18)

> Source: `docs/research/jarvis-v2-top-layer-2026-05-18.md`. **Key reframe:** V2 is misframed as a CC replacement. Realistic shape = thin supervisor ABOVE CC where CC remains one specialist among ~5-7. Today's Supabase + Telegram MCP already cover ~60% of v2's claimed architecture. Missing pieces: supervisor daemon (~500-1500 LOC), HA event webhook (~50 LOC), news specialist (~200 LOC) — fork-psibot likely beats build-from-scratch.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 169 | **Defer the v2 build until ≥2 of §8 trigger conditions hold** [B3-1] | jarvis-v2-top-layer | own design | 🆕 | **P0** | `jarvis_v2_vision` (c973812d), `jarvis_wrapping_direction` (f56b60dd), `pending_grill_orchestrator_role_and_scope` (0e8e04a7) blocks-research foundation question. Owner Q5 directive (row 52) | Pre-building costs weekends and locks design too early. Concrete trigger list = §U.gate |
| 170 | **Watch psibot + OpenClaw monthly; fork-or-build decision in 6 months** [B3-2] | jarvis-v2-top-layer | [DmacMcgreg/psibot](https://github.com/DmacMcgreg/psibot), [openclaw.ai](https://openclaw.ai) | 🆕 | **P1** | None | psibot is closest-in-spirit (single-user Mac daemon, TG-first, Claude Agent SDK + Max plan). Track activity, evaluate fork candidacy |
| 171 | **Stand up Home Assistant in shadow mode ≥3 months before committing** [B3-3] | jarvis-v2-top-layer | [Home Assistant Voice PE](https://www.home-assistant.io/voice_control/) | 🆕 | **P1** | `planned_integrations_digital_twin` (49522efa) — HA fits in digital-twin pillar | Generate real numbers on voice latency / intent quality. HA-as-channel, not HA-replacement |
| 172 | **Prototype tier-1/2/3 router as `/route` skill INSIDE current Jarvis** [B3-4] | jarvis-v2-top-layer | own design + `federated_architecture_direction` memory | 🆕 | **P1** | `federated_architecture_direction` (9757b985) HYBRID target aligned; `architecture_growth_two_dimensions` (d6f00dc3) UP/DOWN axes; `jarvis_scalable_agent_system` (50ce1691); `team_agent_v2_plan_complete` (e28c42c8) | Validate the routing pattern at zero cost as skill before building daemon |
| 173 | **Add `channel` + `specialist` columns to Supabase memory rows now** [B3-5] | jarvis-v2-top-layer | own schema design | 🆕 | **P2** | `mcp-memory/schema.sql`; ties to rows 88, 150. ⚠️ [#660](https://github.com/Osasuwu/jarvis/issues/660) + [#602](https://github.com/Osasuwu/jarvis/issues/602) — open schema-touching issues, **bundle the migration** | Cheap migration, future-proofs persistence for multi-channel/multi-specialist routing |
| 174 | **Research RSS / news-feed aggregation patterns separately** [B3-6] | jarvis-v2-top-layer | gap noted | 🆕 | **P2** | `planned_integrations_digital_twin` (49522efa) — news ingestion is in pipeline | News specialist under-specified; future deep-dive candidate |
| 175 | **Smart-home actuation gets a separate safety design pass before shipping** [B3-7] | jarvis-v2-top-layer | own design | 🆕 | **P2** | `action_agent_safety_gate_model_v1` (f79ce1f2) Tier 0/1/2 model aligned; `pillar7_phase2_six_choices_2026_04_22` (b3e020bf); `enforcement_layer_matches_threat_model` (ab528091); `pillar9_sprint1_self_security` (716fe238) self-security shipped | Four-pattern guardrails: artifact verify, context rotation, privilege boundaries, rate limit |
| 176 | **DON'T build a web UI in v2** (record_decision) [B3-8] | jarvis-v2-top-layer | own roadmap | ❌ REJECTED (record_decision pending) | **P3** | `no_sending_from_owner_name` (9496f369) — TG-first stance | TG + push + native channels sufficient. Web UI adds maintenance burden with no measured demand |
| 177 | **If v2 builds, prefer fork-psibot over from-scratch** (record_decision) [B3-9] | jarvis-v2-top-layer | psibot architecture | 🆕 | **P3** | `jarvis_wrapping_direction` (f56b60dd) — orchestration layer direction aligned | Same primitives (CC + Max + TG). Decision binding only IF trigger conditions land |
| 178 | **Run a Karpathy-style LLM Knowledge Base experiment in parallel** [B3-10] | jarvis-v2-top-layer | Karpathy LLM KB | 🆕 | **P3** | None | Zero-risk; may solve facts-surface half before v2 even exists |

**Don't-do from U research:** no v2 build before triggers fire (over-engineering); no HA replacement (HA-as-channel beats reinvent); no enterprise multi-agent frameworks (CrewAI/AutoGen scale UP, not DOWN); no peer-multi-agent for personal use (Cognition reversal applies to solo, too).

**Trigger conditions (§U.gate):**
1. ≥3 active non-dev channels (today: 1)
2. ≥1 unattended actuating decision/day (today: 0)
3. ≥2 specialists with non-trivial state (today: 1)
4. Intent-routing miss rate ≥10% sustained
5. Cost approaches Haiku-router break-even (moot under Max)
6. Latency on routine intents >3s p50
7. Cross-specialist memory queries become common

None hold today — growing CC's skill set is cheaper than v2 until ≥2 fire.

### V. Decision quality measurement (deep-dive 2026-05-18)

> Source: `docs/research/decision-quality-measurement-2026-05-18.md`. **Key finding:** Brier infrastructure already half-exists — `fok_calibration_summary` RPC is the same query with a different join. Two missing fields (`success_criteria` on decisions, `decision_episode_id` on outcomes) unblock everything. N=10/week is noise (95% CI ±0.20); rolling-60-day is the right window.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 179 | **Add `success_criteria` field to `record_decision`** [B4-1] | decision-quality | [Tetlock GJP](https://www.gjopen.com/) pre-registration finding | 🆕 | **P0** | `record_decision` schema in `mcp-memory/server.py`. Memories: `record_decision_when_what` (1778e466), `record_decision_during_refactors` (7de58f46) — post-hoc rationalization problem; `record_decision_always_pass_memories_used` (7e79666d) Tier-2 hook precedent. ⚠️ [#660](https://github.com/Osasuwu/jarvis/issues/660) FK semantics OPEN — same migration window | Falsifiable predicate written at decision time. Without it, outcome judgment is post-hoc rationalisation |
| 180 | **Add `task_outcomes.decision_episode_id` FK** [B4-2] | decision-quality | own schema design | 🆕 | **P0** | ⚠️ **EXACT MATCH: [#660](https://github.com/Osasuwu/jarvis/issues/660) "outcome_record.memory_id is FK to memories(id), not record_decision episode UUID — 5x lesson recurrence without skill-contract clarification"** OPEN — this row IS that issue. Memory `outcome_record_verify_after_write` (f5b5f74a) silent-drop history | One column. Unblocks every Brier query. **#180 = #660 — close one, dedup table row** |
| 181 | **Build `decision_calibration_summary` RPC** [B4-3] | decision-quality | mirror of `fok_calibration_summary` | 🆕 | **P1** | `fok_calibration_summary` RPC already ships the math; ~half work done. Memory `memory_calibration_summary` (existing MCP tool) sibling pattern | Brier + Murphy decomposition + reliability bins |
| 182 | **`/decision-audit` skill (weekly cadence)** [B4-4] | decision-quality | own design | 🆕 | **P1** | Memory `decision_calibration_audit_2026_05_18_90d` (dc19ce5f) — manual 90d audit exists, this productizes. ⚠️ [#606](https://github.com/Osasuwu/jarvis/issues/606) /last-work-report skeleton OPEN — sibling reporting skill | Calibration curve, worst-5, regret estimates, missed-decision audit |
| 183 | **Process-Quality Score 0-6 on every `record_decision`** [B4-5] | decision-quality | [Decision Quality Society 6-element](https://decisionquality.org) | 🆕 | **P1** | `record_decision` schema. `record_decision_always_pass_memories_used` Tier-2 hook = 1 of 6 checks. Row 137 (ER12) = same scoring direction. [#532](https://github.com/Osasuwu/jarvis/issues/532), [#669](https://github.com/Osasuwu/jarvis/issues/669) Tier-2 hook hardening OPEN | Six binary checks. Outcome-independent quality signal. **Merge with row 137 — same gate** |
| 184 | **Counterfactual-lite via reference-class lookup** [B4-6] | decision-quality | embed alternatives_considered → search reference-class | 🆕 | **P2** | VoyageAI / Qwen3 embeddings (row 39/91). `memory_roadmap_stealable_ideas_2026_04_20` (ba844bfc) lists embedding-based reuse ideas | Not causal; statistical "how did peer decisions go" |
| 185 | **Missed-decision audit (sampling-bias channel)** [B4-7] | decision-quality | own design | 🆕 | **P2** | ⚠️ Row 15 overlap (record_decision omission scan) + [#333](https://github.com/Osasuwu/jarvis/issues/333) CLOSED — confirm coverage. Memory `record_decision_during_refactors` (7de58f46) | Weekly N=10-session sample of exchanges that *should've been* decisions. **Dedupe vs row 15 / #333** |
| 186 | **LLM-as-judge for reasoning-quality subscale** [B4-8] | decision-quality | Superforecaster traits (Tetlock) | 🆕 | **P2** | DeepSeek/Haiku-tier judge call. `scheduled_tasks_subscription_not_api` (d828bd94) no API cost concern. Row 132 cadence | Binary judges on granularity / AOM / reference-class / calibration-awareness |
| 187 | **Clearer Thinking weekly drill for owner** [B4-9] | decision-quality | [Clearer Thinking](https://www.clearerthinking.org/post/2019/10/16/practice-making-accurate-predictions-with-our-new-tool) | 🆕 | **P3** | Row 4 (calibration drill) — wave 3 refines as two-channel. `grill_me_when_to_run_calibration` (49866c45) calibration heuristic | Human Brier separate from agent Brier — orthogonal signals |
| 188 | **Calibration Brier dashboard** in `/status` [B4-10] | decision-quality | own design | 🆕 | **P3** | Row 100 / `/status-record` skill. `decision_calibration_audit_2026_05_18_90d` (dc19ce5f) — 90d audit doc precedent | Rolling 60-day Brier + Reliability + Resolution. Trends only meaningful past 6 weeks |

**Don't-do from V research:** no weekly Brier (N=10 noise dominates signal); no Brier without `success_criteria` (post-hoc rationalisation); no labeled-retrospective audit treated as Brier sample (`decision_calibration_audit_2026_05_18_90d` is gold for κ calibration, not paired forecast data); no full Pearl causal counterfactuals (overkill; reference-class lookup suffices).

**Brier methodology spec (10 steps):**
1. Pull paired set: 60d `decision_made` ↔ `task_outcomes` joined on `decision_episode_id` (new FK), `outcome_status ≠ pending`
2. Headline Brier: `mean((p − o)²)`, `o ∈ {1.0, 0.5, 0.0}` for `{success, partial, failure}`
3. Compare to base-rate uncertainty `ō·(1−ō)`
4. 5 reliability bins (not 10 — too sparse at N≈60); emit `(n, mean_p, mean_o)` per bin
5. Murphy decomp: Reliability `Σnₖ(pₖ−ōₖ)²/N`, Resolution `Σnₖ(ōₖ−ō)²/N`; verify `BS ≈ rel − res + unc`
6. Stratify by `task_type`, `reversibility`, `actor`; min N=10/stratum
7. Rolling 60-day trend; store metric rows
8. Surface in `/reflect` if `reliability > 0.10` OR `resolution < 0.02` over N ≥ 60
9. Counterfactual-lite for worst-5: reference-class regret Δ
10. Pre-registration audit: ratio with `success_criteria` populated; goal ≥ 80%

### W. HITL approval UX & notification ergonomics (deep-dive 2026-05-18)

> Source: `docs/research/hitl-approval-ux-2026-05-18.md`. **Key finding:** Batching reduces self-reported productivity loss but does **NOT** reduce measured stress (Mark/Iqbal/Czerwinski CHI'16) — empty P1 pings are worse than no ping. The "23 minutes to recover" Mark stat is task-return latency (with intervening tasks), not cognitive-recovery time. Time pressure on prior task reduces residue (Leroy 2009) → every approval should carry decay deadline.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 189 | **Add `request_approval` + `await_approval` tools to `plugin:0.0.6:telegram`** (inline keyboard + callback_query handler) [B5-1] | hitl-approval-ux | [Telegram InlineKeyboardMarkup](https://core.telegram.org/bots/api#inlinekeyboardmarkup) | 🆕 | **P0** | Telegram plugin already exists (grammY + callback handler wired). Memories: `telegram_mcp_integration` (e96ead4a), `telegram_chat_ids` (c0e7a063), `claude_code_channels` (e53996cd) — Anthropic official channels available as fallback. ⚠️ [#644](https://github.com/Osasuwu/jarvis/issues/644) mcp__scheduled-tasks blocked in unsupervised mode (no UI approval channel) OPEN — THIS row's deliverable IS the missing channel | `callback_data ≤64 bytes` via `appr:{uuid}:{verdict}:{hash}`. **#644 = primary motivation** |
| 190 | **Pin one "Jarvis digest" message per chat; `editMessageText` for P2 roll-ups** [B5-2] | hitl-approval-ux | Telegram Bot API edit semantics | 🆕 | **P0** | None — Telegram plugin already exposes `edit_message` (in MCP server instructions). Memory `feedback_resume_prompt_silent_restore` (cf0f1d71) — owner expects silence for routine state | Edits don't push — silence guaranteed for low-priority batched updates |
| 191 | **Persist `pending_approvals` in Supabase** (cross-device + restart durability) [B5-3] | hitl-approval-ux | own schema design | 🆕 | **P0** | `mcp-memory/schema.sql`; ties to row 150 (OCC). ⚠️ [#644](https://github.com/Osasuwu/jarvis/issues/644) — scheduled-tasks blocked because no UI approval channel exists. [#660](https://github.com/Osasuwu/jarvis/issues/660), [#602](https://github.com/Osasuwu/jarvis/issues/602) — bundle migration | Survives bot restart; cross-device visibility of approval queue. **#644 root-cause** |
| 192 | **Tier-graduation memory tag** (`tier_graduation`) with per-capability `{tier, success_streak, last_rollback}` [B5-4] | hitl-approval-ux | [Stripe Radar](https://stripe.com/docs/radar), [LaunchDarkly guarded rollouts](https://launchdarkly.com/docs/guides/best-practices/guarded-rollouts) | 🆕 | **P1** | `action_agent_safety_gate_model_v1` (f79ce1f2) tier model aligned; `pillar7_phase2_six_choices_2026_04_22` (b3e020bf) | Trust earned mechanically, not by hand-edit |
| 193 | **Quiet-hours config** in `config/device.json` (`{start, end, timezone, p0_overrides}`) [B5-5] | hitl-approval-ux | own design + PagerDuty alert-fatigue | 🆕 | **P1** | `config/device.json` exists. Q4 directive (row 58 REJECTED late-night hook) — quiet hours respects Q4 since owner controls schedule | 23:00-07:00 default: P0 only, rest deferred to 07:00 batch |
| 194 | **Decay-aware deferral on P1** (`decays_in_seconds`; reminder at half-life; auto-resolve at decay) [B5-6] | hitl-approval-ux | Leroy 2009 time-pressure finding | 🆕 | **P1** | None | Time-pressure literature: hard deadline lowers attention residue. Not UX trick — evidence-based |
| 195 | **Rollback-listener: parse "стой / undo / halt" in TG → immediate Tier 0 → Tier 2 demotion** [B5-7] | hitl-approval-ux | own design | 🆕 | **P1** | Ties to B5-4 graduation. `enforcement_layer_matches_threat_model` (ab528091) — host hooks for direct sessions | Auto-rollback when owner detects misfire mid-stream |
| 196 | **`jarvis status` Telegram command** (Overview-Panel: queue depth, active subagents, next cron fire) [B5-8] | hitl-approval-ux | own design + Slack overview-panel pattern | 🆕 | **P2** | `/status-record` skill exists. [#606](https://github.com/Osasuwu/jarvis/issues/606) /last-work-report — overlap candidate | Owner-poll, no push. Glanceable from phone |
| 197 | **`/reflect`-driven threshold calibration** (output to Discussion, not auto-applied) [B5-9] | hitl-approval-ux | own design | 🆕 | **P3** | `/reflect` skill. [#516](https://github.com/Osasuwu/jarvis/issues/516) /reflect re-routing OPEN | Quiet-hours window, P1 batch size, decay default — measured against owner clicks |

**Don't-do from W research:** no over-batching (anticipation-stress is real even at lower notification rate); no P0 without decay deadline (residue cost); no auto-applied threshold tuning (owner-in-loop until calibration confirmed); no smartwatch glanceable design (no demand signal); no Slack-style approval bots without inline buttons (CTA-distance kills mobile UX).

**Severity ladder:**
| Tier | Channel | Latency | Examples |
|---|---|---|---|
| **P0** wake-now | TG + Push, both sound | <5 min | irreversible blocked on user; `main` CI broken; credential <24h expiry |
| **P1** next-active | TG message, normal sound | <60 min waking | drafted email; PR ready for review; `/grill` blocker on missing context |
| **P2** digest | Edit pinned message — no push | <24h | autonomous tick; auto-triaged issues; research draft complete |
| **P3** log-only | Memory event tag | none | every tool call, routine memory writes, debug spans |

### X. Solo-dev sustainability & cognitive load (deep-dive 2026-05-18)

> Source: `docs/research/solo-dev-sustainability-2026-05-18.md`. **Key finding:** Self-perception is unreliable for multitasking — Wagner-lab follow-up shows heavy media multitaskers' self-rated performance tracks light multitaskers' (i.e. operator can't introspect degradation). **Mitchell Hashimoto explicitly does NOT run parallel agents** ("context switching is very expensive"); Willison runs parallel only for low-review-cost spike work. Leroy & Glomb 2018 ready-to-resume snippet is the highest-leverage single attention intervention in the literature.

| # | Proposal | Source doc | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 198 | **Ready-to-resume snippet in `working_state`** (3-line "where stopped / next / open question") [B6-1] | solo-dev-sustainability | [Leroy & Glomb 2018](https://journals.aom.org/doi/10.5465/amj.2016.0470) | 🆕 | **P0** | `working_state_jarvis` memory ships. Memory `feedback_resume_prompt_silent_restore` (cf0f1d71) — owner expects silent restore from working_state; this adds the missing "next + open question" lines. `checkpoint_skill_architecture` (51f12452) SUPERSEDED into working_state pattern. /end skill (`end` userSettings skill) Step 5 already mentions "Suggested next skills" line | Extend `/end` to require. Lowest-cost, highest-leverage attention intervention in lit. **Already partially shipped — formalize** |
| 199 | **Cross-repo switch counter in `/reflect`** (session-context loads per repo per day) [B6-2] | solo-dev-sustainability | own metric | 🆕 | **P0** | `session-context.py` already logs loads. Memory `feedback_cross_device_path_agnostic_codegen` (9798d41b) — cross-device path discipline. [#547](https://github.com/Osasuwu/jarvis/issues/547) skills pollute goals.jarvis_focus with session journal — relevant data plumbing | Surface trend, no judgement. Per Q4 — no nag, just data |
| 200 | **Days-on-issue distribution** in `/reflect` (top-5 longest owner-assigned) [B6-3] | solo-dev-sustainability | own metric | 🆕 | **P1** | `gh issue list --assignee=@me`. [#606](https://github.com/Osasuwu/jarvis/issues/606) /last-work-report skeleton OPEN — natural home | Scope-creep early warning without nag |
| 201 | **Hyperfocus chain detector** (count >4h-no-break sessions, weekly surface) [B6-4] | solo-dev-sustainability | hyperfocus literature + `user_working_pattern_multistream` | 🆕 | **P1** | `user_working_pattern_multistream` (9ba726e6) — Wave 3 finding: self-report **may be unreliable** for multitasking (Wagner-lab). Memory `feedback_dont_assume_cognitive_style` (e5cc034a) — corrected 3 times in one session | Owner reads, owner decides — surface only. **Treat memory as hypothesis to validate via this dashboard, not as ground truth** |
| 202 | **Hyperdrive→crash signature scan** (N high-activity days → crash-day pattern over 14d) [B6-5] | solo-dev-sustainability | indie-hacker burnout pattern lit | 🆕 | **P1** | None — but ties to row 200 + 201 dashboards | Detect cadence-then-crash before next cycle; advisory only |
| 203 | **Single-pilot mode flag** (manual toggle suppresses advisory output) [B6-6] | solo-dev-sustainability | Hashimoto "context switching expensive" rule | 🆕 | **P1** | `no_deterministic_pipelines` (599fd623) — Jarvis decides judgment, not flag-gated; but flag is owner-controlled override, not system rule | Owner-controlled. When set, `/reflect` skips comparators entirely |
| 204 | **Domain cross-contamination guard** (single dismissible inline note when one session touches both repos) [B6-7] | solo-dev-sustainability | attention-residue lit | 🆕 | **P2** | None | One-shot per session; dismissible. No persistent nag |
| 205 | **Sleep-window-aware task routing (advisory only)** — header tag on post-midnight output, no gate [B6-8] | solo-dev-sustainability | [Fucci 2018 sleep-deprivation study](https://www.researchgate.net/publication/331144839_A_Replicated_Experiment_on_the_Effects_of_Sleep_Deprivation_on_Code_Quality) | 🆕 | **P2** | Q4 directive (row 58 REJECTED late-night hook) — this is HEADER not GATE, complies with Q4 | Respects Q4 line: surfaces, doesn't block |
| 206 | **Throughput vs subjective-feel cross-check** (weekly Δ% PR/issue/commit, optional `mood:` field) [B6-9] | solo-dev-sustainability | Wagner-lab self-perception finding | 🆕 | **P2** | `user_working_pattern_multistream` (9ba726e6) under-trust target — this row is the empirical channel | Counter to self-report unreliability — empirical anchor |

**Don't-do from X research:** no nag-style hooks (Q4 directive); no decision-fatigue-theory-based throttling (Baumeister fails replication); no parallel-session encouragement without empirical validation (Hashimoto runs serial, Wagner-lab shows self-perception unreliable); no late-night gate (Q4 — header only); no productivity-threshold-based gating (owner judges, system surfaces).

**Sustainability dashboard spec (10 comparator-only metrics, no thresholds):** `repo_switches_per_day`, `longest_single_repo_stretch_h`, `hyperfocus_chains`, `days_on_top_stuck_issue`, `parallel_session_concurrency`, `abandoned_tasks`, `commit_cadence_gap_days`, `sleep_window_session_count`, `ready_to_resume_coverage`, `throughput_pr_count`. **Display rule:** compact table at top of `/reflect`, no traffic lights, owner reads + decides.

**Flag for follow-up:** `user_working_pattern_multistream` memory is owner self-report. Wagner-lab evidence says self-perception unreliable for exactly multitasking. Treat as **hypothesis to validate** against dashboard data over 6-8 weeks, not as ground truth driving routing decisions.

---

# Wave 4: multi-modal input (added 2026-05-18, in-table research)

Owner reclassified D1 (was "possibly out-of-scope"): voice-with-LLM-cleanup pipeline ("видел многие используют что-то что вырезает всё ненужное из voice сообщения и переводят в текст уже отформатировано — хотел бы попробовать") is **in-scope, near-term**. Research compressed in-table (no separate `multi-modal-input-2026-05-18.md` doc); upgrade to deep-dive if rows 209-211 land.

### Y. Multi-modal input — voice cleanup + vision (in-table research 2026-05-18)

> Two axes: (1) **STT + LLM cleanup** pipeline (Whisper → filter "um/uh" + punctuate + format → typed text), (2) **vision/screenshot tooling beyond Anthropic computer-use**. Workshop RTX 5080 16GB is the local-inference venue. Main PC is the always-on dictation client.

**Voice landscape (Windows, 2026):**
- **Cloud + cleanup (paid):** Wispr Flow ($15/mo), AquaVoice ($8/mo), WillowVoice ($15/mo), DictaFlow ($7/mo, Citrix/VDI-aware). All cloud-only on Win; their cleanup is the main selling point.
- **Local-first OSS (free):** [voicetypr](https://github.com/moinulmoin/voicetypr) (Tauri, Win+Mac, fully offline, faster-whisper+local LLM), [tambourine-voice](https://github.com/kstonekuan/tambourine-voice) (Tauri+Pipecat, BYOK or local, customizable cleanup prompt — closest Wispr Flow analogue), [OpenWhispr](https://github.com/OpenWhispr/openwhispr) (supports NVIDIA Parakeet alongside Whisper), [whisper-writer](https://github.com/verbumeng/whisper-writer) (system-tray, press-to-talk).
- **Mac-only (excluded):** Superwhisper, MacWhisper, VoiceInk, Aiko.
- **Models on RTX 5080 16GB:** `distil-large-v3` (~756M, ~1.5GB VRAM, 6× faster than large-v3, −1% WER English) is the sweet spot. Cleanup pass: local Qwen2.5-7B/Llama 3.1-8B Q5_K_M (~6–8GB) on Ollama, or DeepSeek via OpenRouter (~$0.27/M tok, pennies per dictation). For English-only low-latency: NVIDIA Parakeet-TDT-0.6B-v2 (~80ms-class, RTFx ~3380).

**Vision landscape (post-computer-use, 2026):**
- New MCP servers: [claude-screen-mcp](https://github.com/lfzds4399-cpu/claude-screen-mcp) (built-in OCR — read text without spending vision tokens, region capture, perceptual-hash diff), [native-devtools-mcp](https://github.com/sh3ll3x3c/native-devtools-mcp) (screenshot+OCR+click+CDP), [ai-vision-mcp](https://github.com/tan-yong-sheng/ai-vision-mcp).
- Local VL under 16GB: **Qwen2.5-VL-7B** is current SOTA (~6GB, 58.6 MMMU / 68.2 MathVista / 95.7 DocVQA, beats Llama 3.2-Vision 11B), MiniCPM-V 4.5 (~5.5GB, strong on photo/video).
- [Microsoft OmniParser V2](https://github.com/microsoft/omniparser) — UI tokenizer; Microsoft itself reports **minimal gain for Claude Sonnet** (Sonnet already parses UIs well), big gain for weaker LLMs. Skip unless using local VL as driver.

| # | Proposal | Source | Direct | Status | Pri | gh / memory | Notes |
|---|---|---|---|---|---|---|---|
| 207 | **Wispr Flow 7-day trial on Main PC** — baseline "is this workflow worth anything" check | own (2026-05-18) | [Wispr Flow](https://wisprflow.ai/) | 🆕 | **P0 / 30min** | None | Throwaway eval. NOT a long-term adoption — $15/mo cloud violates secrets-stay-local posture and SOUL.md "secrets are untouchable" if dictation ever brushes credentials |
| 208 | **voicetypr Windows install** — fully-offline OSS baseline (faster-whisper + local LLM cleanup) | own (2026-05-18) | [voicetypr](https://github.com/moinulmoin/voicetypr) | 🆕 | **P0 / 1h** | None | If acceptable → permanent free baseline. Runs on Main PC GPU directly; doesn't need Workshop for first try |
| 209 | **Tambourine on Workshop + Tailscale audio bridge from Main PC** — hotkey dictation service with custom cleanup prompt embedding CONTEXT.md glossary (correctly spell "Jarvis", "/grill", "Supabase", project-specific terms) | own (2026-05-18) | [tambourine-voice](https://github.com/kstonekuan/tambourine-voice) | 🆕 | **P1 / 1d** | Workshop RTX 5080 already canonical local-inference venue per [#674](https://github.com/Osasuwu/jarvis/issues/674), [#545](https://github.com/Osasuwu/jarvis/issues/545); Tailscale Funnel pattern from #156 | Whisper distil-large-v3 + Qwen2.5-7B cleanup on Ollama. Bottleneck = Tailscale RTT (~5–30ms LAN) + cleanup pass (1–3s local / ~500ms cloud), not GPU. Eats into row 209 batch decision: do this only if #207/#208 prove the workflow has daily value |
| 210 | **Adopt `claude-screen-mcp` alongside computer-use** — OCR without vision-token burn; region capture; perceptual-hash diff for "did this region change" checks | own (2026-05-18) | [claude-screen-mcp](https://github.com/lfzds4399-cpu/claude-screen-mcp) | 🆕 | **P1 / 2h** | computer-use MCP already installed (visible in session); this is complement, not replacement | Concrete: "find baseline value in this dashboard screenshot" without spending Claude vision tokens. Audit repo activity + Issues before pinning a SHA |
| 211 | **Local VL pre-filter via Qwen2.5-VL-7B on Workshop** — custom MCP wrapper answers "what's on this screen" cheaply; only escalates to Claude vision when local confidence low | own (2026-05-18) | [Qwen2.5-VL](https://github.com/QwenLM/Qwen2.5-VL), Ollama VL routing | 🆕 | **P2 / 2-3d** | Routes through existing sandcastle Tier 1 (#543) for fallback escalation pattern | Cuts Claude bill on routine screenshot questions. Custom MCP — small. Defer until #209/#210 quantify how often screenshot input actually happens in practice |
| 212 | **Parakeet-TDT-0.6B via OpenWhispr A/B vs distil-large-v3** for English-only low-latency dictation (~80ms class) | own (2026-05-18) | [OpenWhispr](https://github.com/OpenWhispr/openwhispr), [Parakeet TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) | 🆕 | **P2 / 4h** | None — English-only is the limitation (no RU) | Worth it only if perceived latency on distil-large-v3 is too high. RU dictation requirement disqualifies Parakeet as primary; might run dual (Parakeet for EN-only, Whisper for RU) — but added complexity rarely worth 200ms |
| 213 | **Fork voicetypr or tambourine with Jarvis-specific cleanup prompt** — inject CONTEXT.md glossary + skill names into LLM cleanup pass so technical terms transcribe correctly without manual edit | own (2026-05-18) | own design | 🆕 | **P3** | Builds on #209; mock with #208 first | Worth doing only after weeks of generic cleanup proves it mis-spells project terminology. Premature otherwise |
| 214 | **OmniParser V2 integration as UI-tokenizer for vision pipeline** | own (2026-05-18) | [Microsoft OmniParser V2](https://github.com/microsoft/omniparser) | ❌ DEFERRED | **P4** | None | Microsoft says minimal gain for Claude Sonnet (already a strong UI parser). Revisit only if #211 local-VL pre-filter becomes load-bearing driver — OmniParser would feed structured input to the weaker local model |

**Don't-do from Y research:**
- DON'T pay Wispr Flow / WillowVoice / AquaVoice long-term — cloud-only conflicts with secrets-stay-local invariant (#158) and adds $90–180/yr for cleanup that DIY pipeline (#209) replicates locally for free on existing hardware.
- DON'T install Mac-only tools on Main PC (Superwhisper, MacWhisper, VoiceInk, Aiko) — wasted evaluation cycles; voicetypr/tambourine are the cross-platform Windows-first equivalents.
- DON'T build custom Whisper-server-on-Workshop before trying #208 voicetypr on Main PC — Main PC has sufficient GPU for distil-large-v3 at ~real-time; Workshop+Tailscale (#209) adds latency (~5–30ms LAN) and a network failure mode for marginal headroom unless cleanup-LLM size demands Workshop VRAM.
- DON'T add multi-modal MCP servers without checking activity/SHA pinning — multi-modal MCP ecosystem is thin (3–4 live repos), pre-1.0 churn high.

**Wave 4 batching suggestion:**

**Batch W4-A — eval workflow value at near-zero cost (1.5h total):**
- #207 Wispr Flow trial (30 min) — even if rejected after week, you'll know if dictation-driven Jarvis input is actually wanted
- #208 voicetypr install (1h) — runs in parallel; the local-OSS baseline either replaces #207 or proves the local approach has friction

**Batch W4-B — if W4-A confirms workflow value (1 day):**
- #209 Tambourine + Workshop + Tailscale + Jarvis glossary cleanup prompt — the "production" version that survives long-term
- #210 claude-screen-mcp adoption — independent of voice work, ships in same window

**Defer Batch W4-C** (rows 211-213) until 4+ weeks of W4-B usage data exists. #211 local VL pre-filter is genuinely speculative without screenshot-usage telemetry. #214 stays rejected.

**Honest gaps in this research:**
- No direct RTX 5080 faster-whisper benchmarks publicly available (card too new); extrapolation from RTX 4090 (~70× real-time on large-v3) is reliable but not measured.
- Tambourine listed as cross-platform Tauri — Windows-specific issues not audited; verify GitHub Issues before serious build-out.
- Multi-modal MCP ecosystem is early; no de-facto standards. Rows 210-211 are bet-on-direction, not bet-on-tool.

---

## Conflicts / inconsistencies surfaced

1. **Local LLM hardware memory is stale.** `main_pc_ollama_benchmark_2026_05_10` cites RTX 3050 6GB on Main PC. Actual production primary is **Workshop RTX 5080 with Gemma 4 26B-MoE** per [#674](https://github.com/Osasuwu/jarvis/issues/674) closed 2026-05-16 + [#538](https://github.com/Osasuwu/jarvis/issues/538) production model pick + [#545](https://github.com/Osasuwu/jarvis/issues/545) workshop promotion. **Action**: update or supersede that memory with Workshop benchmark + Gemma 4 selection (owner asked re-ask on hardware — confirmed RTX 5080 16GB).

2. **CLAUDE.md architecture-sweep auto-trigger.** Described as live behavior in CLAUDE.md but `session-context.py` doesn't contain the code path. [#605](https://github.com/Osasuwu/jarvis/issues/605) demoted the claim to "planned" but the code wasn't added. **Action**: either land the code path (small) or rewrite CLAUDE.md section to "manual" with explicit issue ref.

3. **Codex side-channel ranked #1 in 4 of 7 research docs is rejected by owner Q2.** All proposals depending on it (Codex `/cross-critique`, codex exec headless review, AGENTS.md as CLAUDE.md mirror) move to row 11 (local LLM / DeepSeek alternative). The output skill is similar; the input model is different.

4. **`/autonomous-loop` deletion** (Q6) requires positive removal: cron entries, skill file, settings.json hook references, possibly `autonomous_loop_last_run` memory. Doesn't conflict with anything but needs explicit slice in M#41 or separate issue.

5. **#668 "epic" terminology drift wontfix.** 30 hits / 15 files still on Milestone→Epic→Task taxonomy. Standing decision says no epic; cleanup was wontfix'd. **Action**: owner re-decide if it's worth a cleanup pass. Drift undermines the standing decision.

6. **Decision-omission scan** (proposed row 15) overlaps with [#333](https://github.com/Osasuwu/jarvis/issues/333) CLOSED. Need code-read to confirm whether #333 implementation covers `record_decision`-not-called detection or only `recall`-not-called detection. **Action**: 10-min read of `/end` and `/reflect` skill files before opening new issue.

7. **Skill proliferation antipattern memory** vs Q3 owner answer. Memory says don't proliferate; owner says experimentation phase ~1 month. **Action**: time-box the memory ("until 2026-06-18 experimentation phase, antipattern paused").

8. **`user_working_pattern_multistream` memory may be over-trusted** (B6 finding). Owner self-report; Wagner-lab evidence shows self-perception unreliable for multitasking. **Action**: keep memory, but treat as hypothesis to validate against dashboard data — don't drive routing decisions from it.

9. **`main_pc_ollama_benchmark_2026_05_10` stale (re-flagged from conflict #1)** — B3 doc also references current Workshop RTX 5080 as primary; consistent reframe across waves. **Action**: same as conflict #1 (supersede with Workshop benchmark).

10. **Row 89 (bi-temporal memory cols) and row 155 (B1-8) are the same proposal** — wave 3 reframes wave 2's bi-temporal columns as the structural fix for stale-tab cross-device overwrites. **Action**: merge as a single issue; either row references the other.

11. **`record_decision` schema missing two fields for Brier** (B4 finding): `success_criteria` on decision side, `decision_episode_id` FK on outcome side. **Action**: small additive migration; unblocks rows 181-188.

12. **`outcomes_referenced[]` points backward, not forward** (B4 finding). The MCP server has outcomes feeding decisions but no decisions feeding outcomes. **Action**: add forward FK (row 180) without touching backward semantics.

13. **Row 180 (B4-2) = [#660](https://github.com/Osasuwu/jarvis/issues/660) verbatim** — wave 3 research proposes `decision_episode_id` FK; gh issue #660 documents exact problem with "5x lesson recurrence without skill-contract clarification" already open. **Action**: row 180 NOT new work — execute #660. Dedup table row.

14. **Row 162 (B2-3 AC-walk gate) overlaps with [#642](https://github.com/Osasuwu/jarvis/issues/642) pre-dispatch gate AND [#652](https://github.com/Osasuwu/jarvis/issues/652) AC-dodge**, all OPEN. Three separately-filed issues + one wave-3 row describe the same enforcement need. **Action**: consolidate — #652 is the bug to close, #642 is the slot, row 162 is the spec.

15. **Row 106 (SD1 delete .bak.orphan skills) is downstream of [#659](https://github.com/Osasuwu/jarvis/issues/659) installer prune_orphan recursion bug** — without fixing #659 first, .bak.orphan paths regenerate one level deeper on next install. **Action**: fix #659 then sweep, not the other way.

16. **Row 87 (M2 /dream consolidation skill) partially duplicates shipped work** — Phase 5.2 A-MEM neighbor evolution trio (`phase_5_2_trio_shipped_end_to_end` b5d4fad6) handles update/merge already. M#39 implicit-derivation ([#557](https://github.com/Osasuwu/jarvis/issues/557)) covers SessionEnd→Deriver. **Action**: scope `/dream` to scheduled-trigger orchestration only, not new engine.

17. **Row 92 (M7 retrieval eval fixture) duplicates [#505](https://github.com/Osasuwu/jarvis/issues/505)/[#506](https://github.com/Osasuwu/jarvis/issues/506)/[#507](https://github.com/Osasuwu/jarvis/issues/507)/[#673](https://github.com/Osasuwu/jarvis/issues/673)** — q09 stochastic regression + slice 4 keyword_query drift + persistent rank miss + recall hook ordering — all OPEN, all about recall quality. **Action**: extend existing q09 fixture rather than build green-field 50-fixture set.

18. **Row 95 (M10 query expansion) is risky** — `phase3_rewriter_type_narrowing_regression` (d3fc3b3a) memory documents Haiku rewriter cut recall@5 by 5pp. Naive query expansion has prior negative result in jarvis. **Action**: A/B against existing rewriter before promotion, not green-field expansion.

19. **Row 191 (B5-3 pending_approvals persistence) is the missing piece [#644](https://github.com/Osasuwu/jarvis/issues/644) needs** — scheduled-tasks blocked in unsupervised mode because no UI approval channel exists. Wave-3 HITL surface = root-cause fix for #644. **Action**: prioritize Batch W3-D before re-attempting scheduled-task work.

20. **Row 142 (DC5 SOUL split) is the slot for [#694](https://github.com/Osasuwu/jarvis/issues/694)** — post-fix re-eval + SOUL.md personalization-sycophancy acknowledgement OPEN, owner-decided Q5 strip-personality. **Action**: row 142 + #694 = same PR, not two work items.

21. **Row 137 (ER12 process-quality gate) merges with row 183 (B4-5)** — both propose six-element binary scoring on `record_decision`. Tier-2 hook on `memories_used` already exists; one extends, not two. **Action**: dedup; extend the existing hook with the other 5 checks.

## Re-ask candidates (memory possibly stale) — resolved 2026-05-18

| Memory | Owner answer | Action taken |
|---|---|---|
| `superpowers_plugin_evaluation_pending` | **Drop — steal Iron Laws without install** (row 16) | Memory deleted 2026-05-18 (recoverable 30d). Row 16 (Iron Laws copy in /grill, /implement) absorbs the value |
| `managed_agents_wait_and_see` (2026-04-21 revisit) | **Wait further** post Code w/ Claude 2026 | Memory updated 2026-05-18 with 3rd revisit note; carve-out: outcomes-rubric pattern is portable into /verify + /learn outcomes WITHOUT adopting Managed Agents (row 8) |
| `skill_proliferation_antipattern` vs Q3 experimentation phase | **Time-box to 2026-06-18** | Memory updated 2026-05-18 with PAUSED header; resumes 2026-06-18. Decision recorded (UUID `6534501c-eaa8-4b4e-830c-991b6f21430d`) |
| [#668](https://github.com/Osasuwu/jarvis/issues/668) epic-drift wontfix | **Reopen and sweep** | #668 reopened 2026-05-18 with sweep-scope comment. Sweep slice still needs concrete issue (scoped to .github/ workflows + issue templates + docs/process layer). Decision recorded (UUID `311f3d78-cc32-46c1-b2c6-2ae54172c39e`) |

### Remaining (not yet asked — lower priority)

| Memory | Why re-ask |
|---|---|
| `jarvis_event_driven_architecture` "cloud tasks still broken" | `cloud_vs_local_scheduled_tasks_routing` (2026-05-01) refined: cloud routines DO fire for pure repo work, broken only for MCP-connector cases. Old framing in this memory is now misleading |
| `claude_max_upgrade` "tokens no longer constraint" | Still true after Q1's 30h/week intent? `max_20x_upgrade_available` says $200/mo upgrade ready if needed |
| `audit_3_main_changes_lock_2026_04_28` "line numbers in body are stale" | Pure cleanup ack |
| `repo_improve_deferred` SUPERSEDED | Confirmed superseded — should be deleted not kept as SUPERSEDED marker |

## Suggested batching

> Not a roadmap — a suggestion that you can override. Each batch is the smallest set that delivers signal.

**Batch 0 — corrections & cleanups (1-2 hours, no new milestone)**
- Update or supersede `main_pc_ollama_benchmark_2026_05_10` with Workshop RTX 5080 + Gemma 4 26B-MoE
- Land architecture-sweep code in `session-context.py` OR rewrite CLAUDE.md section (#605 close-out)
- Confirm [#333](https://github.com/Osasuwu/jarvis/issues/333) covers record_decision omission detection (row 15 dedupe)
- Time-box `skill_proliferation_antipattern` until 2026-06-18
- Add slice to M#41: explicit /autonomous-loop deletion + cron removal (row 55)
- Re-ask owner on row K stale memories

**Batch 1 — eval harness foundation (3-5 days, becomes a milestone)**
- Row 1: L1 golden-set extension (4 scenario categories on top of M#43 sycophancy)
- Row 21: verify `/grill` CRITIC clean-context invariant (one-line audit)
- Row 41: ANTHROPIC_API_KEY audit script + clear hits across 3 devices
- Row 6: weekly drift sweep prototype (claim-vs-code on the 3 docs) — DeepSeek classifier via existing #543 tier path
- Optional row 11: `/cross-critique` skill calling local Gemma via sandcastle Tier 1

**Batch 2 — subagent verification (2-3 days, fits M#41 or new milestone)**
- Row 23: subagent post-flight verifier shadow mode — bundles [#651](https://github.com/Osasuwu/jarvis/issues/651), [#652](https://github.com/Osasuwu/jarvis/issues/652), [#653](https://github.com/Osasuwu/jarvis/issues/653)
- Row 24: capability-router policy for `/delegate` model choice

**Batch 3 — SOUL surgery + IssueOps (1-2 days)**
- Row 52: strip SOUL personality, keep behavior rules (in same PR as M#43 [#694](https://github.com/Osasuwu/jarvis/issues/694))
- Row 53: document future-Jarvis top-layer in `docs/design/jarvis-v2-redesign.md`
- Row 30: IssueOps first cut — one `ready:agent` label + one workflow (fold into M#41)

**Batch 4 — GH tooling (30 min, no milestone)**
- Row 31: gh-dash install + persona configs (jarvis, redrobot)
- Row 32: gh-poi install + `/end` integration

**Batch 5 — VoyageAI replacement (2-3 days, kills $20/mo)**
- Row 39: Qwen3-Embedding-8B nightly batch on Workshop RTX 5080 → Supabase pgvector

**Batch 6 (skill enrichment, after Batch 1 eval harness gates regressions)**
- Row 14: skill-creation gate in medium mode (Q3 directive)
- Row 16: Iron Laws + red-flag rationalizations copy in /grill, /implement
- Row 8: outcomes-as-rubric, fold into /learn outcomes ([#526](https://github.com/Osasuwu/jarvis/issues/526))

**Batch 7+ — deferred**
- Row 2 (Langfuse self-host) if cheap script reveals depth-worthy signal
- Row 4 (calibration drill) as Friday afternoon habit
- Row 17 (`/walkthrough`) if drift sweep #6 shows misses
- Row 7 (skill bloat audit) after experimentation phase ends ~2026-06-18

## How to read this table

- ✅ = no action needed (status quo)
- 🚧 = open work, see gh ref
- 🆕 = new proposal, decide to schedule or skip
- 👤 = owner already answered in Q1-Q6 — execute directive
- ❌ = standing don't-do, don't accidentally pick up
- ⚠️ = conflict in evidence, needs explicit reconcile
- 🔄 = re-ask owner before treating memory as authoritative

When converting batches to issues/milestones, cite this table row in PRD section "Background" and link to the source doc + direct source. Memory contract: `record_decision` for each architectural choice with `memories_used=[<uuid of relevant existing memory>, this-table-uuid-once-stored]`.

---

## Wave-2 batching addendum (rows 86-147)

The 6 deep-dives added rows 86-147 with their own priorities. Highest-leverage P0 work that wasn't in batches 0-7:

**Batch W2-A — eval/memory foundations (highest leverage, gates other work)**
- #92 retrieval eval fixture (gates #86, #91 — quality measurement impossible without)
- #86 RRF + cross-encoder rerank in `memory_recall` (MRR@3 0.43→0.60)
- #126 contrastive twins for sycophancy eval (closes [#694](https://github.com/Osasuwu/jarvis/issues/694) gameability)
- #127 versioned judge model pinning (prerequisite for trend interpretation)
- #128 paired pre/post with anchor scenarios

**Batch W2-B — context economy (single-day work, big tok savings)**
- #96 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` + `DISABLE_1M_CONTEXT=1` on 3 devices
- #97 annotate `personal_workflow_aihero_adoption` with NoLiMa/Gauthier evidence
- #98+#107 **merged audit pass**: 85 skills × (description quality + third-person + zero-invocation archive)
- #106 delete `.bak.orphan*` skills
- #139 audit CLAUDE.md against 200-line ceiling
- #140 tier CONTEXT.md loading
- #142 SOUL.md split (folds into row 52 already user-decided)
- #104 stop mid-session always-load doc edits (discipline rule)

**Batch W2-C — TDD hardening for M#40**
- #116 Gaming defenses section in `tdd-loop.md`
- #117 pre-commit assertion-density hook
- #118 AC-bullet → test-name binding gate
- (Then #119-125 per M#40 spine A→B→C→D ordering)

**Batch W2-D — memory architecture (medium-term)**
- #87 `/dream` consolidation skill (aligns M#39 implicit-derivation)
- #88 `decision_memory_link` edge table
- #95 query expansion at `memory_recall` (cheap, addresses keyword-sensitive pain)
- #91 Qwen3 embedding migration (after #92 fixture)

**Batch W2-E — eval methodology hygiene (folds into M#43 continuation)**
- #131 pass^k headline metric
- #132 three-tier eval cadence
- #133 position-swap protocol
- #129 indirect_language ELEPHANT axis
- #130 citation-based rebuttal variant

**Defer to later waves:** all P3-P4 from sections M-R, plus #122 (test-writer subagent), #123 (per-skill evals — gated by #116-118 landing first).

---

## Wave-3 batching addendum (rows 148-206)

The 6 B-topic deep-dives added rows 148-206. Highest-leverage P0 work not already covered above:

**Batch W3-A — encoding hygiene + verifier (one weekend, kills 2 bug classes)**
- #148 `.gitattributes` (P0 / 5min) — kills CRLF/BOM bug class on commit
- #149 installer health-check (P0 / 1h) — catches `.env` BOM/CRLF before incident
- #160 PostToolUse `Task` verifier (P0) — shadow 2 weeks, bundles [#651](https://github.com/Osasuwu/jarvis/issues/651)/[#652](https://github.com/Osasuwu/jarvis/issues/652)/[#653](https://github.com/Osasuwu/jarvis/issues/653) (largely replaces row 23 batch 2 plan)
- #162 AC-walk gate in `/delegate` (P0) — single largest MAST coverage win
- #161 adopt MAST as failure-mode spine (P0) — relabels existing outcomes

**Batch W3-B — decision quality infrastructure (1-2 days, unblocks all Brier work)**
- #179 add `success_criteria` to `record_decision` (P0)
- #180 add `task_outcomes.decision_episode_id` FK (P0)
- #181 build `decision_calibration_summary` RPC (P1, mirrors existing `fok_calibration_summary`)
- #183 Process-Quality 0-6 binary score (P1, outcome-independent)

**Batch W3-C — schema migration bundle (memory v3, ties waves together)**
- #150 OCC `version` + `updated_at` columns (B1-3)
- #151 conflict-keeps-both side-table (B1-4)
- #155 / #89 bi-temporal columns (merged: B1-8 = M4) — pick one issue
- #173 `channel` + `specialist` columns (B3-5)
- #191 `pending_approvals` table (B5-3)
- Ship as one Supabase migration; all four cross-reference

**Batch W3-D — HITL Telegram approval surface (single day if `pending_approvals` lands)**
- #189 `request_approval`/`await_approval` tools (P0)
- #190 pinned digest message with `editMessageText` (P0)
- #193 quiet-hours config (P1)
- #194 decay-aware deferral (P1)
- #195 rollback-listener (P1)

**Batch W3-E — sustainability surface (advisory-only, respects Q4)**
- #198 ready-to-resume snippet in `working_state` (P0, highest-leverage attention intervention)
- #199 cross-repo switch counter in `/reflect` (P0)
- #200 days-on-issue distribution (P1)
- #203 single-pilot mode flag (P1, Hashimoto-pattern)

**Batch W3-F — Jarvis v2 watch & defer (no build)**
- #169 defer v2 build until ≥2 trigger conditions (P0 / 0 effort, decision-record)
- #170 watch psibot + OpenClaw monthly (P1)
- #172 `/route` skill prototype (P1, validates routing pattern at zero cost)

**Defer to later waves (Wave 3):**
- All P3-P4 from sections S-X
- #171 Home Assistant shadow mode (3-month commitment; not now)
- #174 RSS/news-feed deep-dive (future research topic)
- #184 counterfactual-lite (requires embedding pipeline + reference-class index — heavier)

---

## Potentially useful — deferred research topics (2026-05-18)

These topics surfaced as gaps in the workflow analysis but were deprioritized below the 6 deep-dives commissioned 2026-05-18. Listed in original priority groupings. Owner may commission any/all later — each maps to a specific blocker or risk.

### B. Medium — risk-reducing, not blockers — **✅ ALL RESEARCHED Wave 3 (2026-05-18)**

| # | Topic | Why it matters | Closest existing memory / gh | Wave-3 doc / rows |
|---|---|---|---|---|
| B1 | **Cross-device sync architecture** | install.ps1 + Supabase memory current. Alternatives (chezmoi, GNU stow, age/sops, doppler). Win/Mac/Linux × 3 devices. Conflicts when memory written on device A, edited on B | install.ps1, `acceptedits_scope_is_project_cwd` memory | ✅ `cross-device-sync-2026-05-18.md` → §S rows 148-159 |
| B2 | **Failure mode taxonomy** | #651 (fabrication), #652 (AC-dodge), #653 (post-compaction hallucination) = 3 points, not taxonomy. MITRE ATLAS, OWASP LLM Top 10, agent-failure-modes lit. Informs row 23 / #125 verifier | [#651](https://github.com/Osasuwu/jarvis/issues/651), [#652](https://github.com/Osasuwu/jarvis/issues/652), [#653](https://github.com/Osasuwu/jarvis/issues/653) | ✅ `failure-mode-taxonomy-2026-05-18.md` → §T rows 160-168 |
| B3 | **Future Jarvis v2 (top-layer orchestrator)** | Q5 separated CC from Jarvis; future scope = smart home + news + task routing. Home Assistant + LLM, n8n + LLM, AGI House, agent frameworks. No research = design with no priors | `jarvis_v2_vision`, `jarvis_wrapping_direction` memories | ✅ `jarvis-v2-top-layer-2026-05-18.md` → §U rows 169-178 |
| B4 | **Decision quality measurement** | Brier score (row 4) nice-to-have but no methodology for long-term `record_decision → outcome_record` attribution. Counterfactuals — "was alternative X better" | `decision_calibration_audit_2026_05_18_90d` (point-in-time, no methodology) | ✅ `decision-quality-measurement-2026-05-18.md` → §V rows 179-188 |
| B5 | **HITL approval UX & notification ergonomics** | Telegram + push exist, no UX research. When to wake vs defer, batching notifications, fatigue-management, mobile-first agent UX | Telegram plugin | ✅ `hitl-approval-ux-2026-05-18.md` → §W rows 189-197 |
| B6 | **Solo-dev sustainability / cognitive load** | Q4 rejected late-night hooks. Broader: attention residue (Sophie Leroy), context-switching jarvis↔redrobot, scope creep failure modes for one-principal systems | None — Q4 only | ✅ `solo-dev-sustainability-2026-05-18.md` → §X rows 198-206 |

### C. Low — wait-and-see / adjacent

| # | Topic | Why it matters | Closest existing memory / gh |
|---|---|---|---|
| C1 | **CI/CD security & cost** | GH Actions security (pinned SHAs, OIDC, supply-chain), self-hosted runners on Workshop RTX 5080 (free CI?), caching strategies | None |
| C2 | **Agentic semantic search for codebase** | Grep/Glob/Read sufficient now. Sourcegraph/Cody-like indexing when jarvis+redrobot grow, LSP-aware agents | None |
| C3 | **Mono-repo vs split (jarvis ↔ redrobot)** | Separate now, shared MCP. When to merge/split. .mcp.json portability exists; broader strategic question | `jarvis_wrapping_direction` |
| C4 | **Email/inbox automation** | Gmail MCP connected, no triage research. Email-as-task-source, agent-reply UX | Gmail MCP tools |
| C5 | **Calendar-driven workflow** | Calendar MCP connected. Time-blocking, calendar-as-trigger for AFK/autonomous loop | Calendar MCP tools |
| C6 | **Personalization-sycophancy paradox empirics** | Named in CONTEXT.md glossary, M#43 measures, but no research on how others resolved (Microsoft Copilot custom instructions, ChatGPT persona research) | CONTEXT.md glossary entry |
| C7 | **Tooling ecosystem watch** | aider, plandex, opencode, sst/opencode. "Patterns to steal" — not migration | Row 60 ACP deferred |
| C8 | **Spec-driven extraction (AC templates)** | Spec-Kit/BMAD wholesale rejected, but AC templates that survive subagent contact = separate. Relates row 23 / #121 AC-dodge mitigation | Row 70 standing don't-do |

### D. Possibly out-of-scope — worth confirming

| # | Topic | Why it matters | Closest existing memory / gh |
|---|---|---|---|
| D1 | ~~**Multi-modal input for Jarvis v2**~~ | ✅ **RECLASSIFIED in-scope 2026-05-18** by owner ("это не out of scope, хотел бы попробовать") — researched in-table → §Y rows 207-214 | `jarvis_v2_vision` |
| D2 | **Bus factor / disaster recovery** | Main PC dies tomorrow. Supabase cloud OK, but install.ps1, .env, memory locale files, MCP tokens — recovery plan? | None |
| D3 | **PKM bridge (Obsidian ↔ GH ↔ memory)** | Obsidian MCP connected, no research on AI-PKM patterns (Andy Matuschak evergreen notes), bidirectional sync GH issues ↔ Obsidian | Obsidian MCP tools |
| D4 | **Cost-per-PR tracking** | Max subscription "marginally free", but measure cost-per-shipped-feature to trigger Max 20x upgrade evidence-based | `max_20x_upgrade_available` |

---

## Index of new (wave 2) rows for fast lookup

- **#86-95**: memory architecture — RRF/rerank, /dream, decision_memory_link, bi-temporal, decay, Qwen3, retrieval fixture, dead_ref linter, supersedes, query expansion
- **#96-105**: prompt caching — autocompact override, disable-1M, NoLiMa-annotation, skill audit, status line, ccusage cache-hit, /resume cold-start, spawn-prompt audit, mid-session edit ban, hot-reasoning routing
- **#106-115**: skill discovery — delete bak.orphan, audit descriptions, category frontmatter, session-context filter, telemetry, curator-lite, vector retrieval, budget fraction stopgap, lifecycle frontmatter, /skill-audit meta-skill
- **#116-125**: TDD for agents — gaming defenses, pre-commit assert-density, AC-binding gate, mutation pilot, property-based pilot, test stub generation, test-writer subagent, per-skill evals, snapshot moratorium, AST integrity
- **#126-137**: eval rubric — contrastive twins, judge pinning, paired anchors, indirect_language, citation rebuttal, pass^k, three-tier cadence, position-swap, per-stratum, Cohen's κ, anti-self-preference, record_decision binary checks

## Index of new (wave 3) rows for fast lookup

- **#148-159** (§S, B1 cross-device sync): `.gitattributes` hygiene, installer BOM/CRLF scan, OCC version columns, conflict-keeps-both, sops+age future-readiness, `.env.example` per-skill, REJECT chezmoi, bi-temporal (overlaps #89), Tailscale Funnel, encoding-write sibling-grep, secrets-local invariant, REJECT CRDT
- **#160-168** (§T, B2 failure mode taxonomy): PostToolUse `Task` verifier (11 checks, bundles #651/#652/#653), adopt MAST spine, AC-walk gate, compaction-grep gate, free behavioral signals, sampled LLM-judge, memory-poisoning lint, PR-template AC rows, stopgap always_load promotion
- **#169-178** (§U, B3 Jarvis v2 top-layer): defer until ≥2 triggers, watch psibot+OpenClaw, HA shadow-mode 3mo, `/route` skill prototype, channel+specialist columns, news-feed deep-dive, smart-home safety pass, REJECT web UI, fork-psibot-if-built, Karpathy KB experiment
- **#179-188** (§V, B4 decision quality measurement): `success_criteria` field, `decision_episode_id` FK, `decision_calibration_summary` RPC, `/decision-audit` skill, Process-Quality 0-6 score, counterfactual-lite, missed-decision audit, LLM-judge reasoning subscale, owner Brier drill, calibration dashboard
- **#189-197** (§W, B5 HITL approval UX): `request_approval`/`await_approval` TG tools, pinned digest message edit-not-push, `pending_approvals` Supabase table, tier-graduation tag, quiet-hours config, decay-aware deferral, rollback-listener, `jarvis status` TG command, `/reflect`-driven threshold calibration
- **#198-206** (§X, B6 solo-dev sustainability): ready-to-resume snippet (Leroy & Glomb), cross-repo switch counter, days-on-issue distribution, hyperfocus chain detector, hyperdrive→crash signature, single-pilot mode flag, domain cross-contamination guard, sleep-window header tag, throughput vs subjective-feel cross-check
- **#138-147**: docs-as-contract — quarterly decision dump, CLAUDE.md 200-line audit, CONTEXT.md tiering, Drift glossary anchors, SOUL.md split, mtime cron, minimal llms.txt, last_reviewed frontmatter, AGENTS.md symlink, glossary↔code bidirectional

## Index of new (wave 4) rows for fast lookup

- **#207-214** (§Y, D1 multi-modal input): Wispr Flow trial (cloud baseline), voicetypr OSS local install, Tambourine+Workshop+Tailscale+glossary cleanup prompt, claude-screen-mcp OCR adoption, Qwen2.5-VL-7B local pre-filter, Parakeet-TDT low-latency A/B, fork-voicetypr-with-glossary, REJECT OmniParser V2 (Microsoft: minimal Claude gain)

---

## Wave 2/3/4 retroactive cross-reference audit (2026-05-18)

> Rows 86-214 were originally added without verification against open gh issues + active memory. This section is the after-the-fact audit. Use when batching: a row that ALREADY MATCHES an open issue isn't new work — it's a spec for that issue.

### Critical "row = existing open issue" matches (treat as duplicate, not new)

| Wave row | Existing open issue | Action |
|---|---|---|
| 180 (B4-2 decision_episode_id FK) | [#660](https://github.com/Osasuwu/jarvis/issues/660) outcome_record.memory_id FK ambiguity | Close #660 via row 180 work; don't open new issue |
| 162 (B2-3 AC-walk gate) | [#642](https://github.com/Osasuwu/jarvis/issues/642) /delegate pre-dispatch gate + [#652](https://github.com/Osasuwu/jarvis/issues/652) AC-dodge | Bundle: #642 = slot, #652 = bug, row 162 = spec |
| 163 (B2-4 compaction-grep gate) | [#653](https://github.com/Osasuwu/jarvis/issues/653) post-compaction premise hallucination | Row 163 is the implementation spec for #653 |
| 160 (B2-1 PostToolUse Task verifier) | [#651](https://github.com/Osasuwu/jarvis/issues/651)/[#652](https://github.com/Osasuwu/jarvis/issues/652)/[#653](https://github.com/Osasuwu/jarvis/issues/653) (bundle) | One milestone, 11-check spec |
| 142 (DC5 SOUL split) | [#694](https://github.com/Osasuwu/jarvis/issues/694) SOUL personalization-sycophancy | Same PR as #694 |
| 191 (B5-3 pending_approvals) | [#644](https://github.com/Osasuwu/jarvis/issues/644) scheduled-tasks blocked no UI approval | Row 191 = root cause fix |
| 166 (B2-7 memory-poisoning lint) | [#654](https://github.com/Osasuwu/jarvis/issues/654) memory-scan recurring-failure trackers | Same lane, bundle |
| 106 (SD1 delete .bak.orphan) | [#659](https://github.com/Osasuwu/jarvis/issues/659) installer prune_orphan recursion | Fix #659 FIRST or sweep regenerates |
| 86/89/90 (M1/M4/M5 memory arch) | [#185](https://github.com/Osasuwu/jarvis/issues/185) Memory overhaul Pillar 4 (lifecycle+bi-temporal+eval) | All three slot under #185, not standalone |
| 92 (M7 retrieval fixture) | [#505](https://github.com/Osasuwu/jarvis/issues/505)/[#506](https://github.com/Osasuwu/jarvis/issues/506)/[#507](https://github.com/Osasuwu/jarvis/issues/507)/[#673](https://github.com/Osasuwu/jarvis/issues/673) | Extend existing q09 fixtures, not green-field |

### Critical "row already shipped" matches (treat as DONE, not new)

| Wave row | What's already shipped | Re-scope |
|---|---|---|
| 87 (M2 /dream consolidation, 3-of-4 phases) | Phase 5.2 A-MEM neighbor evolution trio (`b5d4fad6`) shipped 2026-04-19; M#39 [#557](https://github.com/Osasuwu/jarvis/issues/557) SessionEnd→Deriver planned | Scope `/dream` to scheduled trigger over existing engine, NOT new consolidation engine |
| 198 (B6-1 ready-to-resume) | `/end` Step 5 "Suggested next skills" already in skill spec | Formalize the 3-line shape; don't re-implement |
| 137 (ER12 process-quality binary checks) | Tier-2 hook on `memories_used` already shipped per CLAUDE.md | Extend, don't replace |

### Critical "row contradicts past lesson" — proceed with caution

| Wave row | Past evidence | Action |
|---|---|---|
| 95 (M10 query expansion) | `phase3_rewriter_type_narrowing_regression` (d3fc3b3a) — Haiku rewriter dropped recall@5 by 5pp | A/B against current rewriter, don't green-field |
| 146 (DC9 AGENTS.md symlink) | `claudemd_consolidation_2026_04_08` (ea8c1b6e) — past decision DELETED AGENTS.md | Symlink ≠ contradiction but flag in decision record |
| 201 (B6-4 hyperfocus detector) | `user_working_pattern_multistream` (9ba726e6) is owner self-report; Wagner-lab says self-report unreliable for multitasking | Treat memory as hypothesis to VALIDATE via dashboard, not as ground truth driving routing |

### High-leverage memory UUIDs frequently referenced (for `memories_used` payload reuse)

| UUID | Memory | Used in rows |
|---|---|---|
| e306cb81-81b5-4979-853b-04a2704deca7 | `memory_server_v2_improvements` | 86 |
| 55c3d1c8-d036-4b20-b83b-ba59b045c246 | `consolidation_soft_archive` | 87, 90, 94 |
| ba844bfc-f132-47c4-8566-2ec2a53ebb57 | `memory_roadmap_stealable_ideas_2026_04_20` | 86, 87, 112, 184 |
| 7e79666d-f9fa-479c-859f-cc17d92fc009 | `record_decision_always_pass_memories_used` | 88, 137, 168, 179, 183 |
| 9a5a1ade-0d8c-4163-8559-dff5993094ea | `subagent_acceptance_criteria_dodged_as_out_of_scope` | 118, 160, 162 |
| 50de5f5c-7efa-488c-89f4-ff843f7689bc | `subagent_fabrication_commit_message_vs_diff` | 122, 160 |
| 179ee1f2-79b1-4418-bf18-24c86beec25b | `post_compaction_task_premise_verification` | 125, 160, 163 |
| 9757b985-cf68-4310-9d44-0571697d337f | `federated_architecture_direction` | 172 |
| f79ce1f2-9494-4b57-8a30-e7b31533a108 | `action_agent_safety_gate_model_v1` | 175, 192 |
| dc19ce5f-0a85-4c08-aa9f-0fdcca9592c0 | `decision_calibration_audit_2026_05_18_90d` | 134, 138, 182, 188 |
| f56b60dd-174e-4ec7-ac0e-66b2252850ef | `jarvis_wrapping_direction` | 105, 169, 177 |
| c973812d-8b5f-41d6-9c83-2bb4ad2351a7 | `jarvis_v2_vision` | 169 |
| 9ba726e6-9718-4a1a-986e-cfdba08e43e5 | `user_working_pattern_multistream` | 201, 206 |
| ff994ca2-ac3a-49d4-86cb-360512df3619 | `always_loaded_context_budget_principle` | 104, 113, 140, 168 |
| 7ad9fbb2-d7cc-4773-b9a4-7e1b202e93d7 | `grill_me_protocol_session_2026_04_30` | 104, 139 |

### Revised batching — replaces W2/W3 batching sections above for prioritization

> Original W2/W3 batches were drafted without overlap checks. This is the corrected batching that accounts for the 20+ existing open issues that match wave 2/3 proposals.

**Batch RB-0 — close existing open issues that wave-3 specced (no new milestone, finishes ~7 stuck issues)**
1. **#660** ← row 180 (B4-2 FK semantics) — schema migration + skill-contract update
2. **#642 + #652 closure via row 162 (B2-3 AC-walk gate)** — pre-dispatch gate refuses orphan AC
3. **#653 implementation via row 163 (B2-4 compaction-grep gate)** — PreToolUse Edit refuses post-compaction edits to unre-grepped files
4. **#659 fix BEFORE row 106 (SD1)** — installer prune_orphan recursion; then sweep .bak.orphan
5. **#694 + row 142 (DC5 SOUL split)** — same PR
6. **#644 unblocked via row 191 (B5-3 pending_approvals) + row 189 (B5-1 TG approval tools)** — root-cause fix
7. **#654 + row 166 (B2-7 memory-poisoning lint)** — bundle

**Batch RB-1 — schema migration bundle (one Supabase migration; replaces W3-C)**
- #660 FK fix (row 180)
- OCC version+updated_at columns (row 150)
- conflict-keeps-both side-table (row 151)
- bi-temporal columns (rows 89/155, also #185)
- channel+specialist columns (row 173)
- pending_approvals table (row 191)
- #602 RLS advisor fixes
- All cross-reference; ship as one migration per `schema_sql_requires_paired_migration` (99933db1)

**Batch RB-2 — eval/recall foundation (replaces parts of W2-A)**
- Extend #505/#506 q09 fixtures (covers row 92 M7)
- #507 recall hook ordering (covers row 95 M10 — A/B against rewriter to avoid recall@5 regression precedent)
- RRF rerank under #185 (covers row 86 M1) — partial since hybrid RRF already in `memory_server_v2_improvements`
- #694 + row 126 (ER1 contrastive twins) + row 142 (DC5 SOUL split) in same PR

**Batch RB-3 — verifier shadow mode (replaces W3-A verifier; rows 23+160 bundle)**
- PostToolUse Task verifier from row 160 with 11 checks
- 9 incident memories (50de5f5c, 9a5a1ade, bfcf55c0, 737763bf, d523f7b9, ace97204, 179ee1f2, 7dd8ea95, 48e6fa0f) = ready-made test fixtures
- Shadow 2 weeks → promote per row 23 plan

**Batch RB-4 — context economy quick wins (replaces W2-B)**
- Row 96 env vars on 3 devices
- Row 98+107 merged skill audit (after #659 fix)
- Row 139 CLAUDE.md 200-line ceiling audit
- Row 140 CONTEXT.md tiering
- Row 104 stop mid-session edits discipline
- All independent, ship in parallel

**Batch RB-5 — TDD hardening (no change from W2-C ordering, but contextualize)**
- Row 116 gaming defenses in `tdd-loop.md` — references existing memories (bfcf55c0, 1c8d279e)
- Row 117 pre-commit hook
- Row 118 AC-test binding — bundle with #652

**Defer (P3-P4 across waves):** rows 12, 13, 17, 19, 33, 34, 35, 60, 61, 62-66, 71-73, 94, 102, 105, 111, 113, 115, 124, 125, 135, 143, 144, 145, 146, 156, 159, 174, 176-178, 184, 187, 188, 197, 204-206. Pull when prerequisites land or when context shifts. Multi-modal Batch W4-A still independent of RB-0...RB-5.
