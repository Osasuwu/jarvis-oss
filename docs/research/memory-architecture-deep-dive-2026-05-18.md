---
title: Memory architecture deep-dive — retrieval quality & scalability
date: 2026-05-18
status: working-doc
scope: jarvis memory subsystem (Supabase + pgvector + custom MCP)
sources:
  - LongMemEval (ICLR 2025), arxiv 2410.10813
  - Zep/Graphiti (arxiv 2501.13956)
  - Qwen3-Embedding tech report (arxiv 2506.05176)
  - Voyage-3-large announcement (Jan 2025)
  - "Memory in the Age of AI Agents" survey paper-list (Dec 2025)
  - Anthropic Auto Dream docs (May 2026 preview)
  - Supermemory research; Mem0 state-of-2026; Hindsight "case against external vector DBs"
  - SSGM framework (arxiv 2603.11768); LiCoMemory decay
---

## Executive summary

1. For ~hundreds–10k records, **pgvector + BM25 + RRF + cross-encoder rerank** beats any single-method retrieval. Jarvis already has the first two; missing RRF fusion and rerank.
2. **Query rewriting / HyDE is high-variance** — wins on conversational queries, loses on factual lookups with hallucinated terms. Use selectively, not as default.
3. **Skill-name keyword sensitivity** in `memory_recall` is a known weak spot — the literature calls this "lexical anchor mismatch", standard fix is BM25 leg of hybrid (Jarvis has this) + skill-name field boost + query expansion via synonym dictionary.
4. **Qwen3-Embedding-8B** tops MTEB multilingual (70.58) and beats voyage-3 + text-embedding-3-large on most retrieval tasks. **MRL truncation to 1024d** keeps 96–99% of quality at ~25% storage cost. Solid migration target.
5. **Graph memory (Zep/Graphiti, Cognee, Letta)** earns its complexity only for multi-hop entity reasoning, bi-temporal facts, or 100k+ records. Not yet justified for jarvis. **Steal Graphiti's bi-temporal pattern** (event_time + ingest_time + valid_from/to) without adopting the graph engine.
6. **Anthropic Auto Dream** is replicable as a scheduled skill: 4-phase (orient → gather signal → consolidate → prune+index), triggers on 24h + 5-session delta. Open-source `dream-skill` exists. Jarvis should adopt the pattern; the missing pillar is **periodic consolidation**, not better retrieval.
7. **`memories_used` as graph edge, not array** — the data is already shaped like edges; promoting to a `decision_memory_link` table unlocks queries like "which decisions cite this feedback" and "which memories drove the most outcomes".
8. **Stale memory detection** at scale: LiCoMemory-style decay function in ranking + adversarial probing (periodic counterexample challenge) + supersession chains beat manual linting.
9. **Don't migrate everything at once** — embedding migration alone is a 1-week project (re-embed corpus, dual-write, validate quality, cut over). Stage it after the consolidation skill ships, otherwise you can't measure quality drift.
10. **MemoryAgentBench / LongMemEval-S** are the right benchmarks to copy a handful of fixtures from for jarvis-specific eval; don't try to clone the full harness.

---

## 1. RAG / retrieval patterns for agent memory

### What's SOTA in 2025–2026

**The composite retrieval stack has won.** No single-method retrieval is competitive anymore. The 2025-end RAGFlow review and multiple ParadeDB / Vectorize posts converge on:

```
candidates(BM25 top-N) ∥ candidates(vector top-N)
    → RRF fusion (k≈60)
    → cross-encoder rerank → top-K
```

RRF beats weighted-sum fusion because it doesn't need score normalization — each leg ranks independently, ranks are added with `1/(rank+k)`. Cross-encoder rerank gives the biggest single quality jump (MRR@3 0.43 → 0.60 in one benchmark). [RAGFlow 2025 review, ParadeDB hybrid manual]

### Which advanced tricks apply to a personal agent memory

**Personal agent memory is small (10²–10⁴ records), high-relevance, high-churn, written by one source.** This changes the cost/benefit:

| Technique | Verdict for jarvis | Why |
|---|---|---|
| **Hybrid BM25 + vector + RRF** | YES — adopt | Highest ROI. Already have ingredients. |
| **Cross-encoder rerank** | YES — adopt (BGE-reranker-base-v2 or ms-marco-MiniLM-L-6-v2 local) | 92ms / 55ms local latency, ~50–100ms acceptable; biggest quality jump. |
| **HyDE (Hypothetical Doc Embedding)** | SELECTIVE — only for vague queries | Adds latency (one LLM call) AND hallucinates terms on factual lookups (financial-QA studies show degradation). Useful when user query is "что мы решили про X" — generate hypothetical answer first. |
| **MMR diversification** | NO — skip | ARAGOG and multiple benchmarks: no notable advantage over baseline. Jarvis recall is small-K (≤15), diversity bonus negligible. |
| **Query rewriting (LLM-based)** | YES — adopt for vague queries | Forward-looking rewriting cheap (Haiku). The "skill name must appear in query" pain point in CLAUDE.md is exactly this — solve at query-expansion layer. |
| **TreeRAG / hierarchical indexing** | NO — overkill | Designed for chunked documents. Memory records are already atomic. |
| **Tree-based summary navigation** | NO at current scale | Worth revisiting at 10k+ records. |

### Key citations
- Liu et al., "Memory in the Age of AI Agents: A Survey" (Dec 2025): formalizes Formation → Evolution → Retrieval lifecycle; agent memory is write-heavy unlike RAG.
- LongMemEval (ICLR 2025): five competencies — extraction, multi-session, temporal, knowledge update, abstention. State-of-art commercial systems only 30–70% on it.
- VectorChord, ParadeDB: pgvector-native BM25 + RRF recipes for sub-10k corpora.

---

## 2. Graph memory architectures

### What problem do they solve that pgvector+BM25 doesn't

Pgvector + BM25 fails specifically on:
1. **Multi-hop entity reasoning** — "what did we decide about the memory protocol *after* the embedding swap discussion?" needs to traverse decision→supersedes→decision edges, not similarity.
2. **Bi-temporal queries** — "what did we believe in March about X, regardless of current state?" needs explicit valid-from / valid-to.
3. **Entity-centric reasoning** — "show me all decisions involving Supabase" — entity nodes with typed edges beat keyword matching.

### Field comparison (2026 state)

| System | Approach | LongMemEval score | Solo-dev fit |
|---|---|---|---|
| **Mem0** | Vector + fact extraction service wrapping LLM calls | 67% (older); higher in newer | YES — closest to current jarvis shape |
| **Zep (Graphiti)** | Bi-temporal knowledge graph w/ explicit valid intervals | 71.2% (GPT-4o); 94.8% on DMR | Patterns yes, full system no — needs Neo4j |
| **Letta (MemGPT)** | OS-inspired tiered: core (RAM) / archival / recall | n/a; depends on backing store | NO — replaces agent runtime; conflicts with Claude Code |
| **Cognee** | Vector + graph for unstructured doc ingestion | n/a | NO — wrong workload (docs, not turns) |
| **Supermemory** | Vector + semantic disambig + relational versioning | 81.6% LongMemEval-S | Patterns yes (Updates/Extends/Derives edges) |
| **OMEGA / Mastra OM** | Observational memory + multi-strategy retrieval | 95.4% / 94.87% | Likely overkill |
| **Anthropic Auto Dream** | Consolidation-driven; replaces stale w/ canonical | Not benchmarked publicly | Pattern adoption YES (preview-only access) |

### When is graph memory worth it for a solo agent

Threshold is **entity-density × multi-hop frequency**, not record count:

- Record count alone: graph isn't worth it until ~10k+ vector-only fails on too many queries
- Entity density: if >50% of records reference the same ~20 entities (skills, files, decisions, repos), graph wins early
- Multi-hop queries: if a meaningful fraction of recalls need 2+ hops ("decisions about features that touch shared infra"), graph pays for itself

**Jarvis today:** entity density is high (skills, repos, decisions reference each other), but multi-hop is rare in current usage. Verdict: **adopt graph-like edges in pgvector** (link tables) without adopting a graph DB. Specifically:
- `decision_memory_link(decision_uuid, memory_uuid, role)` — promotes `memories_used[]` to a proper edge.
- `memory_supersedes(new_uuid, old_uuid, reason, ts)` — explicit supersession.
- `memory_entity(memory_uuid, entity_type, entity_id)` — entity inverted index.

### Anthropic Auto Dream — wait or steal?

**Steal.** The pattern is well-documented (claudefa.st reverse-engineer post; `grandamenium/dream-skill` reference implementation). Preview access is gated. Replicate the 4-phase pattern as a scheduled jarvis skill:

1. **Orient** — list memory catalog by tag (already in SessionStart hook).
2. **Gather signal** — grep recent sessions for `record_decision` calls, user corrections, repeated topics.
3. **Consolidate** — merge near-duplicates (cosine >0.92), resolve contradictions via supersession, convert relative dates ("last week") to absolute.
4. **Prune + index** — drop records with confidence <threshold AND age >60d AND no retrieval hits.

Trigger: 7d cadence (smaller than Anthropic's because jarvis writes faster per session) OR after N=50 new records, whichever first.

---

## 3. Memory decay & consolidation

### Patterns from literature

**TTL-based** (LiCoMemory, SSGM):
- Infinite TTL for immutable facts (e.g., "user prefers terse caveman mode") — never decay.
- 30–90d TTL for transient context ("working on milestone X this week").
- **Boost-on-use**: every retrieval that uses a memory resets/extends its decay timer. Mimics biological spacing effect.

**Importance-weighted retention**:
- Score = relevance × recency × usage_count × `reversibility_weight`. Irreversible decisions weighted 3x; reversible 1x.
- Records below threshold drop into "cold" tier (still queryable, demoted in ranking).

**Periodic consolidation** (Anthropic Auto Dream, Supermemory, mem0 OpenMemory):
- Background process merges near-duplicates, resolves contradictions, generates summary records for dense clusters.
- Risk: **semantic drift via lossy compression** (SSGM paper). Mitigations: keep originals + summary; cap consolidation depth to 2 levels; track provenance chain.

**Explicit supersession chains** (Cloudflare Agent Memory, Supermemory):
- Three edge types: `Updates` (contradicts → version history), `Extends` (adds without conflict), `Derives` (inferred from N parents).
- Query "current truth about X" = walk Updates edges to leaf.

### What the literature recommends at 1k+ scale

Best paper guidance — **all four in combination**:
1. TTL on the cold-write side (decay function in retrieval ranking, not deletion).
2. Importance-weighted retention (not just recency).
3. Periodic consolidation **with provenance** (keep originals).
4. Supersession edges for explicit contradiction handling.

**Avoid** (SSGM warnings):
- Pure recency decay → loses load-bearing rare facts.
- Iterative summarization without provenance → semantic drift, unrecoverable.
- Hard delete based on TTL alone → false negatives.

### Concrete proposal for jarvis

- Add `last_retrieved_at`, `retrieval_count`, `boost_score` to memory schema.
- Implement decay function in `memory_recall` ranking: `score *= exp(-age_days / half_life)` where `half_life = 30 (reversible) / 180 (decision) / ∞ (always_load)`.
- Quarterly consolidation skill — merge cosine >0.92 within same type+project, write `derived_from=[uuids]`.
- Supersession edge table (see §2).

---

## 4. Decision-log query patterns

### What query patterns matter

From scanning Cognee, Zep, ADR-as-event-sourcing posts, and the existing jarvis schema:

| Pattern | Why it matters | Current jarvis support |
|---|---|---|
| **By topic + skill** (current) | Recall during decision-making | ✓ (memory_recall with query) |
| **By reversibility** | Cautioning on irreversible-adjacent work | ✗ (would need WHERE filter) |
| **By memories_used → memory** | "What decisions cite this constraint?" | ✗ (array, not edge) |
| **By outcome → decision** | "Which decisions led to bad outcomes?" | partial (outcome.scope is text) |
| **By recency window** | "Last week's decisions" | ✓ (timestamp filter) |
| **By chain** | "Walk supersession chain to current truth" | ✗ |
| **By actor pattern** | `/self-improve` grep for `:post-hoc` suffix | ✓ string match |

### Better schemas

**Event sourcing for ADRs** (Shing 2026, archyl.com guide): treat each `record_decision` call as an event in an immutable log. Current state of architecture = replay all decisions. Jarvis already does this de-facto — every record is timestamped immutable. Missing: a **snapshot/projection layer** that materializes "current architectural state per topic" on read.

**CRDT** (TheOptimizationKing 2025): only relevant if multiple agents write concurrently. Jarvis is single-principal — overkill. Skip.

**`memories_used` as graph edge** — **yes, do this**:
- Today: `decision.memories_used = [uuid, uuid, uuid]` (array column)
- Better: `decision_memory_link(decision_uuid, memory_uuid, role enum('basis','counter','context'), created_at)` table.
- Wins: indexable both directions; can attach role (was this memory the basis, a counterexample, or just context?); enables `SELECT count(*) FROM decision_memory_link WHERE memory_uuid=X` to find load-bearing memories.
- Migration: keep array column too during transition, populate link table from array via trigger, deprecate array after a month.

---

## 5. Embedding quality benchmarks

### MTEB and retrieval-specific scores (2025 data)

| Model | MTEB multi | Notes | Cost |
|---|---|---|---|
| **Qwen3-Embedding-8B** | **70.58** (#1, Jun 2025) | 4096d native; MRL → 1024/512/256; 32k ctx; 100+ langs incl. code | Local |
| Qwen3-Embedding-4B | ~68 | 2560d; MRL | Local |
| Qwen3-Embedding-0.6B | ~64 | 1024d; tiny, fast | Local |
| voyage-3-large | beats OAI-v3-large by 9.7% NDCG@10 avg | 1024d default; int8/binary; code-strong | $0.18/M tok |
| voyage-3 (base) | mid-tier | 1024d | $0.06/M tok |
| voyage-context-3 | adds doc-global context to chunks | — | $0.18/M tok |
| OpenAI text-embedding-3-large | ~64.6 | now ~13th overall | $0.13/M tok |
| BGE-M3 | mid-tier (~64) | strong multilingual; HF free | Local |
| Gemini Embedding 2 | competitive | — | API |

### Evidence for agent-memory class (not just MTEB averages)

MTEB averages overweight long-document IR. Agent memory is short-form (≤500 tok records), often **mixed code+prose+terminology**. The benchmarks that matter:
- **Code retrieval** (CodeSearchNet, CoIR) — Qwen3-8B and voyage-3-large lead; OpenAI lags.
- **Instruction-aware retrieval** — Qwen3 explicitly supports per-task instructions; voyage too.
- **Multilingual** (jarvis user code-switches RU/EN) — Qwen3 and BGE-M3 strongest open-source; voyage-3-large strong API.

### Latency budget — local on RTX 5080 16GB

Qwen3-Embedding-8B at FP8 fits in ~10GB VRAM. Numbers extrapolated from RTX 5090 vllm benchmarks (no direct 5080 numbers in literature):
- Single query encode: ~30–80ms (32–256 input tokens).
- Batch of 8: ~150–250ms.
- Sustained throughput: ~50–100 embed/sec at batch=8 in FP8.

**Caveats**:
- No published 5080-specific numbers. Worth running once before committing.
- FP8 KV-cache doesn't help embeddings (no KV cache in encoder-only forward pass).
- MRL truncation to 1024d saves Supabase storage; do it at write time, not query.
- Bottleneck for jarvis won't be embedding latency — it'll be the Supabase round-trip.

**Recommendation**: stage to Qwen3-Embedding-8B with MRL→1024d. Saves ~$60/year vs VoyageAI, gives top MTEB score, locks out one external dependency, and the 5080 sits idle most of the day. **But ship consolidation skill first** — embedding-swap quality regressions are invisible without a baseline benchmark.

---

## 6. Stale memory detection

### How other systems handle it

**Mem0** (state-of-2026): explicit production gap #3 — "high-relevance memories become confidently incorrect when user circumstances change." No clean solution yet at industry scale; current best practice is short TTL on volatile categories + user-prompted reconfirmation.

**Zep/Graphiti**: bi-temporal edges with `valid_from / valid_to`. New fact contradicts old → old gets `valid_to=now`; not deleted. Query at time T returns only edges where `valid_from ≤ T ≤ valid_to`.

**SSGM (Stability and Safety Governed Memory)**: triple check before consolidation — consistency verification, temporal decay model, dynamic access control.

**LiCoMemory**: decay function in ranking (not deletion), preserves rare-but-important facts above decay floor via importance weighting.

**Supermemory**: "Updates/Extends/Derives" edges instead of decay — store the version chain explicitly, current truth = leaf.

**Future-systems literature recommends** (per arxiv 2602/2603/2604 papers):
- **Uncertainty quantification** — decay confidence over time without re-validation.
- **Adversarial probing** — periodically challenge stored beliefs with counterexamples (LLM generates "is this still true?" probe).
- **Expiration policies** — retire unvalidated reflections after a set period.

### For "this memory references a file/skill that no longer exists" specifically

The CLAUDE.md already encodes the right pattern ("dead references → ignore + note for /reflect, don't ask the user about every dead reference"). To scale:

1. **Automated linting** — quarterly scheduled task that:
   - Parses memory bodies for file paths, skill names, issue refs.
   - Tests existence (file: `Test-Path`; skill: file under `~/.claude/skills/`; issue: `gh issue view`).
   - Tags memories with `lint:dead_ref:<type>:<token>` and decay-boosts them.
2. **Show-and-continue inline disclosure** (already in CLAUDE.md) — surfaces staleness in real time.
3. **Embedding drift detection** — when re-embedding the corpus (e.g., model swap), compute cosine(old, new) per record. Records with cosine <0.7 deserve human review — usually means the text references concepts the new model interprets differently.
4. **Adversarial probing skill** — monthly, sample 5 high-importance memories, ask the model "given current repo state, would you still write this?" Tag stale.

---

## PROPOSALS

| # | Proposal | Source | Priority hint | Notes |
|---|---|---|---|---|
| M1 | **Add RRF fusion + cross-encoder rerank to `memory_recall`** (BGE-reranker-base-v2 local, ~92ms) | ParadeDB hybrid manual; multiple 2026 benchmarks | HIGH | Biggest single quality jump (MRR@3 0.43→0.60 in benchmarks). Reuse existing BM25 + vector legs. |
| M2 | **Build consolidation skill `/dream`** (4-phase: orient → gather → consolidate → prune; 7d cadence) | Anthropic Auto Dream; `grandamenium/dream-skill` | HIGH | Missing pillar. Without this, records grow without bound. Keep originals; write `derived_from`. |
| M3 | **Promote `memories_used[]` array → `decision_memory_link` edge table** with `role` enum | Zep, Cognee, ADR-as-event-sourcing | HIGH | Enables back-queries ("what cites this memory"); cheap migration with trigger. |
| M4 | **Add bi-temporal columns** `event_time` (when fact held), `valid_from`/`valid_to` (when believed) | Zep/Graphiti bi-temporal model | MED | Don't adopt full graph; steal the timestamp pattern. Enables "what did we believe last month" queries. |
| M5 | **Add decay function to ranking** `score *= exp(-age_days / half_life)` per-type half-lives | LiCoMemory; SSGM | MED | Soft decay, not deletion. Half-life per type: reversible=30d, decision=180d, always_load=∞. |
| M6 | **Migrate VoyageAI → Qwen3-Embedding-8B (MRL→1024d) on RTX 5080** | Qwen3 tech report; MTEB Jun 2025 #1 | MED | Saves ~$60/year; #1 MTEB; gated on benchmark fixture set (do M7 first). |
| M7 | **Build a 50-fixture jarvis-specific retrieval eval** (sample real recall queries, gold UUIDs) | LongMemEval, MemoryAgentBench design | HIGH | Gates M1 and M6 — can't measure quality without a fixture set. Cheap to build, 1-day project. |
| M8 | **Add `dead_ref` linting skill** — parse paths/skills/issues, mark stale, decay-boost | CLAUDE.md current rule, scaled | MED | Quarterly cron. Don't auto-delete — tag and demote. |
| M9 | **Promote `supersedes` edge** — when a `record_decision` overrides a prior one, write explicit edge | Cloudflare Agent Memory; Supermemory | LOW | Useful but rare; ship after M3 lands. |
| M10 | **Query expansion at `memory_recall` entry**: expand skill-name + synonym dictionary before search | LLM4IR-Survey query-reformulation | MED | Addresses the documented "keyword-sensitive" pain point in CLAUDE.md. Cheap (Haiku call or static dict). |

---

## Don't-do list

1. **Don't adopt Letta/MemGPT** — it's an agent runtime replacement, conflicts with Claude Code skills/hooks architecture. Steal patterns only.
2. **Don't run a graph DB (Neo4j, Kuzu) yet** — workload doesn't justify operational complexity. Edge tables in pgvector cover 90% of the wins. Re-evaluate at 10k+ records OR when multi-hop queries become frequent.
3. **Don't enable HyDE globally** — financial-QA benchmarks show it degrades factual lookups by hallucinating plausible-but-wrong terms. Use only on vague conversational queries, gated by a query classifier.
4. **Don't iteratively summarize without provenance** — SSGM documents "semantic drift via iterative summarization" as a primary failure mode. Always keep originals + write `derived_from`; cap consolidation depth at 2 levels.

---

## Sources

- [RAGFlow — RAG to Context, 2025 year-end review](https://ragflow.io/blog/rag-review-2025-from-rag-to-context)
- [LongMemEval (ICLR 2025), arxiv:2410.10813](https://arxiv.org/abs/2410.10813)
- [Memory in the Age of AI Agents — Paper List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory, arxiv:2501.13956](https://arxiv.org/abs/2501.13956)
- [Graphiti (Zep open-source)](https://github.com/getzep/graphiti)
- [Qwen3-Embedding blog (Qwen team)](https://qwenlm.github.io/blog/qwen3-embedding/)
- [Qwen3-Embedding HF card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)
- [Voyage-3-large announcement, Jan 2025](https://blog.voyageai.com/2025/01/07/voyage-3-large/)
- [Voyage-context-3 announcement, Jul 2025](https://blog.voyageai.com/2025/07/23/voyage-context-3/)
- [Anthropic Dreams (Claude API Docs)](https://platform.claude.com/docs/en/managed-agents/dreams)
- [Anthropic Auto Dream reverse-engineering (claudefa.st)](https://claudefa.st/blog/guide/mechanics/auto-dream)
- [dream-skill OSS reference impl](https://github.com/grandamenium/dream-skill)
- [Supermemory research (LongMemEval-S 81.6%)](https://supermemory.ai/research/)
- [Mem0 — State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Hindsight — The Case Against External Vector DBs for Agent Memory](https://hindsight.vectorize.io/blog/2026/05/12/case-against-external-vector-dbs-agent-memory)
- [Letta forum — agent memory comparison](https://forum.letta.com/t/agent-memory-letta-vs-mem0-vs-zep-vs-cognee/88)
- [SSGM Framework, arxiv:2603.11768](https://arxiv.org/html/2603.11768v1)
- [Hindsight — Open-Source MCP Memory Server](https://hindsight.vectorize.io/blog/2026/03/04/mcp-agent-memory)
- [ParadeDB — Hybrid Search in PostgreSQL Missing Manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [VectorChord — Hybrid search w/ Postgres native BM25](https://docs.vectorchord.ai/vectorchord/use-case/hybrid-search.html)
- [Cross-encoder reranker benchmark (top 8)](https://medium.com/@bhagyarana80/top-8-rerankers-quality-vs-cost-4e9e63b73de8)
- [Reranker benchmark 2026 (BSWEN)](https://docs.bswen.com/blog/2026-02-25-best-reranker-models/)
- [Matryoshka embeddings — HF blog](https://huggingface.co/blog/matryoshka)
- [Sentence Transformers — Matryoshka docs](https://sbert.net/examples/sentence_transformer/training/matryoshka/README.html)
- [ADR as Event Sourcing (Shing 2026)](https://shinglyu.com/architecture/2026/02/17/adr-as-event-sourcing.html)
- [CRDTs vs Event Sourcing (Medium, Nov 2025)](https://medium.com/@optimzationking2/crdts-vs-event-sourcing-the-architecture-war-that-will-define-the-next-10-years-ae8245cd2ac9)
- [Cloudflare — Agent Memory](https://blog.cloudflare.com/introducing-agent-memory/)
- [Portable Agent Memory (provenance), arxiv:2605.11032](https://arxiv.org/html/2605.11032v1)
- [MarkTechPost — Comparing Memory Systems for LLM Agents (Nov 2025)](https://www.marktechpost.com/2025/11/10/comparing-memory-systems-for-llm-agents-vector-graph-and-event-logs/)
- [MachineLearningMastery — Vector DBs vs Graph RAG for Agent Memory](https://machinelearningmastery.com/vector-databases-vs-graph-rag-for-agent-memory-when-to-use-which/)
- [Hindsight is 20/20 — Agent Memory Retain/Recall/Reflect, arxiv:2512.12818](https://arxiv.org/html/2512.12818v1)
- [Skill Retrieval Augmentation for Agentic AI, arxiv:2604.24594](https://arxiv.org/html/2604.24594v1)
