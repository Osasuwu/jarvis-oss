# Build-vs-buy matrix — capability adoption (2026-04-27)

Companion to [`jarvis-v2-redesign.md`](jarvis-v2-redesign.md). Two parallel scout reports + filter against prior architecture decisions.

## Constraints (load-bearing)

These are principal-locked decisions that pre-filter every adoption candidate:

1. **`architecture_final` (2026-03-28, confirmed 2026-03-31):** *"Anthropic-native stack only. No custom Python services. Before writing Python for any feature: can Claude Code skills/MCP/hooks/subagents do it? If yes → don't write Python."*
   - Filters out: Cognee container, Letta runtime, LiteLLM proxy, Phoenix backend, OpenHands/Goose/Cline/Continue runtimes.
   - Permits: MCP servers (separate process, but standard), Claude Code plugins/skills/hooks, libraries imported into the existing `mcp-memory/server.py`.
2. **`memory_alternatives` (2026-03-30):** mem0 already rejected (no native VoyageAI embedder, LLM cost on every write hurts $20/mo budget). Hindsight rejected (Docker-local = no cross-device sync).
3. **`research_pillar7_multi_agent_frameworks` (2026-04-22):** "Claude Code native + Agent Teams + Routines + **selective** LangGraph". LangGraph carveout permitted as one library-backed MCP for plan checkpointing — not as primary orchestrator.
4. **`team_knowledge_sharing_research_2026`:** Pattern C hybrid wins — git-committed context (CLAUDE.md, skills) + Supabase MCP for shared knowledge. No tool uniformity attempt.

Effect: the "70% of substrate covered by Cognee + LiteLLM + Phoenix" recommendation from scout 1 is **not** valid for this stack. Heavy-runtime substrates contribute *patterns and schemas*, not running code.

## Per-capability matrix

Three columns: **Adopt** (drop-in, integrates as MCP/plugin/hook/skill), **Pattern** (read code/schema, reimplement against own substrate), **Custom** (no good source).

### Identity layer

| Cap | Adopt | Pattern | Custom |
|---|---|---|---|
| **C1 Identity** | Claude Code Project Rules / `CLAUDE.md` / `SOUL.md` (current pattern) — established | — | — |
| **C2 Goals** | — | — | Stays custom — `goals` table + skill. Cap is small enough that adoption overhead > build cost. |

### Cognition layer

| Cap | Adopt | Pattern | Custom |
|---|---|---|---|
| **C3 Memory** | None (heavy runtimes filtered) | **Zep / Graphiti** ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)) — bi-temporal column shape (`t_valid`/`t_invalid`/transaction-time). **Cognee** schema — node/edge + embedding. **LangGraph PostgresStore** for namespace-tuple `(facts \| episodes)` indexing pattern. | Bi-temporal `valid_from`/`valid_to`/`superseded_by` migration on existing `mcp-memory/server.py`. Conflict classifier (Haiku ADD/UPDATE/SUPERSEDE/NOOP). Provenance-aware ranking. |
| **C4 Reasoning** | **sequential-thinking MCP** (already installed). **LangGraph as one MCP server** (Federation & Delegation carveout) wrapping Postgres checkpointer pointing at Supabase. | **Magentic-One ledger** ([MS Agent Framework 1.0](https://github.com/microsoft/agent-framework), MIT) — verified-facts / derived-facts / guesses tri-ledger pattern for plan state. **Plan-and-Execute** template (LangGraph). | `applies_when` / `skip_if` template runtime. Replan-on-stall. Skills→templates migration glue. |
| **C5 Reflection** | None | **Letta sleep-time agents** (Apache-2.0) — async secondary writer pattern. Implement as Routine. **Reflexion** (MIT) — verbal-feedback loop algorithm. **Cognee evolution pass** — synthesis-on-ingest reference. | Stale-belief challenger. LLM-as-judge Brier-score calibration. Principal-correction → label loop. |
| **C6 Decision gating** | **PreToolUse hooks + permissions** (Claude Code native). **Guardrails AI** (Apache-2.0) — Pydantic schemas for *output* validation only, called from hook. | — | **Everything substantive.** State-aware classification (git status, convergence counter, harness restrictions) has no library — confirmed by scout 2: all gating frameworks are stateless. Tier 1 queue table custom. Auto-emit decision events custom. |

### Action layer

| Cap | Adopt | Pattern | Custom |
|---|---|---|---|
| **C7 Execution** | Claude Code native tools (Read/Edit/Write/Bash/Glob/Grep) + MCP. Established. | — | Tool-fallback chains, rate-limit handling. |
| **C8 Sub-orchestration** | Claude Code agents with `isolation: worktree` (only worktree-aware primitive). | **LangGraph Supervisor** state-machine pattern. **Magentic ledger** for handoff payload shape. | HEAD-shift detector + diff-outside-scope post-gate (compensates for [#39886](https://github.com/anthropics/claude-code/issues/39886) and [#50850](https://github.com/anthropics/claude-code/issues/50850) open bugs). Supabase TTL+heartbeat lock. `git stash --include-untracked` pre-dispatch. Structured handoff event. |
| **C9 Tool / env** | MCP for everything. **MCP OAuth 2.1 + Resource Indicators** mandatory since 2025-11-25 — affects `.mcp.json`. | — | Per-device principal verification. |
| **C10 Research** | **GPT Researcher** ([github](https://github.com/assafelovic/gpt-researcher), Apache-2.0, ships an MCP server) — drop-in for primary research. **firecrawl** + **context7** (already MCP). **STORM** (Stanford, MIT) — only if "deep dive" tier needed; would need MCP wrapper. | — | Promotion-to-fact glue (research output → C5 generation arm → C3 fact). |

### Interface layer

| Cap | Adopt | Pattern | Custom |
|---|---|---|---|
| **C11 Perception** | GitHub Actions → Supabase events table (already in place per `event_driven_perception_v1`). | — | Noise-filter config update mechanism. |
| **C12 Communication** | Claude Code CLI (interactive). Claude Code Channels for Telegram. **Routines** for batched briefs. | — | Draft-vs-send approval gate. |

### Cross-cutting

| Cap | Adopt | Pattern | Custom |
|---|---|---|---|
| **C13 Budget / cost** | None (LiteLLM proxy filtered) | **LiteLLM declarative router config** ([github](https://github.com/BerriAI/litellm), MIT) — YAML routing rules format reference. **OTel GenAI semantic conventions** for cost-event column names. **Helicone proxy schema** as reference. | Soft/hard cap enforcement gate. Heartbeat probes per service. Daily reconciliation. API-key-vs-subscription routing protection. |
| **C14 Security** | Existing Sprint 1 stack (gitleaks, trufflehog, secret scanner hook, credential registry). **HIBP API** (free tier) for breach probes. | **Supabase RLS** standard pattern. **MCP OAuth 2.1 / RFC 8707** for MCP auth. | Principal-env-verification at session bootstrap. Compromise-runbook doc. |
| **C15 Self-improvement** | None — ladder is project-specific. | **Darwin-Gödel Machine** ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954)) — empirical-benchmark + sandbox safeguards (add alongside prior-version review). **biosecurity-agent lifecycle** ([bioRxiv 2025.09.17](https://www.biorxiv.org/content/10.1101/2025.09.17.676717v1.full.pdf)) — tier-graduation by formal alignment checks. | M0–M3 modification tier classifier. Misimprovement detection. Bootstrap protection. |
| **C16 Verification** | **Anthropic Code Review plugin** ([anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official), 18k stars, Apache-2.0 per plugin, source-available, fork-friendly) — already ships 5 parallel reviewers (CLAUDE.md compliance / shallow bugs / git-blame context / prior PR comments / code-comment compliance) + Haiku 0–100 confidence with 80 threshold + verifier second-pass. **The single biggest buy-win in the stack.** | Refute-or-Promote ([arXiv:2604.19049](https://arxiv.org/html/2604.19049v1)) — system-level FP/FN tracking pattern. | Diff-coherence reviewer (claimed-edits vs `git diff` — subagent fabrication detection, NOT in plugin). Cross-device integrity reviewer. Smoke-test reviewer. Different-provider plumbing for high-leverage class. |
| **C17 Observability** | None (Phoenix/Langfuse filtered) | **OTel GenAI semantic conventions** ([spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/)) for column names + trace_id propagation. **Honeycomb / Greptime "Observability 2.0"** wide-event substrate validates single-canonical-events. **Phoenix schema** as Postgres-only reference. | Canonical events table on Supabase + materialized projections per hot read path (cost-by-day, decisions-by-trace, last-run-by-actor). NOTIFY/LISTEN dispatcher. |

## Recommended stack (after filter)

**Adopt directly (drop-in, MCP/plugin/native):**

1. **Anthropic Code Review plugin (forked)** → C16 base. Fork [anthropics/claude-plugins-official `plugins/code-review/`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review), add diff-coherence + cross-device + smoke-test reviewers as additional parallel agents in the Markdown command file. **Estimated: 70-80% of C16 covered by fork.**
2. **GPT Researcher MCP** → C10 primary engine. Apache-2.0, MCP-native; config: Claude model, Supabase store for promoted facts. **Estimated: 60% of C10 covered.**
3. **sequential-thinking MCP** → C4 novel/high-stakes reasoning. Already installed; finally wire it.
4. **firecrawl + context7 MCP** → C10 scrape/docs tier (already adopted).
5. **LangGraph as one MCP server** → C4 plan-as-event + Postgres checkpointer to Supabase. Federation & Delegation carveout pre-approved. Wrap behind ONE MCP boundary (not used directly throughout codebase).
6. **Guardrails AI (library, called from hook)** → C6 *output* validation only. Not flow control.
7. **HIBP API** (free tier) → C14 breach probes. Scheduled task.

**Pattern-only (read schema, don't run their runtime):**

- **Zep/Graphiti bi-temporal columns** → C3 schema migration.
- **Cognee Postgres schema + synthesis pipeline** → C3/C5 reference.
- **Letta sleep-time agent pattern** → C5 implemented as Routine.
- **Reflexion verbal-feedback loop** → C5 outcome calibration.
- **LiteLLM YAML routing format** → C13 config shape.
- **OTel GenAI semantic conventions** → C17 column names + trace_id semantics.
- **Magentic-One ledger** → C8 handoff payload + C4 plan-state.
- **Honeycomb/Greptime Observability 2.0** → C17 substrate justification + materialized projections discipline.
- **Refute-or-Promote** → C16 FP/FN system-level tracking pattern.
- **DGM + biosecurity-agent lifecycle** → C15 tier-graduation safeguards.

**Stays fully custom (no usable source):**

- **C6 state-aware gate.** Confirmed by scout 2: all gating libraries are stateless. PreToolUse hook + Supabase per-session counter + git status probe + harness restrictions config — hand-rolled.
- **C8 worktree pre/post-dispatch wrappers.** HEAD-shift detector, diff-outside-scope, untracked-stash, TTL+heartbeat lock — no library does this for git worktrees.
- **C16 subagent fabrication detector** (claimed-edits vs `git diff`). Not in Anthropic plugin. Cheap to write, high leverage.
- **LLM-as-judge Brier-score calibration** with per-judge thresholds.
- **C13 enforcement gate** — soft/hard cap, heartbeat probes, daily reconciliation, API-key-vs-subscription routing protection.
- **C2 goals** as a small custom table + skill.

## Adoption-vs-build coverage estimate

Honest accounting (not the scout-1 70% number):

| Cap | % covered by adoption + pattern | Custom-build remaining |
|---|---|---|
| C1 Identity | 100% | — |
| C2 Goals | 0% | All |
| C3 Memory | ~30% (schema patterns + provenance hierarchy from refs) | Bi-temporal migration, classifier, recall ranking |
| C4 Reasoning | ~50% (LangGraph wrapped + sequential-thinking + Magentic ledger pattern) | Template runtime, replan, skills migration |
| C5 Reflection | ~25% (sleep-time pattern + Reflexion algorithm) | Stale-challenger, calibration, dispatcher |
| C6 Gating | ~5% (PreToolUse harness only) | Everything substantive |
| C7 Execution | 100% (native) | Tool-fallback edge cases |
| C8 Sub-orch | ~30% (Claude Code native worktree primitive) | All gates + lock + handoff |
| C9 Tool/env | 90% (MCP) | Principal verification |
| C10 Research | ~70% (GPT-R + STORM + firecrawl + context7) | Promotion glue |
| C11 Perception | ~80% (existing GHA → Supabase pipeline) | Noise filter |
| C12 Comm | ~70% (CC + Channels + Routines) | Draft-send gate |
| C13 Budget | ~10% (config format reference) | Enforcement, probes, reconciliation, routing protection |
| C14 Security | ~70% (Sprint 1 + HIBP + RLS + OAuth 2.1) | Principal verification, runbooks |
| C15 Self-improve | ~15% (DGM safeguards pattern) | Tiers, classifier, bootstrap protection |
| C16 Verification | ~75% (Anthropic plugin fork) | Fabrication detector, cross-device, smoke-test |
| C17 Observability | ~25% (OTel semconv + projection discipline) | Canonical table + projections + dispatcher |

**Aggregate: ~45% adoption + pattern coverage; ~55% custom-build.** Mainly because the substrate caps (C3/C6/C13/C17) and the project-specific safety caps (C8/C15) all skew custom under the "no Python services" filter.

## Principal decisions 2026-04-27

- **Q1 — fork Anthropic Code Review plugin: yes, first task next session.** Single biggest buy-win (~70-80% C16). Sprint shape: fork → adapt CLAUDE.md compliance reviewer → add 3 missing reviewers (diff-coherence subagent-fabrication, cross-device integrity, smoke-test) → wire into PR pipeline.
- **Q2 — LangGraph carveout may expand** beyond plan-only. Still wrapped behind ONE MCP boundary, not used directly throughout codebase. Additional state-machine use cases acceptable when they fit.
- **Q3 — `architecture_final` ("no Python services") rule stays.** Principal explicitly preferred custom-build over running Cognee/LiteLLM/Phoenix containers across 3 devices. The 55% custom-build remaining is acceptable.

## Action items (next-session priority order)

1. **Fork Anthropic Code Review plugin** (Q1 above) — open issue, sprint scope, fork.
2. **Wire GPT Researcher MCP** — replace ad-hoc research with structured MCP, promotion to facts goes via C5.
3. **Finally use sequential-thinking MCP** — installed and unused; integrate into C4 reasoning mode for novel problems.
4. **Substrate scout v2 in flight** — filtered re-scout (MCP / plugin / skill / hook / SQL-only / pure-library, no Python services) running 2026-04-27 to surface what scout v1 missed. Findings appended to this file when returns.
5. **Watch list** — Anthropic plugins/skills repos for new fits each month; LangGraph Postgres checkpointer maturity; community awesome-mcp-servers list for niche memory/observability/cost MCPs.

## Substrate scout v2 — concrete pieces (post-`architecture_final` filter)

Re-scout 2026-04-27 with hard filter: MCP / plugin / skill / hook / SQL-only / pure-library, no Python services. Returns specific files / packages / column names, not project recommendations.

### C3 — Memory subsystem

- **[`pg_bitemporal`](https://github.com/scalegenius/pg_bitemporal/tree/master/sql)** — PLpgSQL functions, copy `ll_create_bitemporal_table.sql`, `ll_bitemporal_insert.sql`, `ll_bitemporal_correction.sql`, `ll_bitemporal_inactivate.sql`, `ll_bitemporal_update.sql` into Supabase migration. Provides `effective` (valid time) + `asserted` (system time) periods + `*_key` PK + GIST exclusion constraint preventing overlapping periods per business key. **Design refinement**: adopt their column naming (`effective`, `asserted`) over our `valid_from/valid_to/superseded_by` — supersession collapses naturally into closing of `asserted` period.
- **[`pgvector-python` RRF example](https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py)** — pure-SQL hybrid search recipe (`RANK() OVER (... embedding <=> %s)` + `RANK() OVER (... ts_rank_cd)` joined via `1.0/(60+rank)`). Drop-in for Supabase.
- **`pgvector`** — `pip install pgvector` — SQLAlchemy/psycopg adapter so `mcp-memory/server.py` stops hand-formatting vector strings. Aligns with `memory_server_v2_improvements`.
- **MMR** — no library worth a dep; ~15 lines inline in `server.py`.

### C5 — Reflection / calibration (Q4 fix lives here)

- **[`crepes`](https://github.com/henrikbostrom/crepes)** — `pip install crepes` — Mondrian (class-conditional) Conformal Prediction via `class_cond=True`, sklearn-compatible, CPU-only, ~10× faster than MAPIE on small data. **Direct fix for Q4 (sparse-class calibration on N=1)** — partial-pooling across semantically-near classes via Mondrian.
- **[`MAPIE`](https://github.com/scikit-learn-contrib/MAPIE)** — `pip install mapie` — broader CP toolkit; 2026 roadmap explicitly adds CP for LLM-as-judge. Watch list.
- **[`netcal`](https://github.com/EFS-OpenSource/calibration-framework)** — `pip install netcal` — ECE/MCE/ACE/MMCE + reliability diagrams. Pair with sklearn `brier_score_loss`.
- **[`prometheus-eval`](https://github.com/prometheus-eval/prometheus-eval)** — `pip install prometheus-eval`; `from prometheus_eval.prompts import ABSOLUTE_PROMPT, RELATIVE_PROMPT` — drop-in absolute (1–5 rubric) and pairwise judge prompts. **Replace bespoke judge prompts in `/reflect`, `/verify`, C5 calibrator.**

### C13 — Budget / cost ledger (library-shaped, no proxy)

- **`litellm.cost_calculator` as library** — `pip install litellm`; `from litellm import completion_cost, cost_per_token`. Reads in-memory `litellm.model_cost` dict, fully offline, no proxy. [cost_calculator.py](https://github.com/BerriAI/litellm/blob/main/litellm/cost_calculator.py). Fits `architecture_final` cleanly.
- **[`tokonomics`](https://github.com/phil65/tokonomics)** — `pip install tokonomics` — thin wrapper over LiteLLM pricing JSON, auto-cached via `hishel`, refreshes 24h. Use if LiteLLM upgrade-pace becomes a problem.
- **`tiktoken`** — `pip install tiktoken` — OpenAI-compatible token counts pre-call.

### C17 — Observability

- **[OTel GenAI semantic-convention attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)** — copy verbatim as events-table column names: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.provider.name`, `gen_ai.agent.id`, `gen_ai.tool.name`, `gen_ai.conversation.id`, `gen_ai.operation.name`. **Note**: no canonical `gen_ai.cost` exists; invent `gen_ai.usage.cost_usd` consistent with prefix.
- **`traceloop-sdk` (OpenLLMetry) with custom exporter** — `pip install traceloop-sdk`; `Traceloop.init(exporter=YourSupabaseExporter())` — bypass their SaaS, route OTel spans to Supabase via ~30 LOC `SpanExporter` subclass. Library-only adoption pattern.
- **`pg_cron` materialized views** — already enabled on Supabase. `cron.schedule('0 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY events_cost_by_day_mv')`. Solves Q1's "materialized projections from day one" with zero new infra.
- **Trace ID propagation** — Python stdlib `contextvars.ContextVar[str]` + `uuid.uuid4().hex`. No library improves this.

### Claude Code ecosystem inventory (2026-04)

**[`anthropics/skills`](https://github.com/anthropics/skills/tree/main/skills) — 17 skills.** Relevant: `mcp-builder` (4-phase guide for new MCP servers — checklist when extending `mcp-memory/server.py`), `skill-creator`. **None** match memory / observability / cost / verification — those gaps stay ours to fill.

**[`anthropics/claude-plugins-official`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins) — 33 plugins.** Worth installing alongside `code-review`: **`pr-review-toolkit`**, **`session-report`**, **`hookify`**, **`claude-md-management`**, **`mcp-server-dev`**.

**Community lists worth scanning periodically:**
- [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
- [rohitg00/awesome-claude-code-toolkit](https://github.com/rohitg00/awesome-claude-code-toolkit) — 135 agents / 35 skills / 176+ plugins / 20 hooks indexed
- [TensorBlock observability MCP catalog](https://github.com/TensorBlock/awesome-mcp-servers/blob/main/docs/monitoring--observability.md) — `comet-opik` (LLM trace queries; check self-host), `digma`, `aws-cost-notifier-mcp`. Most SaaS-bound; verify before adopting.

**Anthropic engineering Jan–Apr 2026 references:**
- [Context engineering cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools) — memory / compaction / tool-clearing patterns
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — verifiable goals + sub-agent observability reference

### Highest-leverage adoption (sorted by smallest integration cost)

1. **OTel GenAI column names** — pure rename in events-table migration. Hours. No new dep.
2. **`litellm.cost_calculator` import** — `pip install litellm`; one import in `mcp-memory/server.py`. Hours. Replaces hand-rolled price tables.
3. **`prometheus-eval` ABSOLUTE/RELATIVE prompts** — wire into `/reflect`, `/verify`, C5 calibrator. Hours. Removes bespoke judge-prompt drift.
4. **Install Anthropic plugins** — `pr-review-toolkit`, `session-report`, `hookify`, `claude-md-management`, `mcp-server-dev` (alongside `code-review` fork). Hours.
5. **`pg_bitemporal` SQL migration** — copy 5 SQL files into Supabase migration; rebuild memories on top. Days. **Design-altering**: adopt `effective` + `asserted` column naming over our `valid_from/valid_to/superseded_by`.
6. **`crepes` for class-conditional CP** — direct fix for Q4 (rare-class calibration). Days. Wires into `memory_calibration_summary` MCP tool.
7. **`traceloop-sdk` + custom Supabase exporter** — ~30 LOC `SpanExporter` subclass. Days. Unifies instrumentation across `mcp-memory/server.py` + future caps.
8. **`pgvector-python` RRF SQL recipe** — replace any custom hybrid-search math with the canonical version. Hours.
9. **`mcp-builder` skill checklist** — apply when extending `mcp-memory/server.py` (next time).
10. **`pg_cron` materialized projection refreshes** — schedule once Supabase events table + projections land. Hours.

## Reference: alternative if `architecture_final` is later relaxed

Kept here for future reference only — current decision is to NOT relax the rule.

If the "no Python services" rule were relaxed (e.g., one shared cloud container running multiple services):
- **Cognee** becomes adoptable (~30% of C3+C5 covered).
- **LiteLLM** becomes adoptable (~50% of C13 covered).
- **Phoenix** becomes adoptable (~40% of C17 covered).
- Aggregate adoption rises to ~65-70%.

Trade-off principal explicitly weighed and rejected: cross-device sync for the Python services creates a new pain class outweighing build savings.
