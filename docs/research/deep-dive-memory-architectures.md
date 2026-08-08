# Deep dive: memory architectures for Jarvis Pillar 4

Date: 2026-05-06
Author: research subagent (Opus 4.7, 1M ctx)
Companion to: [`agent-dev-practices-sweep-2026-05-06.md`](agent-dev-practices-sweep-2026-05-06.md) §7
Scope: concrete next-iteration proposal for memory. Does **not** propose
rewriting the Supabase store — proposes parallel structures and extensions
that ride on top.

> **Sourcing note (non-cosmetic).** The brief listed eight URLs to fetch
> (mem0 blog, digitalapplied, Atlan, GitHub paper-list, four arXiv/OpenReview
> papers). In this run, both `WebFetch` and the firecrawl MCP scraper were
> denied permission, and `WebSearch` was also denied. The doc therefore relies
> on:
>
> 1. The pre-existing wide sweep (`agent-dev-practices-sweep-2026-05-06.md`
>    §7) — which itself fetched these sources a week ago.
> 2. Training-cutoff knowledge (Jan 2026) of mem0, Letta/MemGPT, AriGraph,
>    A-MEM, Zep/Graphiti, SYNAPSE-style spreading-activation graphs, and
>    Postgres / pgvector / AGE.
> 3. Direct read of `mcp-memory/schema.sql` and `mcp-memory/tools_schema.py`
>    in HEAD on `feat/510-rename-analyze-comms-to-reflect`.
>
> Where a claim depends on a source the sweep didn't already vet, it is
> marked `[unverified]`. Confidence ratings reflect this.

---

## 1. Three-axis taxonomy and where Jarvis lives

Production-grade agent memory in 2026 is best read on three orthogonal
axes. They're often conflated; keeping them separate makes the gap analysis
honest.

### Axis A — Cognitive role (what kind of fact)

| Type | Stores | Example for Jarvis | Recall trigger |
|---|---|---|---|
| **Episodic** | "What happened at time T" — raw events with timestamp + actor | "On 2026-04-12 user rejected approach X for #399 because Y" | similar-situation lookup |
| **Semantic** | Distilled, time-invariant facts | "User prefers vertical slices over horizontal" | topic query |
| **Procedural** | "How to do X" — runbooks, action sequences, parameterised playbooks | "How to close a sprint: milestone close → release → retrospective" | task-name lookup |

### Axis B — Storage substrate (how the fact is indexed)

| Substrate | Best at | Weak at |
|---|---|---|
| **Vector (pgvector/HNSW)** | Fuzzy semantic recall, paraphrase tolerance | Multi-hop traversal, exact relationships |
| **Graph (KG / property graph)** | Entity-relationship traversal, "show me everything connected to X", temporal slicing | Free-form natural-language queries |
| **Relational / FTS** | Exact filters (project=jarvis, type=decision), keyword fallback | Concept-level recall |

### Axis C — Lifetime / scope (how long it lives)

| Tier | Lifetime | Typical home |
|---|---|---|
| **Working / scratchpad** | Within one tool turn | Just the message; no store |
| **Short-term episodic buffer** | Within one session / agent loop | In-memory ring buffer or session-scoped table |
| **Long-term store** | Across sessions, devices | Supabase |

### Where Jarvis sits today

| Axis | Cell | Status |
|---|---|---|
| A: Episodic | `episodes` table (`schema.sql:854`) — raw, unprocessed, partial-indexed by `processed_at IS NULL` | **Present but underused.** Backlog scan exists; no live extractor pipeline that promotes episodes → semantic memories on a regular cadence. |
| A: Semantic | `memories` table with `type ∈ {user, project, decision, feedback, reference}` | **Strong.** This is the hot path. |
| A: Procedural | None as a first-class type. Procedures live as Markdown skills (`.claude/skills/*/SKILL.md`) and ad-hoc `decision` rows. | **Gap.** Procedures are file-system artefacts — discoverable to the agent only via skill auto-discovery, not via `memory_recall`. |
| B: Vector | Voyage 512-dim embeddings on `memories.embedding`, HNSW. | **Strong.** |
| B: Graph | `memory_links` (source/target/link_type ∈ {related, supersedes, consolidates}) + `memory_graph` MCP tool with overview/links/clusters modes. | **Skeleton.** Graph exists but is shallow: 3 link types, no entity nodes (nodes = memory rows themselves), no traversal queries beyond 1–2 hops. |
| B: Relational/FTS | Generated `fts tsvector` GIN index + FTS path in `recall()`. | **Strong.** |
| C: Working | None — fits in the message window. | **Correct, intentional.** |
| C: Short-term episodic | The `episodes` table doubles as both buffer and audit log. There is no session-scoped roll-up that's consulted *during* a session. | **Gap.** Within a single long session Jarvis cannot ask "what did I just do 30 turns ago" without re-reading the transcript. SessionStart hook helps cold start, but mid-session loss-of-thread isn't covered. |
| C: Long-term | `memories` + Supabase. | **Strong.** |

**Net read:** Jarvis is a strong vector-semantic store with a stub graph and
a stub episodic pipeline. The gaps are (1) procedural memory as a queryable
type, (2) episodic→semantic promotion that actually runs, (3) entity-level
graph (people/projects/decisions/outcomes as nodes, not just memory rows).

---

## 2. mem0 v1 procedural memory

> Confidence: 3/5 — unverified against the live mem0 blog post; based on
> training-cutoff knowledge + sweep §7.1 + sweep §3.6. Worth the owner
> spending 10 min on the actual blog before committing.

### What it stores

Procedural memory in mem0 v1 is the third first-class memory type alongside
episodic and semantic. It stores **how-to knowledge**: action sequences,
parameterised playbooks, multi-step recipes. Examples used in mem0 docs:
"how to onboard a new user", "how to escalate a P1 incident", "how to
transfer money in app X".

Critically, procedural is **not** the same as a skill file. A procedural
memory is recall-able by *task description* through the same `memory.search`
API as semantic — the agent doesn't need to know the runbook exists to find
it.

### API surface (mem0 v1, approximate)

```python
mem0.add(messages=[...], user_id="...", memory_type="procedural")
mem0.search(query="how do I close a sprint", memory_type="procedural")
```

The classifier inside `mem0.add` decides whether incoming content is
episodic / semantic / procedural based on text shape (imperative
"how to ..." → procedural, declarative "I prefer ..." → semantic, time-stamped
"on date X happened Y" → episodic).

### Would it slot into Jarvis's existing tools?

**Yes — as a new value of the `type` enum, not a new tool.** Today
`VALID_TYPES = ('user', 'project', 'decision', 'feedback', 'reference')`
(`server.py:98`). Adding `'procedural'` keeps the API surface identical:
`memory_store(type='procedural', ...)`, `memory_recall(types=['procedural'])`.

The only real work is:

1. Add `'procedural'` to the CHECK constraint and `VALID_TYPES`.
2. Write a one-line classifier hint into the `/grill-me` and skill
   conventions: "if you found yourself running the same 5-step sequence
   twice this week, store it as a procedural memory."
3. Optionally, a `recall()` re-ranking bump for procedurals when the query
   matches imperative shape ("how do I ...", "steps to ...").

**Don't** build a separate `procedural_memories` table. The discriminator is
already there.

---

## 3. Graph memory: when does it earn its keep?

### The honest answer

For a single-principal personal agent with O(10³) memories, **vector +
relational filters cover ~85% of useful queries**. Graph earns its keep on
the remaining 15%, which happen to be the queries that *most embarrass an
agent that doesn't have it.*

### Specific queries vector loses on

| Query | Why vector fails | Graph wins because |
|---|---|---|
| "Every decision tied to issue #399, including superseded ones, in chronological order" | Embedding similarity ≠ entity equality. `#399` lexically appears in many memories, semantically in many more. | Edge `decision -[ABOUT]-> issue_399` is exact; ORDER BY edge.created_at trivial. |
| "Show all outcomes downstream of decision D" | Vector returns *similar* outcomes, not *caused-by* outcomes. | 1–N hop traversal `decision -[LED_TO]-> outcome`. |
| "Which user preferences contradict each other?" | Contradiction is a structural relation, not a semantic one. | Edge `pref -[CONTRADICTS]-> pref` is explicit. (Today: nothing models this.) |
| "All projects that touch the Supabase schema" | Vector finds *talk about* Supabase; misses projects whose code touches it without saying so. | Edge `project -[DEPENDS_ON]-> shared_resource:supabase_schema`. |
| "People mentioned in decisions about redrobot in last 30 days" | Multi-filter + entity join — possible in SQL but not in vector. | Three-way join across nodes + temporal filter on edge. |

Notice all of these are *recall + reasoning* patterns Jarvis already pays
for through awkward workarounds (re-reading skills, trusting memory.tags,
asking the user).

### Lightweight graph options for Jarvis

| Option | Cost | Fit | Verdict |
|---|---|---|---|
| **Postgres recursive CTE on `memory_links`** | Zero — it's already there. | Good for chains (supersedes, consolidates) and small radii (≤3 hops). | **Default.** Already in use (`find_chain_head`, link expansion). |
| **Postgres + dedicated `entities` + `entity_edges` tables** | Low — two tables, ~150 lines of SQL. Stays in existing Supabase project. | Good for first-class entities (people, projects, issues, outcomes). Recursive CTEs handle traversal. | **Recommended (see §6).** |
| **Apache AGE extension on Postgres** | Medium — Supabase free tier doesn't ship AGE; would need self-host or paid tier. Cypher in psql is brittle. | Real graph queries, but extension support on Supabase is not first-class. | **Skip.** Premature for current scale. |
| **External Neo4j / Memgraph** | High — separate service, separate auth, separate backup, ~$50/mo Aura free-tier or self-host. | Real graph with rich query language. | **Skip.** Violates the $20/mo external budget; doubles operational surface. |
| **Zep / Graphiti as a service** | Medium — Zep has a free tier, but it's a third store, third auth, vendor lock. | Production-grade temporal KG, designed for agents. | **Skip for now**, revisit if §6 step 3 outgrows Postgres. |

### Cost / complexity trade-off

For a solo dev with one Supabase project: any answer that involves a second
data store fails the "operational budget" test before it fails the dollar
test. The graph extension that wins is **the one that costs zero new
services**. That points squarely at option 2 (entities + edges in the same
Postgres).

---

## 4. Episodic buffer: short-term coherence

### Two distinct things often called "episodic memory"

1. **Short-term coherence buffer** — within a single agent loop, "what did
   I do 30 turns ago that's now off-screen?" Pattern: ring buffer or
   summarised scratchpad. **Letta/MemGPT** popularised this with the
   `recall_storage` + `archival_storage` split, where the "main context"
   has a fixed token budget and an explicit promote/demote API for the
   agent to manage its own working set.
2. **Long-term episodic store** — across sessions, "tell me about that
   conversation last Tuesday." Pattern: timestamped event log with
   semantic + temporal recall. **mem0**, **Zep**, **AriGraph** all fall
   here.

Jarvis today: (1) is **absent** — long sessions rely on the model's own
context window plus SessionStart re-load. (2) is **present** as the
`episodes` table but the extractor pipeline (episode → semantic memory) is
not running on a schedule.

### Is the short-term buffer a separate store?

**No, and it shouldn't be.** Buffer = session state, not a database. The
right shape for a Jarvis short-term buffer is:

- A small JSON file in `~/.claude/projects/<repo>/working_state_jarvis.md`
  (already the convention) holding rolling summaries every N turns.
- Maintained by a hook (`PostToolUse`-N or `Stop`) that summarises the
  last K turns into a "what I'm currently doing" blob.
- Read by the SessionStart hook and on explicit `memory_recall(query="working state")`.

This is mem0-pattern (lightweight scratchpad) rather than Letta-pattern
(formal working/archival split with promote/demote API). Letta-pattern
needs to live inside the agent runtime; Claude Code's runtime is closed,
so we approximate with hooks + files.

### mem0 vs Letta one-liner

| Pattern | Fits Jarvis? | Why |
|---|---|---|
| mem0: classifier-driven add/search across episodic/semantic/procedural | Yes | Matches existing `memory_store` shape. Drop-in. |
| Letta: explicit working-context + archival-context with promote/demote | No | Requires runtime control we don't have inside Claude Code. |

---

## 5. Recent research — positioning, not deep dive

| Paper / system | One-line position | Does Jarvis steal? |
|---|---|---|
| **A-MEM** (arXiv 2502.12110) | Zettelkasten-inspired: each new memory dynamically links to existing memories with LLM-generated edge labels; supports memory evolution. | Yes — see §6 step 2. The "LLM picks edge label" pattern is the missing layer above current `link_type` enum. |
| **SYNAPSE** (arXiv 2601.02744) `[unverified]` | Spreading-activation + lateral inhibition over a memory graph. Recall isn't single-hop ANN — it's iterative activation propagation. | **Watch.** Interesting but heavy. Worth implementing only after entity graph (§6.3) exists; currently no graph to spread over. |
| **AriGraph** (IJCAI 2025) | KG + episodic combined: agent maintains both a fact graph and an episode log, recall fuses them. | Indirect — confirms the §6 architecture (entities graph + episodic log) is on-trend, not a personal idiosyncrasy. |
| **Zep / Graphiti** | Production temporal KG service: timestamped edges, bi-temporal model (event time vs ingest time). | **Skip as a service**, **steal the bi-temporal idea.** Each edge in our entity graph should carry both `valid_at` and `recorded_at`. |
| **Hindsight** `[unverified]` | Retrospective memory — agent looks back at outcomes and rewrites past memories with hindsight knowledge. | Already partially in system: `outcome_record` + `/reflect`. The Hindsight twist is *editing existing memories* with outcome-derived context, not just appending a separate row. Could inform a future `/reflect` enhancement. |
| **KnowledgePlane** `[unverified]` | Layered memory plane separating raw / consolidated / canonical. | Conceptually maps to `episodes` (raw) / `memories` (consolidated) / `decisions` (canonical). Already roughly the shape. |
| **ICLR 2026 MemAgents workshop** | Memory as a first-class architectural component, not a bolt-on. | Validates Pillar 4 framing. No specific paper to steal — it's the venue. |

---

## 6. Concrete proposal: 3 ranked next-steps

Each step: scope · integration · effort · payoff · confidence.
All three avoid touching the Supabase store schema for `memories` itself
beyond additive changes (new types, new tables, new functions).

### Step 1 — Add `procedural` as a memory type + episode→semantic extractor cron

**Scope.**
- One-line schema change: add `'procedural'` to the `memories.type` CHECK
  constraint and `VALID_TYPES`.
- Update `tools_schema.py` enum lists for `memory_store`/`recall`/`list`.
- Stand up the missing extractor: a scheduled task (`/loop` or scheduled
  agent) that scans `episodes WHERE processed_at IS NULL`, batches by
  `actor`, asks Haiku to distil into 0..N semantic/procedural memories,
  marks `processed_at`. The hooks for this already exist (the `episodes`
  table was clearly designed for it).

**Integration point.**
- `memory_store` / `memory_recall` — no API change, new value in enum.
- `episodes_list` / `episodes_mark_processed` — already exist
  (`tools_schema.py:354,388`). Extractor consumes them.
- `/reflect` skill becomes the natural caller for the extractor; can also
  be invoked autonomously in `/end` hook.

**Effort.** ~1 day end-to-end. Schema migration (2h), enum update (1h),
extractor prompt + scheduling (4h), tests + provenance plumbing (2h).

**Payoff.**
- Procedural-as-type: skills become discoverable through `memory_recall`
  rather than only through skill auto-discovery. New self-improvement loop
  becomes possible: agent notices a 3rd repetition → stores a procedural
  → next time recalls it as the canonical playbook.
- Extractor: closes the existing dangling pipeline. Today the `episodes`
  table is a write-only graveyard. Activating the consumer turns it into
  the substrate for behavioural learning — exactly what `/reflect` is
  trying to do manually right now.

**Confidence: 4/5.** The schema work is mechanical. Risk lives in
extractor quality (false-positive procedural memories that pollute
recall) — mitigated by `/reflect` review queue + `source_provenance`
audit trail.

---

### Step 2 — A-MEM-style typed edges on `memory_links` + `memory_consolidate`

**Scope.**
- Expand `memory_links.link_type` enum from 3 values
  (`related, supersedes, consolidates`) to A-MEM-style typed set:
  `related, supersedes, consolidates, contradicts, supports, depends_on, refines, instance_of`.
- Add `edge_metadata jsonb` column on `memory_links` for LLM-generated
  edge rationale (one sentence: "why is A linked to B").
- Hook into existing `memory_store`: after a write, classifier compares
  the new memory against top-K vector neighbours and emits 0..N typed
  edges with rationale. This is the A-MEM "dynamic linking" mechanism.
- Surface the new edges in `memory_graph` tool output.

**Integration point.**
- `memory_store` — internal post-processing step, no public API change.
- `memory_graph(mode='links', name=...)` — already exists; gains richer
  output.
- Optional: `memory_recall` re-ranking that walks supports/refines edges
  by 1 hop and includes connected memories in context.

**Effort.** ~1.5 days. Schema migration + backfill (3h), classifier
prompt (3h), `memory_store` integration with strict cost budget — Haiku
only, batched, opt-out flag (4h), tests (2h).

**Payoff.**
- Catches the "contradicts" relation that today only the user notices.
- Enables consolidation suggestions that are *typed* rather than just
  "these are similar".
- Foundation for §6.3 — entities can hang off these edges later.

**Confidence: 3/5.** A-MEM's gain is real on benchmarks, but the cost of
adding LLM-classification on every write is recurring. Mitigation: budget
caps + opt-out for high-frequency writes (e.g. `outcome_record` rows).

---

### Step 3 — Lightweight entity graph (`entities` + `entity_edges` + bi-temporal stamps)

**Scope.**
- Two new tables in the same Supabase project:
  ```sql
  create table entities (
    id uuid primary key default gen_random_uuid(),
    kind text not null check (kind in
      ('person','project','issue','decision','outcome','skill','external_system','tool')),
    name text not null,
    canonical_id text,                 -- e.g. github URL, memory.id
    attrs jsonb default '{}',
    created_at timestamptz default now(),
    unique(kind, name)
  );

  create table entity_edges (
    id uuid primary key default gen_random_uuid(),
    source_id uuid not null references entities(id) on delete cascade,
    target_id uuid not null references entities(id) on delete cascade,
    relation text not null,            -- about, led_to, depends_on, contradicts, mentioned_in, owned_by
    valid_at  timestamptz,             -- when the relation became true (Zep bi-temporal)
    recorded_at timestamptz default now(),
    valid_until timestamptz,
    source_memory_id uuid references memories(id) on delete set null,
    confidence float default 1.0,
    unique(source_id, target_id, relation, valid_at)
  );
  ```
- Two new MCP tools (parallel to `memory_*`, not replacing):
  - `entity_list(kind?, query?)` — search entities.
  - `entity_neighbors(name, hops=1, relations?)` — recursive CTE traversal.
- Bridge: `record_decision`, `outcome_record`, and the §6.1 extractor
  upsert entities and emit edges as a side-effect. No agent intervention
  needed for the common case.

**Integration point.**
- `record_decision` — already structured. Can extract entities
  (issue numbers, project names, people) on every call.
- `outcome_record` — natural source of `decision -[LED_TO]-> outcome`
  edges.
- New tools coexist with `memory_*`; `memory_recall` can optionally
  enrich its hits with entity-neighborhoods (think of it as the graph
  analog of `include_links`).

**Effort.** ~2.5 days. Schema (3h), entity-extraction prompt for the
extractor (4h), two MCP tools + tests (6h), back-population from
existing decisions/outcomes (3h), docs (2h).

**Payoff.**
- All five queries from §3 become trivial.
- Cross-project intel: redrobot decisions become reachable via
  `entity_neighbors(name='supabase_schema')` regardless of which repo
  recorded them.
- Bi-temporal stamps unlock "what did I think was true on 2026-04-01?"
  audits — exact same pattern Zep sells, but in our own Postgres.

**Confidence: 3/5.** The architectural shape is sound; the risk is
maintenance load on entity extraction quality. Start narrow (only
entities mentioned by `record_decision` and `outcome_record`),
expand only after a month of clean operation.

---

### Order rationale

1. **Step 1 first** because it activates dead infrastructure (`episodes`
   table) and adds the missing memory type with near-zero risk. The
   extractor it requires is a precondition for steps 2 and 3.
2. **Step 2 second** because typed edges are a substrate-only change —
   adds value to existing memories without requiring a new mental model.
3. **Step 3 last** because it introduces a parallel data shape (entities
   ≠ memories) and that's a real cognitive cost. Earn it.

---

## 7. Anti-patterns

### When graph is overkill

- **<500 memories total.** Vector + relational filters cover everything.
  The user is not yet at this floor.
- **No multi-hop queries in the actual usage log.** If `memory_recall`
  logs show only top-K vector queries, building a graph is solving an
  imaginary problem. Recommend: instrument `memory_recall` with query
  shape tags for 30 days before committing to step 3.
- **Single-hop "what's related to X"** — `memory_links` already covers
  this; adding entity nodes is overkill if the query never crosses
  entity types.

### When procedural is just decisions in disguise

- A `decision` memory says "we chose X over Y because Z." A `procedural`
  memory says "to do X, run these N steps." If your candidate memory
  reads as the former, store it as `decision`, not `procedural`.
- Procedural is **executable** — if you can't imagine the agent
  *following* the steps next time, it's reference, not procedural.
- A skill file (`.claude/skills/foo/SKILL.md`) that's been stable for >2
  weeks is already procedural. Don't double-store it as a procedural
  memory; instead, store a procedural memory **about choosing** the
  skill ("when X happens, invoke /foo"). The skill is the body; the
  procedural memory is the trigger.

### Graph without entity discipline

A graph where every node is a memory and every edge is `related` is just
a vector store with extra tables. Edges must be typed (step 2) and at
least some nodes must be non-memory entities (step 3) for the graph to
pay rent.

### Episodic buffer that becomes a third memory store

The short-term buffer (§4) must stay in files / session state. The
moment it lives in Supabase with its own embeddings, it becomes a fourth
memory tier with its own consolidation rules and you've doubled the
classifier surface area. Resist.

---

## 8. Open questions for owner

1. **Procedural classifier authority.** Should the classifier auto-promote
   `type='reference'` writes to `'procedural'` when text reads as
   imperative, or require the writer to set the type explicitly? mem0
   does the former; Jarvis convention so far is the latter (writer is
   responsible).

2. **Edge-type budget.** A-MEM uses an open vocabulary of edge labels
   (LLM picks any string). The proposal in §6.2 closes this to an enum of
   8. Do we want the open vocabulary (more expressive, harder to query)
   or the enum (easier to query, may miss relations)?

3. **Bi-temporal cost.** Adding `valid_at` to every edge is cheap
   storage-wise but doubles the cognitive load on every write
   ("when did this become true?"). Worth it for the Zep-style audit, or
   skip and just rely on `recorded_at`?

4. **Cross-project entities.** If `entity:project='redrobot'` lives in
   the shared Supabase, the entity graph spans both repos. That's
   correct but means a redrobot-side change can affect Jarvis recall
   semantics. Acceptable, or do we namespace entities per-project?

5. **Extractor cadence.** Once the extractor exists, what's the schedule?
   On every `/end`? Nightly cron? Both? Sweep §7 implies nightly is
   cheap; the practical question is whether the user wants extractor
   noise to surface during a session or only at boundaries.

6. **Eval discipline for memory.** Sweep §5.6 flagged
   "eval-as-skill" as missing. Memory is the first place this should
   land — without recall@K eval, every change in §6 is faith-based.
   Should §6.0 actually be "build a memory eval harness first"?

7. **Should `decisions` and `outcomes` migrate to entities?** Today they
   live as memory rows + their own `task_outcomes` / decision-record
   plumbing. Once entities exist, there's pressure to canonicalise
   them as entity nodes. Migration vs duplication?

---

## Sources used

Primary (read directly):
- `mcp-memory/schema.sql` (HEAD on `feat/510-rename-analyze-comms-to-reflect`)
- `mcp-memory/server.py` (lines 1–100)
- `mcp-memory/tools_schema.py` (lines 1–760)
- `docs/research/agent-dev-practices-sweep-2026-05-06.md` §3.6, §7

Secondary (training-cutoff knowledge, **not re-fetched** this session — see
sourcing note at top):
- mem0 blog "State of AI Agent Memory 2026"
- digitalapplied "Agent memory architectures: vector, graph, episodic"
- Atlan "Episodic memory in AI agents"
- Shichun-Liu/Agent-Memory-Paper-List
- arXiv 2502.12110 (A-MEM), arXiv 2601.02744 (SYNAPSE)
- AriGraph (IJCAI 2025), Zep/Graphiti docs, Letta/MemGPT papers

> If the owner wants higher confidence on the mem0/A-MEM specifics in §2
> and §6.2, the right next step is a 20-min manual read of the mem0 v1
> blog + A-MEM paper, then re-run this analysis with verified citations.
