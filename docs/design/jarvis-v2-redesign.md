# Jarvis Architecture — Capability Roadmap

> Top-down architectural design derived from principal goals. Structures the system into 18 capabilities across 5 layers, with membership rules, measurement plans, migration paths, and a bootstrap protocol. Maps to v1 stabilization + 1.x feature evolution; v2 is reserved for cardinal paradigm shifts (framework swap or equivalent).

## How to read this doc

Stratified levels: **L0** (mission/qualities) → **L1** (capabilities) → **L2** (components) → **L3** (technologies/patterns).

Each section follows one template:

```
### <topic>
**Question:** what we're deciding.
**Options:** A — one line. B — one line. C — one line.
**Decision:** <chosen option>.
**Why:** rationale.
**Rejected & why:** keep alternatives with reasoning so we don't reopen.
```

Research is delegated to subagents; only summaries land here.

No intermediate diagrams. One C4 (Context → Container → Component) at the end.

## Status

| Level | State |
|---|---|
| L0 — mission, qualities, non-goals | **done** |
| L1 — capabilities | **done** (18 caps, 5 layers, 3 ops policies) |
| L2 — components per capability | **done** (Tier A: C3, C5, C6, C15, C16, C17, C18; Tier B: C2, C4, C8, C13, C14; Tier C: C1, C7, C9, C10, C11, C12) |
| Bootstrap protocol & migration order | **done** |
| Critical review pass | **done** (high-severity addressed: bootstrap + cost reality + ~15 point fixes) |
| L3 — technologies/patterns | **done** (options marked + scout v2 adoption candidates folded in 2026-04-27 per [`jarvis-build-vs-buy.md`](jarvis-build-vs-buy.md); decisions deferred to implementation) |
| Final C4 | **done** ([jarvis-architecture-c4.md](jarvis-architecture-c4.md)) |
| Sanity check vs `jarvis_v2_vision` | **done** |
| v1 exit criteria + pillar→capability mapping | **done** |

---

## L0 — Mission, Qualities, Non-goals

### Mission

Jarvis is an agentic system that takes the bulk of software-project work off the principal, helps with decisions, runs research, pushes back on bad ideas, and steers thinking — not just executing instructions, but actively shaping them.

### Target user & scope

**Personal-Jarvis for the principal only.** Not a product. Not multi-tenant. Tightly coupled to principal's repos, devices, identity, and SOUL.md.

A separate company-Jarvis variant may be developed in parallel by the principal's company, but it is a **separate project** with its own design, DB, and deployment (per `pillar7_personal_vs_company_separation`). Architecture design ignores company-variant requirements; shared code is extracted post-hoc when the company variant has concrete needs.

Public-Jarvis open-source framework is also a **separate fork** with its own quality bar (per `open_source_quality_standard`); reformatted from private at release boundaries, not architected jointly.

### Problems solved

Jarvis covers the work of an entire software development team, leaving the principal only as resource provider and strategic stakeholder:
- Code authoring & testing
- Project management & progress tracking
- Research & decision support
- Deep code comprehension on demand
- Managing other agents (sub-orchestration)
- Catching principal errors / steering ideas

### Non-goals

**This architecture explicitly excludes:**
- Personal life management (smart home, shopping, calendar)
- Email and messenger management on principal's behalf
- Impersonating the principal — Jarvis drafts; principal sends
- Telegram as a primary interface (chat-only role at most, see L1)
- Designing for other developers / multi-tenancy (separate project)
- Persistent always-on process — Jarvis operates via event-woken cold boot, not a resident daemon. Persistent BEHAVIOR ≠ persistent PROCESS (resolved 2026-05-20 — see `pm_dispatch_v1_superseded_by_persistent_agents`).

**Future feature backlog** (1.x, not separately versioned): TTS/STT, broader personal-data access, cross-platform search/curation, personal-life management, open-source framework split. Captured in memory + GitHub issues, surfaced via 1.x release planning when each becomes ready. Not "v3" — semver discipline reserves v2 for cardinal paradigm shifts.

### Quality attributes

Principal-stated priorities (1–10), with tradeoff notes where they conflict:

| Quality | Priority | Notes |
|---|---|---|
| Memory | 10 | Cross-device, durable, deep. Top constraint. |
| Cost (monetary) | 9 | Claude Max subscription $100/mo (covers Claude Code interactive + scheduled, throttled by usage units not $). External services: **soft cap $20/mo (steady-state target), hard cap $100/mo (block non-essential above this)**. Expected steady-state ~$120/mo total ($100 Max + ≤$20 externals); ceiling ~$200/mo if externals run at hard cap. |
| Extensibility | 9 | Adding capabilities should be cheap. |
| Autonomy | 8 | Acts without confirmation by default; confirms only critical/irreversible. |
| Multi-device | 8 | 3 devices baseline. Device-specific tasks (physical access) acceptable as exceptions. |
| Personality | 7 | Has opinions, principles, thinks. SOUL.md-style is the baseline. |
| Security + Privacy (merged) | 7 | Cloud storage OK. Threat model = external intruders / account takeover, not the cloud provider itself. Strong access control + recovery paths required. |
| UX | 7 | Principal is the only user; doesn't need polish but must not be friction. |
| Flexibility (within work) | 7 | Wide range of work tasks; **does not** stretch beyond work scope. |
| Offline tolerance | 3 | Acceptable to be unusable when offline (Claude API + Supabase + GitHub all need internet anyway). |

**Resolved conflicts:**
- *Memory 10 vs Privacy 7* — privacy redefined as access control, not data residency. Cloud storage allowed.
- *Flexibility 7 vs «just work tasks»* — flexibility is breadth **inside** the work domain, not outside it.

### Autonomy / decision boundary

Jarvis decides autonomously when it knows the principal's preference (from memory or SOUL); asks when it doesn't. **Long-term goal: digital-twin level — Jarvis knows the principal well enough to decide everything, only confirming critical/hard-to-reverse decisions.**

Decision routing:

| Situation | Behavior |
|---|---|
| Principal's view known (memory/SOUL) | Decide and act. |
| Principal's view unknown, low-cost / reversible | Decide and act, log decision. |
| Principal's view unknown, hard-to-reverse / high-cost | Research, propose, confirm. |
| Both don't know | Research, decide, confirm before acting. |

This makes **memory of principal preferences** the lever that reduces interruptions — every captured preference shifts the line further into autonomy. Reinforces *Memory = 10*.

### Harness coupling — deliberate trade-off

Jarvis is built on Claude Code as the runtime harness. This is a deliberate trade-off, not an oversight:

**What's locked to Claude Code:** skills (slash commands), hooks (PreToolUse / SessionStart / etc.), SOUL.md / CLAUDE.md auto-load, subagent `isolation:worktree`, plugin marketplace fork base.

**What's harness-agnostic by design:** the cap layer (C1–C18 describe *what*, not *which runtime*); Supabase memory schema + bi-temporal periods; canonical events substrate (OTel GenAI conventions); MCP servers (`mcp-memory/server.py` works with any client speaking MCP); embeddings via VoyageAI; goals tables.

**Why accepted:** Claude Code currently closes the largest set of pain points (memory + breadth + subagent isolation + native plugin ecosystem) for the smallest implementation cost at this scale. Building a "harness adapter" abstraction with one real implementation = indirection, not abstraction (per SOUL §«Abstractions need two real implementations»).

**Cost of swap if a better harness ships:** rewrite ~12 skills + 4–5 hooks + subagent dispatch glue (~1–2 sprints). Data, schema, and cap layer survive.

**Trigger to reconsider:** another runtime substantively better than Claude Code in our use case (not «new shiny release»). Until then, harness lock-in stands. Q7 in pass2 review tracks this; v2 (per Status table) is reserved for the framework-swap class of change.

### Engineering principles (anti-vibe-coding stance)

Jarvis is built and operated under the AI Hero / Pocock stance: AI raised the stakes on engineering fundamentals, didn't lower them. Garbage codebase → garbage agent output. The principles below constrain *how* Jarvis is built and *how* Jarvis builds (the agent applies them to its own work too).

- **Real engineering > vibe coding.** Modularity, testability, clear interfaces. LLM speed is not a substitute for engineering discipline.
- **Smart zone (~100K tokens), Plan / Execute / Clear.** Past ~100K, reasoning quality drops. Sessions write state to memory and start fresh windows. Reviews of own work go in fresh sessions, not the same one that wrote the code.
- **Vertical slices.** Each task crosses the whole stack to a verifiable result. No "all schema then all API then all UI" — feedback arrives too late.
- **Deep modules.** Small interface, large hidden implementation. Apply the deletion test before adding a third small adjacent file.
- **TDD as the agent's runtime ground truth.** Tests verify behavior through public interfaces. Without them, the agent flies blind.
- **Tight automated feedback loops.** Types, tests, linters, scripts — anything that gives the agent ground truth without a human in the loop.
- **Reach shared understanding before writing the plan.** PRD is an *input* for the next phase, not a human-readable artifact (`/grill`).
- **Don't bite off more than you can chew.** Decompose into independently-grabbable issues. Planning depth beats task ambition.
- **Treat agents like humans with no memory.** Strict, repo-level processes (skills, playbooks, glossaries) compensate. Vibes don't.

Canonical statements live in `.claude-userlevel/reference/engineering-principles.md` (operational rules; installed to `~/.claude/reference/`) and `docs/research-aihero-principles.md` (full research, sources, 3-layer mapping). `CONTEXT.md` carries domain glossary + invariants for in-session loading. This section is the L0 cross-link, not the source of truth — keep it short and let the canonical files drift independently.

---

## L1 — Capabilities

### Research notes (compressed)

External landscape studied 2026-04-27 (8 systems incl. Devin, Claude Code Agent Teams, MS Agent Framework 1.0, Magentic-One, MetaGPT, SWE-agent, LangGraph, Generative Agents).

**Convergent — every studied system has:** orchestrator/lead, planner, specialized worker roles, tool use (MCP), inter-agent communication, some form of memory.

**Variant — splits the field:** memory model (scratch vs pluggable persistent vs episodic+reflective), explicit vs implicit re-planning, critic/verification (role vs mechanism vs absent), HITL/checkpointing.

**Patterns worth stealing:**
- *Agent-Computer Interface* (SWE-agent) — environment shaped FOR the agent.
- *Task + Progress Ledgers* (Magentic-One) — orchestrator-owned dual artifacts.
- *Reflection* (Generative Agents) — periodic synthesis → higher-level memory.
- *SOP-as-code* (MetaGPT) — process is the artifact.
- *Shared task list + mailbox* (Claude Code Agent Teams).

**Conspicuously missing in others (gaps we should fill — they align with L0 priorities):**
- Stakeholder / strategic-communication layer (Mission: "steers, pushes back").
- Long-horizon cross-session learning (Memory=10).
- Goal / priority management as first-class block.
- Cost / budget governance at architectural level (Cost=9, $100–200/mo cap).
- Self-modification / capability acquisition (Extensibility=9).

Filling these gaps is the deliberate differentiator vs the field.

### Final capability list

Five layers, 18 capabilities:

**Identity layer** — what Jarvis *is*:
- **C1. Identity & values** — SOUL-style personality and principles that shape every decision. Distinct from memory: identity is principal-authored axioms; memory is learned facts.
- **C2. Goals & priorities** — active strategic context that prioritizes work. Kept separate from C3 because goals are *active* (drive ranking) while memory is *passive* (provides context). Analogous to SOUL.md vs CLAUDE.md.

**Cognition layer** — how Jarvis *thinks*:
- **C3. Memory** — durable cross-device store. Has two sub-types (detail in L2): *principal/preference memory* (durable, slow-changing) and *project/world knowledge* (volatile, refreshable). State/handoff between sessions lives here.
- **C4. Reasoning & planning** — decompose tasks, sequence steps, replan on signals.
- **C5. Reflection / learning** — periodic synthesis. Two roles: (a) raw episodes → lessons → new memory; (b) **challenge stale preferences** so Jarvis doesn't fossilize on outdated principal views.
- **C6. Decision gating** — escalation matrix:
  | Situation | Behavior |
  |---|---|
  | Only one viable path | Act, log decision (principal is *informed* afterward). |
  | Multiple options, only one fits the work | Act, log decision + why others rejected. |
  | Multiple genuinely viable options | Escalate — propose options, principal picks. |
  | Destructive / hard-to-reverse | Always confirm regardless of ambiguity (SOUL rule). |
  | Low confidence in own analysis + high stakes | Escalate even when above says "act". |

  Principal's only inputs: (a) be informed of decisions (low-effort read), or (b) pick among proposals. Principal never reads implementation by default.
- **C18. Proactive challenger** — alongside C5: surfaces calibration drift, neglected goals, and contradictions between principal's stated direction and observed behavior. Triggers user-facing recalibration via C12 when the gap exceeds threshold. Distinct from C5 (which mutates memory) — C18 *only surfaces* without mutating.

**Action layer** — what Jarvis *does*:
- **C7. Execution** — perform individual tasks (code edit, test run, message draft).
- **C8. Sub-orchestration** — dispatch and manage subagents for parallel work.
- **C9. Tool / environment interface** — connect to repos, filesystem, MCP servers, external APIs.
- **C10. Research** — gather external info to inform reasoning. Tightly coupled to C4 but kept separate: research explores possibilities without choosing; reasoning chooses based on what research returned.

**Interface layer** — how Jarvis *interacts with principal*:
- **C11. Perception** — receive inputs from all sources: principal messages, repo/GitHub events, schedule triggers, file changes, external system events. Decision *when to act on a trigger* lives here, but is informed by C2 + C3 (autonomy is memory-driven, not pipeline-driven — see `no_deterministic_pipelines`).
- **C12. Communication with principal** — strategic comms: push back, propose, confirm, report. Channels (chat / desktop / mobile / voice) are L2 detail.

**Cross-cutting** — properties applied across all layers:
- **C13. Budget / cost governance** — track spend against $100–200/mo cap, refuse cap-blowing actions, optimize model selection (use lighter models for mechanical work).
- **C14. Security & privacy** — secret protection, access control, recovery paths. Threat model = external intruders / account takeover; cloud provider is trusted.
- **C15. Self-improvement** — Jarvis modifies itself. **Hybrid scope**: read-only self-analysis when running autonomously (proposes changes); write access only when principal is actively collaborating on Jarvis's own code.
- **C16. Verification / QA** — automated review of Jarvis's own work. Principal does not code-review by default. Reviewer is a dedicated agent: a peer Jarvis instance, or a different model/provider for independence (different-model = no shared bias). Principal reviews vision/plan alignment only.
- **C17. Observability & audit** — track-record for autonomous operation: action logs, decision logs, replay/diff, self-monitoring (rate-limit, memory write failures, suspicious tool calls). Enables: (a) trust-building over time, (b) post-hoc principal inspection, (c) self-detection of malfunction. Required for *Memory=10* and *Autonomy=8* to work safely.

### Operational policies (flow from L0+L1, not capabilities themselves)

These are not blocks on the diagram but commitments that constrain L2:

- **Trust ladder.** Autonomy scope expands by track record. Day 1 = safe-class actions only (reversible, low-cost, well-precedented). Categories unlock as Jarvis builds successful history in C17.
- **Review topology.** Code review is fully delegated. Principal's review surface = vision/plan alignment only, never implementation diff. Different-provider reviewer is preferred over same-model peer for high-leverage changes.
- **Memory-driven autonomy, not pipelined.** No hardcoded execution order. Jarvis decides what to do based on judgment + memory + goals (`no_deterministic_pipelines`).
- **Design-to-evaluate.** Every L2 capability decision must include "How measured" — a real-task quality signal, not unit-test sufficiency. Rationale: in-vacuum success ≠ in-task success; if a block isn't measurable in production, we cannot tell if our design works. **Benchmarks are built before implementation**, not after. Capabilities without a viable measurement plan get deferred until one exists.

### Decision

L1 capability set locked: **18 capabilities in 5 layers + 3 operational policies.** Justification for going wider than competitor systems: every "extra" capability (C5 challenge-old, C13 budget, C15 self-improvement, C16 verification, C17 observability, C18 proactive challenger) addresses a gap explicitly identified in research as missing in the field but required by L0 priorities.

### Rejected during review

- *Merging C2 Goals into C3 Memory* — different roles (active vs passive).
- *Merging C5 Reflection into C3 Memory* — reflection is an active loop, memory is storage.
- *Merging C6 Decision gating into C4 Reasoning* — decision policy ≠ reasoning process.
- *Merging C10 Research into C4 Reasoning* — coupled but distinguishable; explored as possible sub-component, kept separate.
- *Splitting C3 into two L1 capabilities* — sub-types acknowledged, but split happens at L2 (single capability, two storage flavors).
- *C15 Self-improvement first-class with full write* — too risky in v2; deferred write to collaborative mode.
- *Multi-instance coordination as separate L1* — folded into C8 Sub-orchestration with peer-instance note for L2.

---

## L2 — Components

### Methodology

L2 template (per capability):
```
**Question:** ...
**Options:** A — one line. B — one line. C — one line.
**Decision:** ...
**Why:** ...
**Rejected & why:** ...
**How measured:** real-task quality signal (op policy: design-to-evaluate).
```

For each capability we run two research streams and synthesize:

1. **Internal experience distillation** — subagent reads relevant memory entries and current Jarvis code/docs, distills *root problems hit* (separate from solutions chosen, since past solutions may have been symptom-fixes). Treated as third-party opinion with its own bias, not truth.
2. **External research** — fresh subagent surveys current state of the art (no training-data trust).

Stratification:
- **Tier A** (deep): C3, C5, C6, C15, C16, C17, C18 — novel or risky, full two-stream research.
- **Tier B** (medium): C2, C4, C8, C13, C14 — discussion + targeted research.
- **Tier C** (shallow): C1, C7, C9, C10, C11, C12 — single-line decision.

Order within Tier A: C3 Memory and C17 Observability first (other caps depend on them), then C5/C6/C15/C16/C18 (C18 sequenced after C5 — shares the calibration substrate). After Tier A, Tier B, then Tier C.

### C3 — Memory

**Question:** What is the structure of Jarvis's memory subsystem after de-overloading the current catch-all?

**Membership rule:** something belongs in memory iff *long-lived* AND *retrieval-driven* AND *content-not-mechanism*. Fail any → relocate to a dedicated subsystem.

**De-overload — moves OUT of memory in v2:**
- Behavioral rules with mechanical enforcement → **hooks** (Claude Code's first-class hook system; separation of enforcement from memory is universal across studied systems).
- Working state / session checkpoints → **session-bootstrap subsystem** (universal split: LangGraph checkpointer, Letta core-block, Assistants Thread, Devin Checkpoints, Claude Code compaction).
- Operational logs (`consolidation_report_*`, `evolution_plan_*`, etc.) → **C17 Observability** (universal: every studied system keeps telemetry outside memory).
- Always-load instructions → **C1 Identity** (CLAUDE.md/SOUL.md pattern; universal: persona block, Custom Instructions, `instructions` field, Project Rules, Playbooks).
- Goals → **C2** (already L1-separated; storage decoupled too).

Rules requiring subjective judgment (rare) stay in memory tagged as procedural-feedback for retrieval on related context.

**Decision: memory becomes 2 sub-stores.**
- **C3-F. Factual store** — principal preferences, project knowledge. Slow-changing, retrieval-driven, supersedable.
- **C3-E. Episodic store** — decisions, outcomes, incidents. Append-only, time-ordered.

Both accessed through one recall API (caller doesn't pick a store).

**Why 2 not 1:** different lifecycles (facts supersede, episodes accumulate), different confidence semantics, different decay shapes. Mixing them caused current pain.

**Why 2 not more:** further splits (procedural / semantic / working) violate membership rule (procedural = hooks; working = session). Tier-based splits (Letta in-context vs archival) are retrieval optimization, not structural.

**Rejected:**
- *Single flat store with `type` field* (current state) — what we're moving away from. Source of overload pain.
- *4-store LangGraph/Mem0 model* (semantic/episodic/procedural/working) — procedural and working fail membership rule.
- *Letta-style core-memory in-context* — conflicts with Cost=9 cap (in-context tokens are the most expensive memory).
- *Mem0 user/org scope tier* — N/A (single user).

**How measured — four signals, ALL built before any v2 memory code lands:**

1. **Self-replay regression** (offline, CI) — principal's historical sessions as gold set; on every memory-subsystem change, replay and verify recall surfaces the memories the session actually used. Primary CI gate. Cheap (use existing logs). Strongest fit for N=1 personal agent.
2. **Useful-injection rate** (sampled online) — LLM-as-judge scores a **10% sample of recalls** for whether the surfaced memory was load-bearing in the response. Sampling (not every recall) is the deliberate cost choice — judging every recall would be the single biggest unbounded cost (~$30+/mo at high recall volume). 10% gives a continuous signal at ~$0.50/mo.
3. **Staleness / contradiction probe** (periodic, FAMA-style) — sample N memories weekly; pose questions whose answers reveal whether stale or fresh content surfaces. Catches beliefs that should have been superseded but weren't.
4. **Decision-outcome linkage** (retrospective) — for `record_decision` episodes, after outcome is known, were the memories that informed the decision actually correct? Calibrates `confidence` values via real outcomes.

Heavy academic benchmarks (LoCoMo, MemoryAgentBench, MEMTRACK) → run **before major releases**, not on a fixed schedule (release-gated, not time-gated). N=1 A/B (memory ON/OFF) impossible — substituted by self-replay regression.

**Open for next pass within C3:** Q3 conflict resolution model, Q5 provenance use in recall ranking.

---

#### Storage shape

**Question:** How are C3-F (factual) and C3-E (episodic) physically structured?

**Decision:** Two tables, one per sub-store.

- **`memory_facts`** — supersedable. Bi-temporal columns (`valid_from`, `valid_to`), `superseded_by`, `confidence`, `provenance`, embedding, tags. Recall returns the *current head* of any supersession chain by default.
- **`memory_episodes`** — append-only. `occurred_at`, `event_type`, payload, links to associated decisions/outcomes. No supersession; aging happens via *extraction* to facts (an episode is mined for durable knowledge that lands as a fact), not via in-place mutation.

Shared: provenance enum, embedding column shape, recall API.

**Why two tables not one with `kind` flag:** different write rules (supersedable vs append-only), different indices, different scoring. Single-table flag forces every query to filter and every write rule to branch — exactly the current pain.

**Why not three+:** other "kinds" failed the membership rule and relocated. C3 ends up genuinely two-shaped, not more.

**Rejected:**
- *Single table with `kind` flag* — current pain pattern.
- *Episodes inside `memory_facts` with `valid_from = valid_to = occurred_at`* — abuses bi-temporal for append-only, breaks supersession semantics.
- *Episodic-only with on-the-fly fact extraction at recall* — recomputes durable knowledge every call, blows Cost=9.

**How measured:** schema-level invariant tests (no orphan supersession links, no facts without valid_from, no episodes with `valid_to`). Self-replay regression confirms recall surfaces correct head-of-chain on facts.

---

#### Recall path (single funnel)

**Question:** How do all callers (MCP tool, hooks, cloud scheduled tasks, subagents) reach memory consistently?

**Decision:** Canonical recall + write API = **Postgres functions**. All paths call the same function. Python MCP layer is a thin wrapper that adds OPTIONAL LLM enhancements (query rewriting, judge); it does not gate access.

Implications:
- Cloud scheduled tasks (`execute_sql`) call the canonical function — full guarantees, no degraded path. Current cloud-bypass pain eliminated.
- Hooks call the canonical function (via direct Postgres connection or RPC) — no more drift between handler API and hook RPC.
- Writes from any path enforce provenance + dedup + classifier trigger because they happen *inside* the function. No raw `INSERT` allowed.
- Observability event emission lives **inside** the function so every recall is logged regardless of caller (closes the FOK hook-bypass gap). **Write semantics:** event emission uses Postgres `NOTIFY` + best-effort row insert; if event-row insert fails, the recall/write itself does NOT fail — the event-write failure is logged via the next successful event with `degraded=true`. C3 functionality survives transient C17 substrate issues; no circular blocking.

**Why DB-as-canonical, not Python-as-canonical:** cloud scheduled tasks have no Python runtime; Python-canonical means cloud always degrades. DB-canonical means **every caller is first-class**.

**Why thin Python wrapper still exists:** LLM-based query rewriting and LLM-as-judge can't run in PL/pgSQL. They're enhancements, not gatekeeping. MCP callers get them; hooks/cloud get the canonical recall without rewriting (acceptable — these contexts have less ambiguous queries).

**Rejected:**
- *Python-only canonical API* — cloud bypass = current pain.
- *DB-only with no Python* — loses LLM rewriting + judge.
- *Multiple paths with shared library* — current state; drift inevitable because no single enforcement point.
- *Read-replica per caller for performance* — premature; revisit if benchmarks force it.

**How measured:**
- **Path parity test**: same query via MCP, hook, and cloud SQL must return the same ranked recall set on a fixed corpus.
- **Useful-injection rate by path**: caught divergence (e.g., hook recalls suddenly less load-bearing) signals enhancement layer drift.
- **Event log audit**: every recall has a corresponding event row; gap = a path that bypassed the function.

---

---

#### Conflict resolution

**Question:** When the store holds memory A, then A', then A'' on the same topic — what surfaces during recall, and when does new content supersede old?

**Decision: bi-temporal heads + tiered supersession trigger.**

*Truth selection at recall:* canonical function returns only current heads (`valid_to IS NULL`) by default. History accessible via explicit `include_history=true` flag for audit/UI.

*Supersession trigger* — three lanes:
1. **Explicit declaration** (writer states `supersedes=<id>`): trusted, applied immediately. Used by classifier when confidence is high and by principal corrections.
2. **Classifier proposal** (Haiku-style ADD/UPDATE/SUPERSEDE/NOOP): auto-applied only if **(a)** classifier confidence ≥ threshold AND **(b)** measured class-precision (from outcome labels) ≥ threshold. The second gate self-throttles when classifier quality drifts. **Bootstrap:** before N labeled outcomes accumulate per class (N defined in bootstrap protocol), gate (b) is *bypassed* — classifier auto-applies on confidence alone but with a **conservative initial threshold** (e.g., 0.95 instead of 0.85). As labels accumulate, gate (b) activates and threshold relaxes if precision is high.
3. **Below thresholds**: goes to review queue with principal CLI loop. Queue throughput is itself a measured signal.

*Conflict detection ("same topic")*: hybrid — embedding similarity gives candidate set; LLM verifier confirms semantic overlap before triggering supersession classification. Prevents embedding-only false positives.

*Principal correction*: `record_correction(memory_id, correct=<id_or_content>)` creates a high-trust ground-truth label, used both to (a) immediately fix recall, and (b) feed the classifier eval that gates lane 2.

**Why this not pure auto-classify** (current state): classifier without measured precision is a black box; failures are silent. Two-gate (confidence + class-precision) makes failures self-correcting.

**Why this not explicit-only**: most memory writes come from LLM agents that won't reliably declare supersession. Pure explicit = supersession never happens for the bulk case.

**Rejected:**
- *Last-write-wins* — destroys provenance, loses minority-but-correct view.
- *Highest-confidence-wins ignoring time* — old confident-but-stale memory beats new less-confident-but-correct one.
- *Principal-only supersession* — bottlenecks the principal; conflicts with "only stakeholder" mission.

**How measured:**
- **Classifier precision/recall by class** (ADD/UPDATE/SUPERSEDE/NOOP), labeled by `record_correction` events. Triggers threshold updates.
- **Time-to-resolution for review-queue items**: how long stale beliefs coexist with corrections.
- **Self-replay**: after principal correction, does subsequent recall surface the correction (not the old)?
- **Contradiction probe** (FAMA-style): catches stale heads that should have been superseded.

---

#### Provenance-aware ranking

**Question:** Provenance is collected (NOT NULL, namespaced) but unused in recall. How does it affect ranking and supersession?

**Decision: provenance hierarchy as a recall ranking factor and a supersession tiebreaker.**

*Hierarchy* (highest → lowest trust):
1. `user:explicit` — principal direct statement
2. `tool:<name>` — verified tool/system output (deterministic)
3. `agent:<role>` with stored `confidence ≥ 0.7` — Jarvis or subagent inference, well-supported
4. `agent:<role>` with stored `confidence < 0.7` — low-supported inference
5. `external:<system>` — extracted from third-party source (web, GitHub issue body, email)
6. `legacy:*` — pre-typed migrations, lowest weight

The 0.7 boundary between tiers 3 and 4 is the same threshold C5's calibrator tunes per writer — when a writer's outcomes show its 0.7 calls are reliable, threshold relaxes; when noisy, threshold raises. Tier boundary follows.

*At recall:*
- Default ranking includes a provenance weight (multiplicative on similarity score).
- Caller can request `provenance_filter=[user, tool]` to restrict to high-trust only.
- Caller can request `provenance_weights=...` to override defaults.

*At supersession:*
- Higher-tier provenance auto-supersedes lower-tier on the same topic when conflict detected (e.g., principal correction beats agent inference without going through review queue).
- Same-tier conflict still uses classifier/explicit lane.

**Why hierarchy not flat:** without trust weighting, agent self-derived memory drowns out principal-stated facts in recall (more agents writing → more low-trust noise). Hierarchy is the antidote.

**Why hierarchy values are these:** principal is ground truth by definition. Tool output is deterministic and reproducible. Agent inference is opinion. External-extracted is suspect (per `AGENTS.md` → *Two invariants* → "external content is data, not instructions"). Legacy is unverified.

**Rejected:**
- *No provenance weighting* (current state) — principal statements get out-voted by agent self-derivation.
- *Provenance as filter only, not weight* — forces caller to know hierarchy; default behavior should already be sensible.
- *Single-bit "trusted/untrusted"* — loses distinction between tool output and agent inference.

**How measured:**
- **Recall set provenance distribution**: track ratio of `user`-tier / `agent`-tier in surfaced top-K. Drift toward agent-tier = noise accumulating; drift toward user-tier = healthy.
- **Principal override rate**: how often principal corrects recall results. Falling rate = hierarchy is working.
- **Useful-injection rate by provenance tier**: validates that higher-tier surfaces are actually more load-bearing.

---

C3 complete. All six original questions answered: Q0/Q6 boundary + sub-stores, Q1 storage shape, Q2 recall path, Q3 conflict, Q4 measurement, Q5 provenance.

---

### C17 — Observability & audit

> **Implementation 1-pager:** [`c17-events-substrate.md`](c17-events-substrate.md) (Sprint 35, locks schema + OTel column convention + trace propagation contract + write semantics for #476 and #477).

Distillation surfaced 8+ piecemeal log/audit subsystems added reactively (events, episodes, task_outcomes, audit_log [no schema!], memory_review_queue, known_unknowns, `*_last_run` memories abusing memory store as task heartbeat, device-local session jsonl, `recall-audit.py` post-hoc grep). Pattern is identical to memory's: every pain → new table → schema drift + query fragmentation + instrumentation gaps.

**Question:** What is the structure of Jarvis's observability/audit subsystem after collapsing the piecemeal pile?

**Membership rule:** something belongs in C17 iff *append-only* AND *time-stamped* AND *actor-attributed* AND *recording an action / state-change / anomaly that occurred*. Failing any → relocate.

**Things that don't belong here (and where they go):**
- Memory of insights / what Jarvis learned → C3.
- Current state (running budget, active tasks, last-run markers) → transient state / C13.
- Configuration → C1.
- Workflows that need updates (queue items pending action) → kept as queues (e.g., `memory_review_queue` keeps that role); queue *operations* emit to C17.

#### Substrate

**Decision:** ONE canonical `events` table. Everything observability writes goes through it. Other "tables" (decisions, outcomes, audit_log, known_unknowns, last_run, episodes) become **views** over the canonical events table. No new tables added per pain.

**Core schema (logical):**
- `event_id` (uuid, pk)
- `trace_id` (uuid) — groups related actions
- `parent_event_id` (uuid, nullable) — nesting (subagent's events point to spawning event)
- `ts` (timestamptz)
- `actor` (text) — `jarvis-main`, `jarvis-subagent-<id>`, `hook-<name>`, `task-<name>`, `user`
- `action` (text) — `tool_call`, `decision_made`, `memory_write`, `memory_recall`, `error`, `compaction`, `cost_charge`, `hallucination_suspected`, etc.
- `payload` (jsonb) — type-specific
- `outcome` (enum: success | failure | timeout | partial)
- `cost_tokens` (int, nullable) — for token-incurring events
- `cost_usd` (numeric, nullable)
- `redacted` (bool) — secret redaction applied

**Why one table not many:** views are cheap; tables drift. Past pain (audit_log declared in code but not in schema; `*_last_run` memories abusing C3) was the cost of denormalizing observability.

**Why these fields:** `trace_id` + `parent_event_id` = OpenTelemetry-style propagation, the only known way to reconstruct nested-agent activity. `cost_*` fields integrated (not separate ledger table) so C13 Budget reads views directly.

#### Trace propagation

**Decision:** every initiating context creates a `trace_id`; downstream actors inherit.

- Principal message → new trace_id
- Scheduled task fire → new trace_id
- Subagent spawn → inherits parent's trace_id; `parent_event_id` = the spawning event
- Hook fire → inherits trace_id of the session/task it fires in
- Cross-agent handoff (mailbox-style) → trace_id flows with the handoff payload

**Why:** without trace_id propagation, subagent activity is opaque to parent (current pain). Replay of "what happened in this delegation" requires walking the trace.

#### Subagent visibility

**Decision:** subagents emit events to the same canonical substrate using inherited trace_id. Parent reconstructs subagent activity by querying `events WHERE trace_id = X`.

Subagent transcripts (raw jsonl) remain device-local for fidelity, but **structured events are the cross-device source of truth**.

This closes the "subagent fabrication" gap (no automated detection currently) by making subagent action stream observable, queryable, and verifiable against actual side effects (e.g., comparing claimed-edits events against `git diff`).

#### Cost ledger

**Decision:** cost-incurring events carry `cost_tokens` + `cost_usd` inline. Aggregations (`cost_by_day`, `cost_by_actor`, `cost_by_skill`) are SQL views. C13 Budget reads views; no separate ledger table.

**Why:** denormalizing to a separate ledger table would drift from events. Inline cost on the event is the single source of truth.

**Closes gap:** principal upgraded to Claude Max blind to consumption (`max_20x_upgrade_available`). With ledger views, real-time spend visibility is one query away.

#### Self-detection

**Decision:** specific `action` values reserved for failure-mode signals. Detection happens in:
- **PostToolUse hooks** — compare claimed effect vs reality (e.g., "subagent reports 3 files edited" vs `git diff` shows 0 → emit `hallucination_suspected`).
- **Tool wrappers** — emit `tool_returned_empty` when a tool succeeded but returned 0 rows in a context expecting data.
- **Periodic anomaly checks** (sample over events) — emit `anomaly_flagged`.
- **System events** — `compaction_event`, `rate_limited`, `api_failure` emitted by infrastructure when detected.

**Why explicit event types:** principal-facing dashboards and reflection (C5) need to query failure modes by category, not text-search payloads.

#### Migration / what dies

- `events` table → kept and extended to canonical schema.
- `task_outcomes` → view over events.
- `episodes` → reframed as events with action='episode_*'; table folded.
- `audit_log` (currently ad-hoc, not schematized — exact class of #326 schema-drift) → view; eliminated as stand-alone class.
- `known_unknowns` → view (events where action in `recall_failed`, `tool_returned_empty`).
- `*_last_run` memories → **dropped**, replaced by view `last_run_by_actor`. Ends abuse of C3 as task-heartbeat store.
- `memory_review_queue` → keeps queue role (workflow, not log); queue operations emit events.
- Device-local session jsonl → kept for raw fidelity; significant actions emit events to canonical store. Cross-device replay no longer requires syncing jsonl.
- `recall-audit.py` post-hoc jsonl grep → reads canonical events instead.

#### How measured

- **Trace coverage** — % of significant agent-actions with corresponding events. Target: ≥99%. Sampled audit cross-checks session jsonl against events.
- **Trace-id propagation** — % of subagent events with parent's trace_id. Target: 100%. Orphaned trace_id = bug.
- **Self-detection precision** — when `hallucination_suspected` / `tool_returned_empty` fires, validation rate (manual spot-check sample). Maintains principal trust in the signal.
- **Cost-ledger drift** — ledger total vs Anthropic billing reality. Target <2% drift. Monthly reconciliation.
- **MTTR (Mean-Time-To-Reveal)** — interval between an actual malfunction and its surfacing as an event. Lower better. Critical for autonomy=8.

#### Rejected

- *Many specialized tables* (current state) — schema drift, query fragmentation, instrumentation gaps.
- *Local-only session jsonl as primary log* — no cross-device replay, no aggregation, principal can't audit autonomous-while-sleeping work.
- *Subagent transcripts harvested post-hoc* — too late for in-flight observability, lossy.
- *Cost ledger as separate table* — denormalization cost > query simplicity benefit.
- *Hand-rolled trace propagation per skill* — drift inevitable; OpenTelemetry-style ID inheritance is the established pattern.

#### Cross-capability dependencies

- **C16 Verification** reads events by trace_id to evaluate Jarvis's work — substrate enables automated review.
- **C5 Reflection** consumes events for periodic synthesis — raw episodes → lessons.
- **C13 Budget** reads cost views.
- **C3 Memory** writes emit events (bridges write-side observability gap).

---

C17 complete. Substrate, trace propagation, subagent visibility, cost ledger, self-detection, migration, measurement.

---

### C5 — Reflection / learning

Distillation: 5+ reflection mechanisms exist (/reflect skill, /end behavioral, A-MEM evolution, consolidation, FoK batch, calibration view), all uncoordinated. **None have an autonomous mutation arm** — /reflect renders markdown for principal to manually `memory_store`. The "challenge stale" role doesn't exist: Step 8 of /reflect lists 14d-untouched memories without challenging them. The most successful reflection event in 2 months (`reflection_driven_sprint_2026_04_23`) was *principal mining outcome logs by hand* — exactly what C5 must do autonomously.

**Question:** What is the structure of Jarvis's reflection/learning subsystem so the two L1-committed roles (synthesize new + challenge stale) actually happen autonomously?

**Membership rule:** belongs in C5 iff *reads from C17 events* AND *proposes mutations to C3 memory* AND *operates on a population of memories/events, not single writes*. Single-memory direct writes from skills are C3 writes, not C5.

**Things that don't belong here:**
- Principal-facing markdown render of "what happened" → C12 reporting (separate from learning).
- Single-memory writes from skills → C3 direct API.
- Outcome recording (event capture) → C7/C17.

#### Substrate

**Reads:** events (C17) — decision_made, error, principal_correction, outcome_recorded, anomaly_flagged, hallucination_suspected.
**Writes:** C3 memory (new facts, supersession links, confidence updates) via the canonical Postgres function (single funnel from C3-Q2). Also emits its own events to C17 (`judgment_made`, `mutation_proposed`, `stale_challenge_fired`, etc.) so reflection itself is observable.

**No separate "reflection" tables.** Current `consolidation_plan_*` and `evolution_plan_*` memories abuse C3 as a planning store; v2 represents plans as events (ephemeral, audit-trailed, not facts).

#### Triggering

**Decision: event-triggered primary, sweeps as backstop, principal-invoked as override.**

| Lane | Trigger | Handler |
|---|---|---|
| Event-triggered (primary) | `outcome_recorded` | Calibration handler (Brier update on judges) |
| | `principal_correction` | Stale-challenge on related beliefs |
| | `anomaly_flagged` rate spike | Policy re-examination |
| | N decisions since last synthesis | Pattern extraction |
| | `recall_failed` (FoK) | Known-unknown logging + sampling |
| Sweep (backstop) | Weekly: memories not touched 30+ days | Sample-based stale-challenge |
| | Quarterly: deeper re-examination on long-held beliefs | Full stale-challenge |
| Principal-invoked | `/reflect` command | All handlers run on demand |

**Why event-triggered primary:** pure cron is fragile (distillation: scheduler may not run; A-MEM gated on classifier UPDATE → if classifier never writes, evolution never fires). Event-triggering = `no_deterministic_pipelines` + `Memory-driven autonomy` op policies.

**Why sweeps still exist:** rare beliefs get no events; backstop ensures nothing fossilizes silently. But sweeps are sample-based (cost-bounded), not exhaustive.

#### Generation arm — synthesize new

Events → pattern detection → candidate memories. LLM extracts patterns over event windows.

Three-lane apply (mirrors C3 supersession pattern):
1. **Confidence ≥ threshold AND judge class-precision validated** → auto-write to C3.
2. **Below thresholds** → review queue with principal CLI loop.
3. **Principal correction** of an applied write = ground-truth label feeding the judge calibrator.

Per C17 migration, the standalone `episodes` table is folded into the canonical events table with `action='episode_*'`. C5 synthesizer reads these events on schedule; what's new in v2 is the *extractor* (currently absent) that turns episode events into candidate memory writes.

#### Stale-challenge arm — the missing piece

**This is the role the current system entirely lacks.** Detection-only is not enough.

Triggers (in priority order):
1. **Principal statement on a topic similar to existing memory** — principal says Y, memory says X on topic T. Handler asks LLM: "compatible, evolved, or contradiction?" → propose update/supersession via Q3 conflict resolution.
2. **Decision failure** — `outcome_recorded` with negative outcome → trace back to memories that informed the decision (via `memories_used` in `record_decision`) → re-examine those memories against recent evidence.
3. **Anomaly pattern** — repeated `hallucination_suspected` events on related actions → re-examine policies/heuristics that produced them.
4. **Sweep backstop** — sample old (90+ day untouched) memories; LLM judges "still valid given recent events on this topic."

Outcomes from re-examination:
- **Still valid** → update `last_examined`, no mutation.
- **Evolved** → propose supersession with new memory.
- **Contradicted** → propose supersession or principal-confirm.

Closes the `reflection_driven_sprint` gap — principal-mined lessons should now surface autonomously.

#### Judge calibration loop

Every internal judge (Phase-2 classifier, FoK verdict, consolidation MERGE, evolution UPDATE, stale-challenge re-examination) emits a `judgment_made` event with claim + confidence. Ground-truth labels arrive later (principal override, decision outcome) and link back to the judgment.

**Periodic Brier per judge.** Threshold updates auto-derived: judge whose precision drops below floor has its auto-apply threshold raised (or auto-apply suspended). Self-correcting — closes the `DEFAULT_CONFIDENCE_GATE = 0.85` symptom-fix (universal threshold without calibration data).

#### Coordination

**One dispatcher routes events to handlers; handlers are independent.** Handlers: synthesizer, stale-challenger, calibrator, FoK, anomaly-investigator. Each subscribes to specific event types, emits its own observability events, and uses the canonical C3 API.

Why specialized handlers under one dispatcher (not monolithic /reflect): different roles have different cost/cadence/precision profiles. The current `/reflect` 9-step kitchen-sink (per `verify_skill_not_reflect` decision) collapses to one job per handler.

#### Cost bounding

C13 allocates per-handler monthly budget. Handler hits cap → backs off (sample rate down, queue accumulates, or skip non-critical work). C13 enforces; C5 self-throttles via emitted events.

Concrete: most handlers use Haiku (cheap); only stale-challenge re-examination of high-stakes beliefs may use Sonnet, **capped at 5 Sonnet escalations per week** for the stale-challenge handler (rest defer to next week or principal queue). C13 enforces.

#### Migration

- `/reflect` skill → principal-invoked entry; **most logic moves to autonomous handlers**. Skill becomes "fire all handlers now + render summary."
- `/end` behavioral reflection → principal-correction events trigger C5 stale-challenge directly. Skill becomes thinner.
- A-MEM evolution → integrated into stale-challenge arm (tag/desc drift is one signal among many).
- Consolidation → integrated into generation arm (cluster detection → merge proposal).
- FoK batch → calibration arm with proper schedule (currently unregistered per distillation).
- `consolidation_plan_*` / `evolution_plan_*` memories → **DROPPED**; represented as events. Closes the C3-abuse class.
- Hypothesis tracking → ambient: `hypothesis_made` event triggers stale-challenger when related evidence arrives, instead of resolving only inside /reflect.

#### How measured

- **Mutation arm activity** — memories created / superseded / confidence-updated by C5 per week. Zero = mutation arm broken.
- **Principal override rate** of C5 mutations. Falling = handlers calibrated. Rising = drift.
- **Stale-challenge yield** — re-examined memories: update vs still-valid ratio. Indicates whether stale-challenge is finding real staleness or thrashing.
- **Judge Brier trends** — per-judge precision over time. Should be flat or improving.
- **Event coverage** — % of significant event types subscribed by at least one handler. Gaps = unmonitored failure modes.
- **Cost-vs-cap** by handler — actual spend against C13 allocation.

#### Rejected

- *Principal-facing markdown only* (current /reflect) — no autonomous mutation = role unmet.
- *Pure cron triggers* — fragile (current pain).
- *Monolithic reflection engine* — different roles have different needs; collapsing them produced the kitchen-sink /reflect.
- *Auto-apply without calibration* — universal threshold without measurement was a symptom-fix.
- *Separate plan/report tables* — current C3 abuse via `consolidation_plan_*` memories.

---

C5 complete. Substrate, triggering, generation arm, stale-challenge arm, judge calibration, coordination, cost bounding, migration, measurement.

---

### C18 — Proactive challenger

> **Implementation 1-pager:** [`c18-proactive-challenger.md`](c18-proactive-challenger.md) (Sprint 36, locks drift-signal taxonomy + threshold YAML schema + `recalibration_proposed` event shape + surfacing routing + bootstrap protocol + false-positive calibration loop for #C18.2 and #C18.3).

**Question:** Who notices when Jarvis's calibration drifts, when goals go neglected, or when stated direction doesn't match observed behavior — and surfaces it before the principal has to catch it?

**Membership rule:** belongs in C18 iff *detects gaps between stated and observed* AND *surfaces to the principal without mutating memory*. Memory mutation = C5. Surfacing = C12. C18 is the detection layer that decides *when surfacing is warranted*.

**Why it's not C5:** C5 mutates state — synthesizer writes new memories, stale-challenger supersedes old ones. C18 only observes and routes to C12. Folding it into C5 conflated "fix the belief" with "tell the principal a recalibration is needed". Different blast radii: C5 changes the system; C18 changes what the principal sees.

**Why it's not just C12:** C12 is the comm channel. C18 decides *which patterns are worth surfacing*. Putting the detection inside C12 makes C12 carry judgment that doesn't belong with a transport layer.

#### Detection signals (input)

C18 reads from C17 events and C2 goal state:

| Signal | Source | What it indicates |
|---|---|---|
| Calibration drift | C5 calibrator outputs (Brier per memory type, per-class FP/FN) | Memory class confidence has decayed; principal's mental model may be stale |
| Goal neglect | C2 `goal_stale` events crossed N day threshold without activity | Stated priority isn't matching observed work allocation |
| Direction-vs-action gap | `decision_made` events grouped by `goal_slug` vs principal-stated priority | Jarvis is working on P2 while P0 is starving |
| Repeated principal override | C16 `principal_override` events on a recurring class | Jarvis's classifier disagrees with the principal systematically — recalibration needed |
| Stale assumption | Memory `confidence < 0.5` AND last-used > 90 days | Belief Jarvis still acts on but no longer has evidence for |

#### Surfacing decision (output)

C18 emits a `recalibration_proposed` event when the gap exceeds threshold. C12 picks it up and routes to the principal in the next batched brief — never as a real-time interrupt (this isn't urgent; this is "you should know").

Surfacing format (rendered by C12):
> Calibration check: `decision` memories in jarvis are overconfident (Brier 0.31, target <0.20). Pattern: heavy reliance on research memories without principal confirmation. Suggest: `/reflect` pass on last 10 decisions, or change the always-load rule.

#### Interaction with C5

- C5 detects pattern → mutates memory (e.g. supersede stale belief).
- C18 detects pattern → surfaces to principal (e.g. "your stated P0 hasn't been touched in 14 days; either re-prioritize or update the goal").

If both fire on the same signal, C5 mutates first, C18 surfaces the mutation to the principal. They share the input substrate but the outputs are orthogonal.

#### Cost bounds

Pure C17-event reads + threshold checks. No LLM calls in the hot path. Optional Sonnet pass to phrase the surfacing message readably; capped at 1 LLM call per `recalibration_proposed` event, max 5 events surfaced per batched brief.

#### Migration

- New code; no current implementation to migrate.
- First version: pure SQL queries over C17 events + `goals` table, emitted via existing batched brief plumbing (C12 already aggregates).
- L3 detail: detection thresholds tuned per signal class.

#### How measured

- **Surfacing acceptance rate** — % of `recalibration_proposed` events the principal acts on (re-prioritizes goal, runs `/reflect`, edits the rule) vs dismisses. Target: >50% (otherwise C18 is noise).
- **Pattern-discovery latency** — time from drift signal first detectable in C17 to first `recalibration_proposed` event. Target: <7 days.
- **False-surface rate** — `recalibration_proposed` dismissed as "not actionable" by principal. Rising = thresholds too sensitive.

#### Rejected

- *Fold into C5 stale-challenger* — conflates mutation with surfacing.
- *Real-time interrupt on first signal* — drowns the principal in noise; calibration drift is slow, doesn't need real-time.
- *No detection layer (let the principal notice)* — exactly the gap C18 closes; the principal *should not have to notice* drift in their own AI peer.

---

C18 complete. Detection signals, surfacing decision routed to C12, no memory mutation, calibration-loop-aware.

---

### C6 — Decision gating

Distillation: enforcement is **scattered across 3 places that drift** (SOUL prose, `agents/safety.py` Tier model, skill §7.5 prose). `safety.py` only covers action-agent paths (~5% of decisions); conversational lane is ungated. Stakes are state-dependent (`gh pr merge --delete-branch` is Tier 0 normally, irreversible on a stack root — hit twice). No convergence detector (6 iterations before principal stepped in). Tier 1 principal queue is documented but the table doesn't exist; "ask principal" today = unstructured chat message. `record_decision` is post-hoc, doesn't gate, has the same compliance problem as the rules it logs.

L1 already committed to the escalation matrix. L2 specifies how it's implemented as one canonical gate that's actually called from every action path.

**Question:** What is the structure of Jarvis's act/ask gate so the L1 matrix is uniformly enforced and measurably calibrated?

**Membership rule:** belongs in C6 iff *classifies an intended action before commit* AND *outputs one of {act, log-and-act, explain-and-act, queue, escalate, block}*. Operates pre-action, not post-hoc.

**Things that don't belong here:**
- Post-hoc decision logging → C7 + C17 events (gate output emits these, but they're not C6).
- Verification of completed work → C16.
- Memory of past decisions → C3.
- Principal notification UI → C12.

#### Single canonical gate

**Decision:** ONE `gate()` function in a canonical location, called from PreToolUse hook for **every** tool call regardless of lane (conversational, action-agent, skill, scheduled task, subagent). Returns: `PROCEED` | `LOG_AND_PROCEED` | `EXPLAIN_AND_PROCEED` | `QUEUE` | `ESCALATE` | `BLOCK`.

Eliminates the 3-way drift (SOUL prose + safety.py + skill §7.5). Skills and SOUL describe **policy**; the gate is the only **enforcement**. SOUL prose becomes documentation of the gate's behavior, not a parallel rule.

**Why one gate not many:** drift is the documented current pain. Single function = single source of truth.

**Why PreToolUse hook:** that's the harness chokepoint where every tool call is observable before commit. Conversational lane currently bypasses `safety.py`; PreToolUse fixes that.

#### State-aware classification

**Decision:** gate inputs include action AND state, not just `(tool, action, target)`.

Inputs to `gate()`:
- Action: `(tool, action, target, payload)` — current narrow input
- **Git state**: dirty worktree? stacked-PR root? protected branch? unmerged children?
- **Convergence state**: N attempts on this topic in this session?
- **Memory recall** on topic: principal preference known?
- **Cost class**: token-expensive? blocks principal?
- **Harness restrictions**: does the harness block this action class? (e.g., Claude Code blocks `.claude/*` edits — `claude_dir_edits_need_manual_confirm`)

State queries are LIVE at gate time, not snapshots. Each state probe is itself an event in C17 (cheap; lets us audit *what the gate knew* when it decided).

**Why state-aware:** stacked-PR class hit twice with the same `(tool, action, target)` because state was invisible. Static whitelist can't fix this.

#### Convergence detection

**Decision:** per-session counter on `(topic_hash, action_class)`. Counter increments each attempt without confirmed progress.

**`topic_hash` definition:** stable hash over the smallest disambiguating context — for code work: `(file_path, function_or_symbol)`; for issue work: `(repo, issue_number)`; for memory work: `(memory_name)`; for research: hash of the question text normalized. The hash function is fixed per action class (config), so same problem produces same hash across attempts within a session.

Thresholds:
- **3 attempts, no progress signal** → emit `convergence_stall` event → C5 stale-challenge handler fires (the rule isn't working — re-examine) AND C6 force-escalates next attempt.
- **5 attempts** → BLOCK until principal intervenes.

"No progress" = no `outcome_recorded(success=true)` linked to this topic since counter started.

**Why structural counter not memory:** `step_back_after_3_failed_iterations` lives as recallable memory; recall is probabilistic. A counter is deterministic — fires every time.

#### Real Tier 1 queue

**Decision:** structured queue table, not chat messages.

Queue item: `(action_id, classification, payload, gated_at, decided_at, decision, reasoning)`. Status enum: `pending → approved | rejected | superseded | expired`.

Principal-facing review:
- Batched morning brief lists pending items, sorted by reversibility (irreversible first).
- Bulk approve/reject with rationale (rationale becomes a feedback memory + classifier label).
- Items expire if not decided in N days (configurable per-class). **Irreversible-class items NEVER expire silently** — when N elapses, the item gets re-flagged with higher urgency in the next batched brief and stays in queue. Principal explicit decline is the only way out.

Closes the documented-but-unbuilt gap (`action_agent_safety_gate_model_v1` queue piece).

#### Symmetric calibration

**Decision:** track BOTH failure modes of the gate, not just over-permissive.

Two outcome types feed gate calibration:
- **`gate_overpermissive`** — principal reverts/corrects an action the gate let through. Logged via principal-correction or revert detection.
- **`gate_overcautious`** — principal approves a queued item with annotation. **Channel:** queue UI (the batched-brief item that principal reads to approve) presents two approve-buttons: "approve" and "approve + 'should have been autonomous'". Single-click labelling, no implementation review required. Principal provides label by their choice of approve action, not by reading code.

Both update per-class precision/recall. Threshold for that class auto-adjusts (more cautious if over-permissive trend; less cautious if over-cautious trend).

**Closes:** `decision_shedding_via_open_questions` showed 5/6 questions were over-cautious — principal caught it manually. v2: tracked structurally, threshold self-corrects.

#### Real-time gating, not post-hoc logging

**Decision:** `record_decision` is **emitted automatically by the gate** when a decision is made. No manual `record_decision` call required.

`record_decision_during_refactors` and `record_decision_during_session_not_at_end` are compliance failures of post-hoc logging. Gate emits them as a side effect of classifying the action. Compliance becomes 100% by construction.

#### Harness awareness

**Decision:** harness restrictions (Claude Code blocking `.claude/*` edits, etc.) are a config consulted by gate.

When gate sees an action the harness will block, it pre-emptively `QUEUE`s rather than `PROCEED`s — saves the cost of a failed attempt. Config seeded from memories (`claude_dir_edits_need_manual_confirm`).

**Discovery loop:** new harness blocks emerge dynamically. Gate emits `harness_block_observed` event when an action fails with a harness-rejection signal; the config update path is **C15 M2** (collaborative — principal present), not M3 (fully protected). Adding new entries does not require schema/SOUL touches, so it's not in the highest-protection class. This avoids the "config goes stale because it's protected" pitfall.

#### Boundary with C16

C6 = before action (act/ask classifier). C16 = after action (review of completed work). Different time, different role. Both contribute to autonomy track record (C17 events).

#### Migration

- `agents/safety.py` Tier model → kept as the rule store; gate() consumes it. Tier definitions migrate from hardcoded Python to config (still rule-based, but inspectable).
- SOUL.md autonomy section → documentation of gate behavior, not parallel rules.
- Skill §7.5 risk policies → migrated into per-action-class entries in the rule store; skills consult the same gate.
- `record_decision` MCP tool → kept for principal-explicit decisions; auto-emission from gate covers the rest.
- Tier 1 queue → finally implemented as a real table (currently `queued=True` flag with no actual queue).

#### How measured

- **Gate coverage** — % of tool calls that passed through `gate()`. Target: 100%. Gaps = unenforced lanes.
- **Per-class precision/recall** — over_permissive vs over_cautious rates by action class. Calibration spine.
- **Convergence-stall trigger rate** — how often the 3-attempt threshold fires. Drop in rate = topics resolving faster (or detector dead).
- **Queue latency** — time from `QUEUE` to principal decision. Long latency = principal-bottleneck pain.
- **Queue approval rate** — what fraction of queued items get approved. Low rate = gate is over-queueing.
- **Drift detection** — periodic comparison of gate decisions on same `(action, state)` over time. Drift signals miscalibration.

#### Rejected

- *Multiple parallel rule stores* (current state) — drift inevitable.
- *Action-only classification (no state)* — can't catch state-dependent reversibility.
- *Memory-based convergence detection* — probabilistic; deterministic counter wins.
- *Chat-message-per-ask* (current state) — principal-bottleneck, unstructured.
- *Manual `record_decision`* — compliance always degrades; auto-emit by gate is the fix.

---

C6 complete. Single canonical gate, state-aware classification, structural convergence detection, real Tier 1 queue, symmetric calibration, real-time gating with auto-emission of decision records, harness awareness.

---

### C16 — Verification / QA

Distillation: reviewer agent **doesn't exist** — L1 commitment is stated intent, not practice. Current review = orchestrator self-reviewing (same agent that delegated; not independent). Subagent fabrication caught only by manual `git diff`; documented in 4+ recent incidents. Copilot is quota-dead on jarvis right now, so the documented "check Copilot before merge" gate is unenforceable. Different-provider review never attempted. No FP/FN tracking exists.

C16 is essentially build-from-scratch. The L2 design specifies what the reviewer subsystem looks like.

**Question:** What is the structure of Jarvis's verification subsystem so that principal stays out of code review while quality holds?

**Membership rule:** belongs in C16 iff *evaluates produced work* AND *can block its acceptance* AND *operates independently of the producer*. The independence requirement is non-negotiable: orchestrator self-review fails it.

**Things that don't belong here:**
- Pre-action gate → C6.
- Outcome tracking → C7 + C17.
- Self-detection during action → C17 self-monitoring.
- Pattern-extraction across many reviews → C5 reflection (consumes C16 outputs).

#### Specialized reviewers, not monolith

**Decision:** multiple reviewer roles, each narrow and independently calibratable.

| Reviewer | What it checks | Mechanism |
|---|---|---|
| **Diff coherence** | Claimed edits (from C17 subagent events) match actual git diff | Deterministic (no LLM) |
| **Test coverage** | New symbols have positive tests, not just regression | Deterministic (AST + test discovery) |
| **Logical correctness** | Code does what it claims; no obvious bugs | LLM, narrow prompt |
| **Goal alignment** | PR matches linked issue / stated goal | LLM, medium |
| **Interaction effects** | Downstream consumers of changed interfaces still work | LLM + dependency trace |
| **Cross-device integrity** | Paths, configs, secrets work on all 3 devices | LLM + simulation per device profile |
| **Smoke test** | Post-merge: runs the changed code path with seed inputs in a clean throwaway environment (subprocess + temp dir for scripts; ephemeral container for services); asserts no crash + expected outputs | Runtime test runner, not LLM |

**Why specialized:** each category has clear evaluation criteria (mechanical or LLM-with-narrow-prompt). Monolithic reviewer with broad prompt produces unfocused output and resists calibration. Per-reviewer FP/FN tracking is only meaningful when each reviewer has one job.

**Why mechanical reviewers exist alongside LLM ones:** diff coherence and test coverage are not judgment calls — they're verifiable facts. Spending LLM tokens on them is waste; making them deterministic gives a hard floor that LLMs can't compromise.

#### Same-model vs different-provider

**Decision:** mechanical reviewers are deterministic. LLM reviewers default to peer-Jarvis (same provider, different session). **High-leverage class (per C6) mandates different-provider** for at least one LLM reviewer in the bundle.

"High-leverage" trigger is **a concrete checklist** computed at C6 gate time, any one matching → high-leverage:
- Schema migration (`*.sql` changes, schema-touching code).
- Cross-project shared code (M3 list per C15).
- Gate / reviewer / observability subsystem itself (C6/C16/C17 code).
- Security-tagged path (`scripts/secret-scanner.py`, `agents/safety.py`, credential registry, `.env*` adjacency).
- API contract change (public function signature, MCP tool schema, exported types).

For these, run logical correctness reviewer twice: once peer-Jarvis, once different-provider (OpenAI/Gemini). Disagreement = automatic principal escalation via C12. Expected rate: ~25% of PRs (C13 cost rehearsal anchor).

**Why not always different-provider:** $100–200/mo cap. Per-PR different-provider review is too expensive for routine changes.

**Why not always peer-Jarvis:** same-model bias. Two Jarvis instances may share blind spots; different-provider provides genuine independence on high-stakes changes.

#### Subagent fabrication is first-class

**Decision:** diff coherence reviewer runs **before any other reviewer**. If subagent's claimed edits (from C17 events) don't match git diff, BLOCK with `subagent_fabrication_detected` event — no further review attempted, escalate immediately.

This closes the documented incident class (redrobot #640/#647, jarvis #688/#700) where logical-correctness reviews would have run on phantom edits. Cheap to implement, high-leverage to have.

#### Authority

**Decision:** every reviewer can BLOCK merge by setting check status fail. Reviewer is not advisory.

- Mechanical reviewers: block is final (deterministic facts).
- LLM reviewers: block carries reasoning + confidence; principal can override via C12 (override is a calibration label).
- Reviewer disagreement with orchestrator → C6 escalation lane (multiple viable interpretations → principal picks).

#### Triggers

| Trigger | Reviewers fired |
|---|---|
| PR open | Diff coherence + test coverage + logical correctness + goal alignment |
| PR touches shared interface | + Interaction effects |
| PR touches config/paths/scripts | + Cross-device integrity (capped: only PRs touching `.mcp.json`, `config/`, `scripts/`, OS-shell scripts; ~6 PRs/mo expected) |
| PR classified high-leverage by C6 | + Different-provider logical correctness (independent run) |
| Post-merge | Smoke test (high-leverage only at first; broader as trust ladder permits) |
| Periodic (weekly) | Cross-device integrity sweep across recent merges |

#### Principal surface — class-aware auto-merge

**Decision:** PRs split into two classes by leverage; class determines whether the principal sees the PR at all.

| Class | What | Principal surface |
|---|---|---|
| **Auto-merge** | Typo / doc-only changes, behavior changes in low-leverage scope | None by default. Merges automatically when **all three** hold: (a) C16 diff-coherence pass, (b) tests pass, (c) every triggered reviewer green. Surfaces in batched brief as a one-line "merged" entry, not as an action. |
| **Principal gate** | High-leverage class — schema (`*.sql`), cross-project shared code (per C15 cross-project list), hook/gate/reviewer/observability code (C6/C16/C17), security-tagged PRs, ANY reviewer block (even warning) | Mandatory principal review. Aggregator summary + drill-in available. Merge blocked until explicit principal action. |

**Classifier:** reuse C6 high-leverage classifier (already exists for different-provider review trigger). Don't create a parallel classification — one classifier, two consumers.

Summary shape (for the principal-gate class):
> PR #123 — feat: X. Linked: #100. Class: **principal-gate** (touches `mcp-memory/schema.sql`). ✅ aligned with goal. ✅ tests cover new symbols. ⚠️ logical-correctness flagged: <one-line concern>. Recommend: review concern OR merge.

Auto-merge class produces a thinner summary (one line in batched brief). Routine work has zero principal touch by default; the principal sees high-leverage work and reviewer-flagged work.

**Why class-aware:** flat "all PRs aggregate to principal" surface either bottlenecks on routine doc PRs or trains the principal to rubber-stamp. Class split means routine work shipped autonomously and the principal's attention reserves for the cases where it matters. Closes the principal-bottleneck side of `caution_vs_overconfirmation_principle`.

#### Cost bounds

- Mechanical reviewers: free (no LLM).
- Single LLM reviewer per category: Haiku first pass; Sonnet on suspicious flags only.
- Different-provider only on high-leverage class.
- Per-PR cap: TBD L3; C13 enforces. Mechanical floor is always covered regardless of LLM cap.

#### Calibration loop

Tracked per reviewer:
- **FP rate** — reviewer blocked, principal overrode → label.
- **FN rate** — reviewer passed, post-merge incident traced back to PR → label (via C17 incident events linked to merge).

Both feed C5 calibration arm. Per-reviewer Brier; thresholds adjust per reviewer.

When FP rate climbs on a reviewer → its block authority is downgraded to advisory until recalibrated. When FN rate climbs → its scope expands or it's replaced.

#### Migration

- **`/verify` skill** → renamed and re-scoped. It's an outcome-status tracker (`gh pr view` checks), NOT verification. Move to C7/C17 layer; do not call it "verify" in v2.
- **Copilot review** → kept as one input to the aggregator (not the gate). Treated as a low-cost LLM reviewer alongside others; quota-out is no longer a blocker because it's not the primary.
- **Auto-fix workflow** → fires only after reviewers pass diff coherence; never on raw Copilot suggestions without orchestration check.
- **Orchestrator self-review** → **DEPRECATED**. Violates independence requirement. /implement and /delegate skills no longer carry their own §6 review checklists; they hand off to C16.
- **`/security-review`** → kept as specialized reviewer (security-tagged PRs) — already follows the specialized pattern.

#### How measured

- **Reviewer coverage** — % merged PRs that went through full reviewer pipeline. Target: 100%.
- **Per-reviewer FP/FN** — calibration spine. Tracked via principal overrides and post-merge incident attribution.
- **Time-to-review** — PR-open to aggregated verdict latency.
- **Subagent fabrication catch rate** — known-fabrication audit (sample post-merge for hidden cases) vs reviewer-flagged.
- **Principal override frequency** — when principal overrides reviewer block. Should be rare; rising = miscalibration.
- **Auto-merge class accuracy** — % of auto-merged PRs that the principal later flags as "should have been gated". Rising = classifier needs tightening; ~0 = classifier working. Symmetric metric for the gated class: % the principal merges without comment (overcautious classification).
- **Cost per PR by class** — average $$ spend on review by leverage class; tracks against C13 cap.

#### Rejected

- *Monolithic reviewer agent* — unfocused, hard to calibrate.
- *Orchestrator self-review* (current state) — fails independence.
- *Copilot as primary gate* — quota-fragile, misses domain bugs.
- *Advisory-only reviewers* — Copilot pattern, not effective at the act/ask level.
- *Same-model on high-leverage* — shared blind spots between same-provider instances.
- *Principal reviews diff* — contradicts L0 mission (principal = stakeholder, not reviewer).

---

C16 complete. Specialized reviewers (mechanical + LLM), same-vs-different-provider triggered by C6 high-leverage, subagent fabrication as first-class gate, block authority, principal-facing summary aggregation, FP/FN calibration via C5.

---

### C15 — Self-improvement

Distillation: the highest-yield self-improvement path is *principal mining outcomes by hand* (`reflection_driven_sprint_2026_04_23`), not `/self-improve` — that skill exists but cadence and yield are unmeasured. The current protected-file whitelist (SOUL.md, CLAUDE.md, .mcp.json, mcp-memory/server.py, .env) substitutes for an absent reviewer (C16 didn't exist). Same low/medium/high tier classification lives in 3+ places and drifts (same C6 pain). Documented bootstrap incidents: `.claude/*` edit halting autonomous run; Jarvis-written CI guard silently passing because guard watched wrong path while Jarvis edited the right one; compaction-induced fabrication of "I implemented X" when nothing was done; `always_load` rules growing context until token diet was needed.

C15 in v2 is mostly *composition* of C5 (proposals), C6 (tier classifier), C16 (independent review), C17 (modification audit). The new pieces are: **modification tiers**, **bootstrap protection**, **trust ladder for self-modification specifically**, **misimprovement detection**.

**Question:** What is the structure of Jarvis's self-modification subsystem so it can improve itself without bricking its own safety mechanisms?

**Membership rule:** belongs in C15 iff *modifies Jarvis's own configuration / code / skills / hooks / schema* AND *operates as a deliberate self-improvement loop, not as side-effect of normal work*. Memory writes during task execution = C3 direct. Issue creation in workflow = C7/C12. Reflection-driven memory mutation = C5. C15 is reserved for changes to Jarvis itself.

**Things that don't belong here:**
- Hygiene/cleanup of principal repos (C7).
- Memory consolidation/evolution (C5 handlers).
- Issue creation as part of normal task flow (C12).
- Reviewer-flagged auto-fixes on non-Jarvis PRs (C16).

#### Modification tiers

**Decision:** four tiers, anchored to C6 classification but specialized for self-modification context.

| Tier | Mode | What's allowed | Trigger |
|---|---|---|---|
| **M0** | Autonomous, read-only | Self-analysis, gap detection, friction logging, proposals (issues, design docs, memory) | Always available |
| **M1** | Autonomous write | Doc-only changes, test additions, deletion of skills with measured zero-use, low-risk in non-protected scope | Trust-ladder unlocks after N successful M0 → M1 transitions |
| **M2** | Collaborative or strong-reviewed | Skill changes, code edits in Jarvis repo, hook changes, anything in `.claude/*`. Plus the **M2-strong** subset (former M3 soft) — autonomous write OK only with C16 different-provider review + cross-device smoke | Principal present in session OR explicit principal approval queued (default M2). M2-strong adds the reviewer + smoke gates. |
| **M3** | Protected — always principal-explicit, no auto-propose | The M3-hard list (below). Six items, fixed. | Never auto-applied; never even auto-proposed without explicit principal ask. |

**M3-hard list (6 items, fixed-protected):**

| Item | Why it's fixed M3 |
|---|---|
| `config/SOUL.md` | Identity axioms — self-modification = self-validating alignment. |
| `mcp-memory/schema.sql` | Cross-project blast radius (redrobot uses the same Supabase). One wrong migration = both projects broken. |
| `.env` (and `.env.*` secrets) | Secrets — never read, never write, ever. Absolute. |
| C6 gate logic | The classifier deciding what's safe — self-modifying it = bypass mechanism for everything below. |
| C16 reviewer logic | Quality gate. Self-modifying review = self-grading. |
| C17 observability schema/projections | The audit substrate every other cap reports to. Tampering breaks reflection, calibration, cost tracking simultaneously. |

**M2-strong (former M3 soft — 3 items, reclassified):**

These were previously bundled with the M3-hard list under "always principal-explicit", but the cost was wrong: they're config/code, not safety mechanisms or secrets. Reclassified as M2 with **mandatory strong review** (different-provider per C16 + cross-device smoke per C16's cross-device sweep):

| Item | Reason for reclassification |
|---|---|
| `CLAUDE.md` (project instructions) | Operational config. Routine edits (skill table updates, process clarifications) shouldn't bottleneck on principal touch. Wrong edits caught by reviewer + smoke. |
| `.mcp.json` (MCP config) | Configuration, not gate logic. Misconfiguration = MCP server doesn't load = obvious failure mode (vs silent corruption). Cross-device smoke catches portability regressions. |
| `mcp-memory/server.py` (code only — schema stays M3-hard) | Code with high test coverage and reviewer scrutiny. Schema migrations are the cross-project blast risk, not handler code. |

**Why split M3:** flat M3 put `.mcp.json` (config) next to `.env` (secrets) — false equivalence. The schema-drift incident (Jarvis-written CI guard watching wrong path, silently passing) was a `mcp-memory/schema.sql` problem — that stays M3-hard. The CLAUDE.md/`.mcp.json`/server.py group was lumped in by association, not by blast radius. Real semantic distinction now applied.

**Why M1 exists at all:** without M1 there's zero autonomous self-improvement and v2 mission ("Jarvis improves itself") fails. M1 is the only narrow autonomous lane; doc-only and zero-use deletion are genuinely safe.

#### Bootstrap protection

**Decision:** any self-modification PR that touches **C6, C16, or C17** is reviewed by the **previous version** of those mechanisms, never by the version being modified. Plus mandatory different-provider review (C16's high-leverage path).

This blocks the class of failure where Jarvis modifies its gate/reviewer/observability and that same modified mechanism approves itself. Concretely: changes to gate logic must be reviewed using the gate as it stood before the change; changes to the reviewer agent are reviewed by a frozen prior reviewer image plus different-provider.

**Why this not "freeze C6/C16/C17 entirely":** they need to evolve. But evolving them is the highest-risk class — must use the most rigorous review (prior version + different provider), not the lightest.

#### Trust ladder for self-modification

**Decision:** progression specific to C15, narrower than the global trust ladder.

- **Day 1:** M0 only. All self-modification proposals are issues/design docs/memory, no writes.
- **After K successful M0 cycles** (proposal → principal review → principal-applied with no regression in N days) → M1 unlocks for the **specific class** that demonstrated reliability. Per-class unlock, not blanket.
- **After M successful M1 applications with FP/FN within tolerance** → principal can manually elevate that class to M2 default. Not auto-elevated; M2 always remains a deliberate decision.
- **M3-hard never unlocks.** The 6-item list is fixed-protected. New M3-hard items can only be added by principal.
- **M2-strong** (former M3-soft) follows normal M2 progression — the reviewer + smoke gates are the unlock condition, not a per-class trust counter.

K and M are operational tunings (L3); the principle is per-class, evidence-based, monotonic-with-override.

#### Misimprovement detection

**Decision:** every C15 modification emits a `self_modification_applied` event in C17 with **claimed benefit** (1 sentence + measurable signal if applicable). After observation window, C5 reflection fires the misimprovement check.

Outcomes:
- **Benefit observed in metrics** → modification confirmed; class-precision label updated.
- **No benefit observed** → modification flagged as `improvement_inconclusive` (not reverted, but no further M1→M2 progression for this class).
- **Negative signal observed** (regression metrics, principal override frequency, reviewer FP/FN rises) → emit `regression_suspected` event; auto-propose revert via M1; principal decides.

**Closes:** current `/self-improve` produces PRs without measurement of whether they improved anything. Now every change has a measurable claim attached.

#### Compaction / hibernation handling

**Decision:** all self-modification state lives in **persistent artifacts** (PR, issue, design doc, events) — never relying on session memory.

After compaction, Jarvis re-reads the canonical PR/issue/event state before claiming progress. The `post_compaction_premise_verification` rule applies first to C15 work specifically: before claiming "I implemented X" in a self-improvement context, verify by reading the artifact, not by recalling.

This is enforced via a hook that triggers on compaction events for the C15 lane.

#### Cross-project shared code

**Decision:** `mcp-memory/server.py`, schema, `.mcp.json` are flagged as cross-project class. They are M3 unconditionally and require coordination with the dependent project (redrobot) before merge. Migration of this class is **never** routed through normal C15 — only through explicit cross-project change protocol.

#### Migration

- `/self-improve` skill → kept as the C15 principal-invoked entry, but pipeline rebuilds: ideation = C5 generation arm; risk classification = C6; review = C16; apply = M1/M2 by tier; measurement = C17 + C5.
- Hard whitelist of protected files → kept but renamed *M3 list*; explicitly tied to safety semantics, not "files we're scared of".
- Risk classifier (3-tier in 3 places) → consolidated under C6 (single canonical gate).
- `/self-improve` Step 8 (orchestrator self-review of self-improvement PRs) → **deprecated**; C16 reviews instead, with bootstrap rule for C6/C16/C17 modifications.
- `/repo-improve` deferred → unblocked once C16 ships (the dependency that motivated the deferral).
- Autonomous-loop scoring weights → exposed as M2 config (was: hardcoded in SKILL.md), so they can be tuned via collaborative mode.

#### How measured

- **Per-class M1 application precision** — `improvement_inconclusive` rate vs `confirmed` rate. Drives trust-ladder progression.
- **Misimprovement detection rate** — `regression_suspected` flags raised before principal notices vs after. Lower-is-late = bad.
- **Bootstrap-protection trigger frequency** — modifications that hit the prior-version-reviewer rule. Should be rare (most self-improvement avoids C6/C16/C17); spikes = something is wrong.
- **M0 → M1 promotion latency** — how long it takes for a class to earn autonomous write. Unbounded latency = trust ladder dead.
- **Compaction-induced fabrication catch rate** — instances where post-compaction verification rejected a fabricated "implemented" claim.
- **C15 cost vs cap** (C13) — self-improvement budget consumed.

#### Rejected

- *Single risk gate copied across self-improvement* (current state) — drift, no calibration.
- *Same C16 reviewer reviewing changes to itself* — fails the bootstrap protection rule.
- *Hardcoded protected list as the only safety* — substitutes for review; new safety-critical files emerge and aren't covered.
- *Trust ladder applied as one global policy* — self-modification has different risk profile than user-task autonomy.
- *Auto-revert on regression suspicion* — too aggressive; principal-decision via M1 propose preserves judgment.
- *Allow M3 to unlock autonomously after enough successes* — the failure mode (autonomous gate self-modification) is catastrophic and rare; never trade it for cadence.

---

C15 complete. Modification tiers (M0–M3) anchored to C6, bootstrap protection for C6/C16/C17 changes, trust ladder per-class with evidence gating, misimprovement detection via measurable claims, compaction-aware artifact-based state, cross-project shared-code class.

---

### C2 — Goals & priorities

Distillation: schema is decent (11 real goals, lifecycle works); the bottleneck is **enforcement and feedback loops**. `progress_pct` is manual and lies. `jarvis_focus` field has rotted into an append-only journal. Stale detection lives only in skill prose. Goal-decision linkage is aspirational prompt-engineering — `record_decision` has no `goal_slug` field at all; `outcome_record.goal_slug` is optional and unvalidated. Hierarchy is mechanical but no rollup.

**Question:** What is the structure of C2 so goals actively rank work and stale ones retire?

**Membership rule:** belongs in C2 iff *active strategic context* AND *queryable as source for autonomy decisions* AND *has lifecycle (created → progressed → achieved/abandoned)*. Per-task work items → GitHub issues (per `milestone_vs_pillar_hygiene`); pillars → memory; goals are the time-boxed strategic tier between.

#### Schema cleanup

- **`progress_pct` removed**; replaced with view `progress_ratio` = checked items / total in `success_criteria`. Auto-derived, can't lie.
- **`jarvis_focus` split** into:
  - `strategic_posture` — one short paragraph, current strategic stance (e.g., "blocked on principal review of v2 design").
  - `progress_log` — moved out of the goal row entirely; goal-tagged events in C17 are the journal. The goal row stops being a write-amplification target.
- **`direction` field** dropped as free-text. Multi-goal grouping handled by explicit `parent_id` chain (already in schema); no `directions` table. Free-text direction was rolled-up identical strings — chains do that semantically.

#### Stale detection as queries (not prose)

Periodic checks run as SQL views over events:
- P0 active goals with no events on `goal_slug` in 14d → emit `goal_stale` event.
- Deadline within 7d → emit `goal_deadline_soon`.
- Both feed **C12** (principal notification, batched, not interrupting) and **C5** (challenge handler — is this goal still relevant?).

Closes the gap that `/goals review` text instructions never queried anything.

#### Goal-decision linkage as first-class

- `goal_slug` becomes a **FK on every decision event** emitted by C6's gate. **Source at gate time:** gate runs a narrow C3 recall on the action's topic + active goals; if a single active goal matches semantically, it's bound automatically; if multiple match, the gate either picks the highest-priority active or escalates ambiguity. Default-required for decisions classified above a threshold (architectural / high-leverage); optional for trivial.
- `outcome_record.goal_slug` validated against `goals.slug`; orphan slugs rejected.
- `autonomous-loop` scoring formula: goal alignment becomes a **multiplier**, not one of N additive inputs. Work that doesn't align with any active goal is deprioritized by default — closes "principal forgets, Jarvis tracks" gap (`proactive_goal_tracking`) by making goal alignment structural.

This makes "Goals drive priorities" a programmatic primitive instead of a SOUL.md aspiration.

#### Hierarchy semantics

- `parent_id` cascade: when all children of a parent are `status=achieved` → emit `parent_close_suggested` event. **Not auto-close** — principal judgment via C12 batched suggestion (a single goal closing is a strategic moment).
- Progress rollup view: parent's effective progress = aggregate of children's `progress_ratio`. Surfaced in goal listing.

#### Proactive tracking — auto-create draft goals

- C5 generation arm subscribes to `principal_message` and `decision_made` events; LLM extracts surfaced deadlines / commitments / time-bounded asks → candidate child goals.
- **Candidates auto-create as drafts**, not queued for approval. Goal row inserted with `provenance:agent_extracted`, `confidence:low`, `status:draft`. No C6 gate trip — these are extracted from the principal's own messages and decisions; treating them like risky autonomous actions adds friction without safety value.
- Drafts surface to the principal in `/status` and the batched brief: "N draft goals — accept / edit / dismiss". Single-click triage; no approval ceremony.
- **Quiet auto-dismiss after 7 days** of no principal action (with a `draft_auto_dismissed` log emit so the extractor's accuracy can still be measured).
- **Rate limit: max 3 draft goals per week.** Prevents over-extraction noise; if the extractor is producing more than 3 useful candidates a week, that's a signal to revisit the formula.
- Closes `university-degree` parent having 0 children despite 7 disciplines + 3 overdue.

**Why auto-create vs queue:** worst case is a wrong draft sitting until dismiss — that's a UX issue, not a safety issue. The C6-queue route was wrong: it equated "agent extracts a candidate from the principal's own words" with "agent decides to fire a destructive action". Different blast radius, different gate.

#### Migration

- Existing 11 goals: `jarvis_focus` content split (mostly into `progress_log` events; a paragraph each into `strategic_posture`).
- `progress_pct` column dropped; consumers read view.
- Backfill `goal_slug` on past `decision_made` events where derivable; null otherwise.
- `record_decision` skill template adds `goal_slug` as required field (nullable in tool, but skill prompts always ask).

#### How measured

- **Goal-tag coverage on decisions** — % decisions with valid `goal_slug`. Target rising.
- **Stale-detection cadence** — `goal_stale` events fired, then resolved by activity within N days vs by abandonment.
- **Proactive-extraction acceptance rate** — % LLM-proposed child goals accepted by principal. Calibrates the extractor.
- **Parent-close-suggestion accuracy** — when principal accepts the suggestion (label).
- **Goal-alignment multiplier effect** — ablation of autonomous-loop scoring with vs without multiplier.

#### Rejected

- *Manual `progress_pct`* — lies.
- *`jarvis_focus` as multi-purpose field* — rot pattern.
- *Goal-awareness as SOUL.md prose only* — aspirational, not enforced.
- *Auto-close parent on all children achieved* — too aggressive; closing a goal is strategic.
- *`direction` as free-text repeated string* — no rollup possible.
- *Goal alignment as additive scoring input* — multiplier is what makes goals actually rank.

---

C2 complete.

---

### C4 — Reasoning & planning

Distillation surfaced deeper tensions than expected for a Tier B cap:
- Skills today are **deterministic numbered pipelines** (Steps 0..N) — directly contradicts the L1 op policy `Memory-driven autonomy, not pipelined`. Tension is unresolved in current code.
- TodoWrite is optional, model-discretion. Plan state is transient or implicit.
- Sprint plans live as freeform memories (`metacognition_sprint_plan_2026_04_20`, etc.) — same C3-abuse class we already retired in C5.
- `sequential-thinking` MCP server is installed (`.mcp.json`) but **never used** in any skill or hook.
- No mid-step replan — only post-compaction fresh-start or principal-pushback restart.
- `Already-done audit` (`/implement §4a`) exists *because* planning routinely skips a "verify-not-done" step — symptom-fix bolted onto the pipeline.

**Question:** What is the structure of C4 so that planning is judgment-driven (per op policy), plans are first-class observable artifacts, and replanning is incremental?

**Membership rule:** belongs in C4 iff *decomposes work into ordered/conditional steps* OR *works through a problem to draw a conclusion*. Two modes: planning (between actions) and reasoning (within an action). Decision act/ask = C6; research = C10; recall = C3.

#### Plans as first-class events, not implicit state

**Decision:** plans are **structured events** in C17 (consistent with C17's "everything is events" substrate). Plan event payload:
- `plan_id`, `task_id`, `goal_slug` (FK to C2)
- `steps[]` — each: `description`, `status`, `applies_when` (conditions), `outcome` (filled when done)
- `template_ref` (which template was instantiated, if any)
- `replan_history[]` — each revision with reason + replaced step ids

**Why events:** queryable, replayable, observable across devices, integrates with C17 trace propagation. Closes the "no unified view of in-flight plans" gap.

**Replaces:** TodoWrite as the canonical plan store (TodoWrite remains as UI affordance but writes also emit plan events). `pre-compact-backup` extraction of TodoWrite becomes redundant — events ARE the persistent backup. Sprint-plan freeform memories deprecated.

#### Skills become plan templates, not pipelines

**Architectural commitment:** skills migrate from numbered Steps 0..N (run unconditionally) to **annotated templates** — graphs of steps with `applies_when` conditions, `why_this_step` rationale, and `skip_if` rules. The planner instantiates a template and adapts to current context; it doesn't blindly execute.

This is the v2 resolution of the skill-pipeline vs `no_deterministic_pipelines` tension. Skills remain valuable as **encoded lesson scaffolds** (the gates from `reflection_driven_sprint_2026_04_23` belong in templates), but execution is judgment-driven, not slot-filling.

Migration is gradual: existing /implement, /delegate, /research, /autonomous-loop become templates one at a time as v2 components stabilize. They keep working in pipeline mode meanwhile (kept-current).

This commitment is **architectural** at L2; detailed template format is L3.

#### Replanning as partial, structural

**Decision:** replan is incremental, not all-or-nothing. Triggers and behaviour:

| Trigger | Replan scope |
|---|---|
| Step outcome differs from `expected` (test fails, missing file, etc.) | Re-evaluate remaining steps that depended on the changed assumption |
| Principal correction during execution | Re-evaluate from correction point |
| Anomaly event in C17 (rate-limit, hallucination_suspected, error) | Re-evaluate against goal alignment |
| `convergence_stall` from C6 | Force replan with broader template scope |
| Compaction event | Re-ground premises (per `post_compaction_task_premise_verification`), preserve done steps |

Each replan emits a new event with reason; done steps preserved (no work loss). Closes the all-or-nothing gap.

#### Two modes: planning + reasoning

**Planning mode** — between actions. Decomposes, sequences, replans. Uses templates, plan events.

**Reasoning mode** — within an action. Working through a hard problem. Default = inline LLM reasoning. **Sequential-thinking MCP** engaged when:
- Template doesn't apply (novel problem)
- Multiple paths visible, need disciplined exploration before C6 escalation
- High-stakes step (per C6 classification) where the cost of jumping conclusions > cost of structured thinking

This finally integrates the installed-but-unused MCP server.

#### Plan-decision-outcome linkage

Plans emit decision events through C6 gate at significant steps. Decisions carry `plan_id` + `step_id`. Outcomes (C7) link back via `plan_id`. Closes the granularity gap (today: decision ↔ outcome, but not at step granularity).

This makes plan effectiveness measurable at the right level.

#### Template lifecycle feeds C5

Templates are not static. Each instantiation's outcome (success/partial/failure) attributes back to the template. C5 reflection arm:
- High-success template → priority surface in planner
- High-failure template → flagged for refinement (Tier 1 principal queue)
- Stable, novel-task patterns → candidate for new templates

Closes "skill not used in 2 weeks → merge or delete" rule that currently has no telemetry to enforce.

#### Migration

- TodoWrite usage → kept; writes also emit plan events. Principal UI unchanged.
- /implement, /delegate, /research, /autonomous-loop → become templates over v2 development; pipeline behaviour kept until each is migrated.
- Sprint plan memories (`*_sprint_plan_*`, `metacognition_sprint_plan_*`) → DEPRECATED as memory class; new sprints use plan events.
- Sequential-thinking MCP → wired into reasoning mode for the triggers above.
- `Already-done audit` step in /implement → folded into template's `applies_when` (the step "verify not already done" runs first; subsequent steps apply only if not-done).

#### How measured

- **Plan event coverage** — % of significant tasks with a plan event. Target rising; gap = unplanned ad-hoc execution.
- **Replan rate by trigger** — distribution of why replan fires. Spike in any one trigger = pattern to investigate.
- **Step-outcome attribution** — % of step outcomes linked back to template + plan. Drives template lifecycle.
- **Template success rate** — per-template completion-without-replan ratio. Calibration spine for C5's template-refinement loop.
- **Sequential-thinking trigger rate** — when reasoning mode engages structured tool. Should match novel-problem rate.

#### Rejected

- *Skills as pipelines indefinitely* — direct violation of `no_deterministic_pipelines`.
- *TodoWrite as the only plan store* — transient, model-discretion, not cross-device.
- *Plans as memory rows* — same C3-abuse pattern v2 explicitly rejects.
- *All-or-nothing replan* — work loss, slow recovery.
- *Templates without lifecycle feedback* — they fossilize.
- *Sequential-thinking always-on* — token cost; reserve for novel/high-stakes problems.

---

C4 complete. Plans as first-class events, skills migrate to annotated templates, replan is partial-structural, sequential-thinking integrated for novel/high-stakes reasoning, plan-step-decision-outcome linkage closes granularity gap, template lifecycle feeds C5.

---

### C8 — Sub-orchestration

Distillation: worktree isolation is **advisory, not enforced** — Edit/Write tools take absolute paths bypassing worktree CWD; only Bash CWD is isolated. Repeated incidents (#295, #412, #413, #640) where subagents wiped orchestrator WIP, leaked untracked files into PRs, or worked in main tree. Multi-instance Jarvis collision validated 2026-04-06 (~1000 duplicated lines). Supabase locks have no TTL/heartbeat — stale locks from crashed sessions block others until manual cleanup. Handoff = free-text "PR URL + 2-line summary". `pm_dispatch_v1` (wave-based PM) retired in favor of orchestrator-worker (peer federation rejected per MAST 41–86.7% failure).

**Question:** What is the structure of C8 so isolation is enforced rather than hoped, multi-instance collisions are structurally prevented, and handoff is structured?

**Membership rule:** belongs in C8 iff *manages parallel agent execution* AND *coordinates state between agents/instances*. Output review = C16; logging = C17; individual subagent reasoning = subagent's own C4.

#### Isolation enforcement: pre + post gates, not subagent discipline

**Decision:** orchestrator runs structural checks bracketing every dispatch.

- **Pre-dispatch gate**:
  - Dirty-tree check; if dirty, refuse OR auto-stash with named stash linked to dispatch (recoverable, traced).
  - Compute expected scope from issue/task (files plausibly changed).
  - Acquire Supabase lock with TTL + heartbeat (see below).
- **Post-dispatch gate**:
  - HEAD-shift detector: if main repo HEAD moved to subagent's branch during dispatch, isolation failed → emit `worktree_isolation_breach` event, surface to C12.
  - Diff-outside-scope detector: subagent's commit diff must stay within expected-scope set; out-of-scope files (especially untracked-file leak class) flag for C16 review.
  - Recover stash from pre-dispatch.

**Why structural gates not subagent discipline:** discipline failed repeatedly because Edit/Write tools bypass CWD. Orchestrator gates run regardless of subagent compliance.

#### Coordination substrate

**Decision:** three primitives, layered:

- **GitHub labels + issue claim comment** — principal-visible status.
- **Supabase lock with TTL + heartbeat** — fast intra-Jarvis claim. Initial values: heartbeat every **30s**, TTL **120s** (4× heartbeat — survives one missed beat). Auto-release on missed heartbeat. Concrete values tunable in config (L3); the rule is TTL ≥ 3× heartbeat. Closes stale-lock gap.
- **Structured handoff event** (in C17) — subagent emits `subagent_complete(plan_id, branch, claimed_files[], claimed_changes_summary, test_results, blockers[])`. Orchestrator + C16 reviewer read this, not free-text summary. Trace_id propagated per C17.

**Why not free-text handoff:** review (C16) needs structured input to validate claims against C17 events from subagent. Free-text loses the reasoning chain.

#### Multi-instance protocol

**Decision:** mandatory primitives when parallel sessions are active.

- **Mandatory worktrees** for any parallel session — validated 2026-04-06; `git checkout` collisions destroy work otherwise. Not optional.
- **Lock-before-work**: any session must acquire Supabase lock on the work item before touching files. TTL = work bound; heartbeat = liveness. No lock = no write.
- **Stagger + lock acquisition order**: 10-second minimum stagger between session launches when both target same repo. Under contention (both still racing for same lock), Supabase `INSERT ... ON CONFLICT DO NOTHING` decides — first writer wins, second sees existing row and blocks/retries. Stagger reduces probability of contention; lock semantics resolves it deterministically when it happens.
- **Crash recovery**: stale locks (heartbeat missed for 2× TTL) auto-released. Recovery emits event for principal audit.

#### Cross-repo dispatch class

**Decision:** subagent dispatched isolated to a single repo per dispatch. Cross-repo work decomposes into sequential single-repo dispatches with explicit handoffs (events linking the chain). No parallel cross-repo dispatch — `isolation: worktree` cannot enforce isolation across repo roots.

#### Federated architecture remains rejected

Orchestrator-worker only inside any delegated task (per `federated_architecture_direction`). Peer federation rejected per MAST findings. C8 is hierarchical by design — single orchestrator, narrow workers, structured handoff back to orchestrator.

#### Migration

- `/delegate` skill → kept as the dispatch entry, but pipeline gains the pre/post gates above.
- "Always commit/stash before dispatch" memory rule → enforced by pre-dispatch gate, not relied on as discipline.
- Free-text handoff summary → kept as principal-readable, but **canonical handoff** is the structured event.
- Supabase lock writes from skills → migrated to TTL+heartbeat schema.
- Branch-naming convention `feat/<N>-<slug>` → enforced by gate, branch-race becomes structural-impossible (lock prevents two agents claiming same N).
- `pm_dispatch_v1` (already retired) — no migration needed.

#### How measured

- **Isolation breach rate** — `worktree_isolation_breach` events per 100 dispatches. Target → 0; spike triggers harness investigation.
- **Stale lock auto-release rate** — locks released by heartbeat-miss vs by normal completion. Spike = sessions crashing silently.
- **Handoff completeness** — % of `subagent_complete` events with all structured fields populated. Drives reviewer (C16) confidence.
- **Multi-instance collision rate** — sessions blocked by lock vs sessions that proceeded. Indicates parallelism level + collision protection effectiveness.
- **Diff-outside-scope flag rate** — proxy for subagent silent-drift class.

#### Rejected

- *Advisory isolation* (current) — repeatedly fails.
- *Free-text handoff* — opaque to reviewer, loses trace.
- *Locks without TTL/heartbeat* — stale-lock pain.
- *Optional worktrees in multi-instance* — validated as work-destroying.
- *Peer federation / flat orchestration* — MAST 41–86.7% failure rate.
- *Cross-repo parallel dispatch* — isolation primitive doesn't span repos.

---

C8 complete.

---

### C13 — Budget / cost governance

Distillation: no aggregated $/month dashboard across externals — principal is blind to cap proximity. Pain discovered via 400/422 errors (VoyageAI throttling, Anthropic API key empty, Copilot quota out, GHA minutes 50% in 5 days). One $1800 surprise bill when scheduled runs accidentally routed through `ANTHROPIC_API_KEY` instead of subscription auth (`max_20x_upgrade_available`). Dispatcher's `usage_probe` is the only live gate, single-agent only — undercount likely. Model selection is largely model-discretion; CLAUDE.md rules are prose.

C17 already commits cost-on-event (inline `cost_tokens`/`cost_usd`). C13 is the consumer + enforcement layer + router.

**Budget structure** (per L0 quality table):
- Claude Max subscription ~$100/mo — covers Claude Code interactive + scheduled tasks; bound by usage limits, not $.
- External services: **soft cap $20/mo, hard cap $100/mo** — Anthropic API key, VoyageAI, Supabase, OpenAI/Gemini (different-provider review per C16), GitHub Actions minutes, etc.
- Total ceiling ~$200/mo.

**Routing rule** (closes the cost-ambiguity raised in cost rehearsal): the architecture supports two modes — *subscription-first* (current default) and *API-first* — and is designed to switch between them with config change, not refactor.

- **Mode A — Subscription-first (current default while Max is active)**: C5 handlers, C16 LLM reviewers, classifiers, and recall judges run as Claude Code scheduled tasks under Max subscription wherever possible (free under Max). Only paths that *cannot* run on subscription — cloud Supabase scheduled tasks via `execute_sql` (no Python runtime), and C16 different-provider review — use API key. Pre-implementation cost rehearsal: ~$13/mo expected steady-state externals.
- **Mode B — API-first (evaluation period, planned)**: same handlers route via API. Principal's interactive Claude Code usage joins the API-billed pool. Pre-implementation rehearsal at current usage tempo (~9.5M tokens/mo, Opus-favoured): ~$50–80/mo aggregate; well within hard cap.

**Operational plan**: run Mode A for the current Max period; collect real usage data; transition to Mode B for an equivalent observation window; final mode decided on actual data, not estimates. C13's cap enforcement and ledger work identically in both modes — only the routing default changes. No architectural rework required for the switch.

**Question:** How does C13 turn the C17 ledger into spend visibility, cap enforcement, and model routing — and prevent the API-key-vs-subscription routing class that produced the $1800 bill?

**Membership rule:** belongs in C13 iff *tracks monetary spend* OR *enforces caps before action* OR *selects cheaper alternatives among equivalent options*. Reads from C17, gates via C6, routes via config.

#### Multi-service per-cap ledger

**Decision:** per-service tracking aggregated to the external-services pool with the soft/hard caps above. Subscription (Claude Max) is tracked in usage units, not dollars.

| Service | Cap class | Source |
|---|---|---|
| Anthropic API key | Near-zero (subscription used elsewhere); alert if rising | C17 events + monthly billing reconciliation |
| VoyageAI | Tier-bound | C17 events + provider API |
| Supabase | Free-tier or specified | Provider API |
| OpenAI / Gemini (different-provider review per C16) | Per-PR allocation | C17 events |
| GitHub Actions minutes | Per repo, monthly | gh API |
| Copilot quota | Binary availability probe | gh API |

Each service has a configured budget; sum constrained to principal's external cap.

#### Cap enforcement gate (soft + hard for externals)

**Decision:** projected month-end external spend (linear extrapolation from C17 ledger) drives a two-threshold gate consulted by C6.

| Projected external spend | Behaviour |
|---|---|
| ≤ $20 (soft cap) | No action |
| $20 – $100 (above soft, below hard) | Warn — event + principal notification (C12 batched); deprioritize non-essential external LLM use; prefer subscription/Haiku where possible |
| > $100 (hard cap) | Block non-essential external calls; allow only critical (principal-tagged or hard-required) |

Per-service projection inside the gate so one service hitting hard cap doesn't kill all others, but **aggregate hard cap** is the bottom line. Subscription usage tracked separately (units, throttling-based, not dollar gates).

#### Daily reconciliation + heartbeat probes

**Decision:** silence-failure (key empty, quota out) is detected pre-emptively, not after a 400.

- **Daily reconciliation**: C17-derived spend vs provider billing API (where available). Drift > N% → investigation event.
- **Heartbeat probes**: weekly low-cost API call per external service to verify keys/quotas alive. Failed probe → principal notification (C12) before next action depending on it.

Closes the "Anthropic API key empty discovered by 400 error" class.

#### Model router as configured rules, not discretion

**Decision:** every LLM call passes through a router that picks model from rules. Discretion replaced by config.

Rules (initial):
- Mechanical / classifier / extraction → Haiku
- Implementation / refactor / structured reasoning → Sonnet
- Architectural decisions / cross-system reasoning / redrobot principal work → Opus (regular, not 1M unless principal-tagged)
- Different-provider review (per C16 high-leverage) → OpenAI/Gemini per-class

Router emits `model_routed` event with rule that fired. Calibration: if outcome of a class consistently underperforms with chosen model → C5 reflection proposes rule update.

Closes "default Opus / Opus 1M extra billing" prose-rule fragility (`opus_1m_extra_billing`).

#### API-key vs subscription routing protection

**Decision:** structural guard against the $1800-bill class.

- Default for all Claude Code interactive + scheduled tasks → subscription (Max).
- API-key paths require **explicit allowlist entry** (actor + reason + cap).
- Pre-action probe: if action is about to use `ANTHROPIC_API_KEY`, verify allowlist match. Else block + escalate.
- C6 gate consults C13 routing decision; mismatch = `routing_violation` event.

#### Migration

- `agents/usage_probe.py` → kept; extended to all actors (not just dispatcher) by reading C17 cost events.
- CLAUDE.md model-selection prose → migrated to router config; prose becomes documentation of router rules.
- Scheduled-task subscription assumption (`scheduled_tasks_subscription_not_api`) → enforced by routing protection above, not behavioural memory.
- Manual escalation Max → Max 20× → triggered by 4-week sustained throttling event from C17, surfaced via C12 to principal.

#### How measured

- **Cap proximity** — projected month-end vs cap, per service. Principal-facing dashboard.
- **Reconciliation drift** — C17 ledger vs provider billing. Should be near-zero.
- **Heartbeat-failure lead time** — interval between heartbeat-detected exhaustion and the action that would have failed. Should be > 0 (i.e., we caught it early).
- **Routing-violation events** — should be near-zero. Spike = something is bypassing routing.
- **Model-class outcome quality** — per-router-rule outcome quality from C5 calibration.

#### Rejected

- *Single aggregate cap* — masks per-service exhaustion (one service can be empty while aggregate is fine).
- *Pure post-hoc tracking* — discovers cap breach via 400 error, current pain.
- *Model selection by discretion* — codified router gives reproducibility + calibration.
- *No API-key routing protection* — repeats $1800 class.
- *Heartbeat probes per-action* — too expensive; weekly per-service is the right cadence.

---

C13 complete.

---

### C14 — Security & privacy

Distillation: significantly more mature than other caps — Security & Digital Hygiene Sprint 1 shipped (memory key: `pillar9_sprint1_self_security` — historical identifier from pre-Phase-B numbering). Existing layered defense: secret scanner PreToolUse hook (12 regex families + 8 bash-exfil patterns, heredoc-aware), credential registry (metadata-only, DB CHECK rejecting raw values), protected-files hook with principal-aware tiering, action gate Tier 0/1/2 in `safety.py`, gitleaks pre-commit + CI, soft-delete with 30-day retention, recovery playbook. Threat model = external intruders / cloud-provider-trusted / single-user / GitHub-public-repo. Principal deliberately accepts: prompt injection via web/issues, personal-data leakage, password hygiene blindness.

Remaining gaps require structural treatment in v2; most are extensions of what shipped, not rebuilds.

**Question:** What does C14 add to Sprint 1's foundation to close the audit-completeness, compromise-detection, and recovery-runbook gaps?

**Membership rule:** belongs in C14 iff *protects credentials/access* OR *bounds blast radius of compromise* OR *enables forensic reconstruction*. Principal-stated out-of-scope (personal data, password hygiene, prompt injection on untrusted external content) stays out of scope unless principal re-decides at L0.

#### Layered defense (preserved from Sprint 1)

Kept as-is:
- Secret scanner PreToolUse hook
- Credential registry (metadata-only, CHECK constraint)
- Protected-files hook with principal tiering
- Gitleaks pre-commit + CI
- Soft-delete 30-day retention
- Action gate Tier 0/1/2 (consolidated under C6 in v2)

#### Audit-completeness — close the hook-bypass blind spot

**Decision:** every hook firing — including denials — emits a persistent event to C17 (`hook_fired`, `hook_denied`, `hook_bypassed`).

Current state: hook denials produce stdout JSON visible only in Claude's transcript; no persistent record. A breach via hook bypass leaves no trace beyond the device-local jsonl.

v2: hooks call into the canonical event substrate (C17). Forensic reconstruction works regardless of which device the session ran on.

**Closes the documented forensic gap** — agent-mediated mutations were reconstructable; raw-credential events were not.

#### Memory-write versioning (carried over from C3)

C3's bi-temporal model preserves `memory_facts` history (no destructive overwrite). This already closes the "memory_store overwrite has no version backup" gap that the recovery playbook flagged. C14 confirms this dependency.

#### Compromise detection

**Decision:** active monitoring beyond passive scanners.

- **HIBP-style breach probe**: weekly check of registered credential identifiers (emails, usernames where applicable) against haveibeenpwned API. Hit → emit `credential_potentially_compromised` event → C12 principal notification.
- **Suspicious-activity heuristics**: anomaly events from C17 self-detection (rate-limited spikes, hallucinated tool calls, off-hours dispatcher activity) cross-checked against credential usage timestamps. Pattern match → flag for principal.
- **Post-rotation verification**: when principal rotates a credential, registry's `last_rotated` updated; subsequent uses of old credential (failed auth events) → `stale_credential_in_use` event (a script somewhere wasn't updated).

#### Recovery runbook for credential compromise

**Decision:** new runbook, distinct from agent-broke-something playbook.

`docs/security/credential-compromise-runbook.md`:
1. Identify scope (which credential class, what touched it via C17 audit)
2. Rotate credential per registry's `rotation_notes`
3. Update credential in all storage locations (registry's `stored_in` field tracks them)
4. Verify rotation via heartbeat probe (C13)
5. Audit C17 events for the compromise window — what was accessed/modified
6. Principal decides whether to rollback specific changes or accept

Closes "no key-leaked runbook" gap.

#### Principal-env-var verification

**Decision:** session bootstrap self-verifies the principal env var matches the launching context.

Workshop scheduler logon failure (2026-04-25) showed launchers silently misconfigure the principal env. With principal-aware tiering deciding what hooks block, a wrong principal = wrong protections.

v2: bootstrap reads launcher signature (process tree, env source) and verifies expected principal. Mismatch → block session start, emit `principal_misconfigured` event.

#### Supabase RLS — defense in depth

**Decision:** enable RLS even though single-user. Two roles: `principal` (full read/write) and `agent` (no read on `credential_registry.value-equivalents`, no DELETE on `memories`/`events`).

Single-user assumption is correct today, but RLS adds a guard if a leaked anon key ever happens. Cheap to add; matches principal's "восстановление если что-то всё-таки произойдёт" clarification at L0.

**Note:** RLS on the shared Supabase project is a **cross-project change** (per C15's cross-project shared-code class). Coordination with redrobot before applying — RLS rules must allow redrobot's existing access patterns or break it.

#### Migration

- Existing Sprint 1 components → kept as-is.
- Hooks emit to C17 events → required when C17 ships.
- HIBP probe → new component, scheduled task.
- Credential-compromise runbook → new doc.
- Principal verification → bootstrap script extension.
- Supabase RLS → migration; needs paired schema change (with redrobot coordination per cross-project M3 class in C15).

#### How measured

- **Hook-firing event coverage** — % of hook fires represented in C17 events. Target: 100%. Gap = forensic blind spot.
- **Heartbeat-probe lead time** for credential exhaustion (shared with C13).
- **HIBP probe outcomes** — number of hits caught proactively vs after-the-fact.
- **Stale-credential-in-use rate** — drops to ~0 after a rotation indicates clean propagation.
- **Principal-mismatch rate** — should be near-zero; spike = launcher misconfiguration class.

#### Rejected

- *Hook denials in transcript only* — current state, forensic blind spot.
- *No active breach monitoring* — discovers compromises by their consequences.
- *Single recovery playbook for all classes* — credential-compromise has different steps than agent-broke-something.
- *Reliance on principal env var without verification* — workshop scheduler class.
- *Skip Supabase RLS because single-user* — cheap defense in depth that aligns with principal's recovery emphasis.
- *Re-expanding scope to personal data / passwords / prompt injection* — explicitly out per L0; not re-decided here.

---

C14 complete. **Tier B done.**

---

### Tier C — shallow decisions

These caps inherit most structure from Tier A/B decisions. Each gets a single-pass commit.

#### C1 — Identity & values

**Decision:** SOUL.md (principal-authored axioms) + CLAUDE.md (project-specific instructions) loaded at session start via the session-context hook. **Distinct storage from C3 memory** — identity is immutable-by-Jarvis (principal edits only, M3 in C15), retrieved by injection not query. C5 stale-challenge does NOT challenge identity (would break the alignment substrate); only principal does, via direct edits.

**How measured:** identity-drift detection — C5 reflection compares Jarvis's recent behavior pattern to SOUL.md rules; deviation flagged for principal review (not auto-corrected).

**Rejected:** identity learnable from outcomes (drift risk), identity stored in C3 (sub-type rule violation).

#### C7 — Execution

**Decision:** Claude Code native tools (Read/Edit/Write/Bash/Glob/Grep) + MCP tools as the tool substrate. Tool selection follows CLAUDE.md "dedicated tools over Bash" — encoded in C4 templates' `applies_when` rules. Every tool call goes through C6 gate (PreToolUse) and emits a C17 event.

**How measured:** tool-error rate by class (`tool_call_error` events), hallucinated-tool-call rate (`hallucination_suspected` events from C17 self-detection).

#### C9 — Tool / environment interface

**Decision:** MCP for all external systems (Supabase, GitHub, Telegram-as-channel, firecrawl, context7, etc.). Native Claude tools for filesystem/shell. `.mcp.json` portable across 3 devices (no hardcoded paths) per existing rule. C13 heartbeat probes verify MCP server availability per service.

**How measured:** MCP server availability % (C13 heartbeat), MCP call error rate by server.

#### C10 — Research

**Decision:** delegated to subagents for multi-step / open-ended (proven pattern; this design exercise itself uses it). Direct WebSearch / WebFetch / context7 / firecrawl for narrow lookups. Research outputs emit `research_completed` events with structured payload (sources, key findings); high-value findings can be promoted to C3 facts via C5 generation arm.

**How measured:** research-to-decision rate (% of research events that fed a `decision_made` within N hours), citation accuracy on promoted facts.

#### C11 — Perception

**Decision:** perception is the boundary where external events become C17 events. Sources: principal messages (CLI, future channels), GitHub events (via Actions → Supabase per `event_driven_perception_v1`), scheduled-task triggers, repo file-changes, MCP server signals. C11 does **not** decide what to act on — it converts external signals into structured events; **C2/C3/C5/C6 chain** decides response per `Memory-driven autonomy` op policy.

**Ingest gate:** raw signals from external sources can be high-volume (CI events, file watches). C11 has a thin filter — discards signals that match known-noise patterns (config) — but every accepted signal becomes an event regardless of whether anything acts on it. The decision *to act* is downstream (autonomous-loop / handlers query events); the decision *to record* is C11's only call, biased toward record-everything. Signals dropped by the noise filter emit a `signal_dropped` count event for audit (no payload).

**How measured:** event-to-action latency for time-sensitive events (e.g., principal-message to first action), event drop rate (events that arrived but weren't ingested).

#### C12 — Communication with principal

**Decision:** structured channels with priority semantics:
- **Interactive** (CLI / future desktop) — primary, highest fidelity.
- **Critical alerts** — interrupt-style; few, validated against false-positive rate. Examples: cap-breach (C13), credential compromise (C14), bootstrap fail (C15).
- **Batched briefs** — morning/evening summary aggregator: pending C6 queue items, C16 reviewer summaries, C5 stale alerts, C2 deadline-soon, C13 cap proximity. **Single principal-facing surface**, not N notifications.
- **Telegram** — chat-only role per L0 (dropped as primary interface).

**Drafts vs sends:** Jarvis drafts; final send to other humans (PR comments, messages, emails) goes through principal approval per L0 non-goal "не пишет от моего лица". **Mechanism:** outgoing-message production is a separate C12 action class (not a tool call), gated by a `c12_send_intent` event that requires explicit principal-confirmed `c12_send_approval` before any tool actually emits text outward. Drafts written to a queue (visible to principal via batched brief) — sending = principal button-press = produces the approval event = unblocks the actual send tool call. Pre-confirmation tool calls don't fire.

**How measured:** interrupt rate (should fall as autonomy + accuracy grows), batched-brief actionability (% of items in brief that principal acted on / dismissed).

**Rejected:** Telegram as primary interface (L0); per-event principal notification (drowns principal); auto-send to other humans (L0 non-goal).

---

**L2 complete.** All 18 capabilities have membership rules, structural decisions, measurement plans, migration paths, and explicit rejections.

---

## Bootstrap protocol & migration order

L2 commits to mature-state behaviors that depend on data, calibration, or peer subsystems. **Bootstrap protocol** specifies the minimum viable system on Day 1 and how each capability transitions from cold-start to mature operation. Closes the reviewer's flag that decisions assumed mature infrastructure.

### Migration shape — vertical slices, not horizontal layers

Per `Vertical slices, not horizontal` (SOUL §Engineering principles, adopted 2026-04-30): bootstrap is cut as **per-action-class vertical slices**, each delivering a runnable end-to-end result, not as horizontal cap-by-cap layers. Old path coexists for action classes not yet cut over (`two-mode coexistence`).

Each slice carries one action class through whichever caps are needed for an end-to-end audit trail in MVP form. Slices reach across multiple caps; subsequent slices reuse and deepen the substrate the earlier ones laid down.

**Slice 0 — `memory_write` warmup.**
- Path: `memory_store` → C6 gate (conservative seed threshold) → tool execution → C17 event written → C5 outcome record (success / `record_correction`).
- Verifiable: `memory_store` runs end-to-end, events table shows full chain (gate decision, tool call, outcome). Old `memory_store` path still serves writes not yet on the new path.
- Goal: learn the cutover mechanic on a low-blast, high-frequency action class; seed C6 + C5 calibration labels.
- Out of slice 0: C16 review, C13 enforcement, C3 schema migration, multi-cap correlations.

**Slice 1 — `/implement` AFK-trust path.**
- Path: `/implement` invocation → C6 pre-tool gate → C17 event log per step → C16 mechanical reviewers (diff coherence + test coverage) → C5 outcome record → principal-visible audit.
- Verifiable: run `/implement` on a small task, inspect events table for full audit trail with gate decisions, review verdict, outcome. C16 catches `N edited + diff = 0` automatically.
- Goal: directly move the HITL → AFK-trust needle. Closes the pain points captured in `verify_before_assuming_implemented`, `subagent_acceptance_criteria_dodged_as_out_of_scope`, `record_decision_during_session_not_at_end`.
- Out of slice 1: LLM reviewers (slice 2), `/delegate` path (slice 3), cross-device sweep, C13 enforcement.

Subsequent slices extend slice 1 (LLM reviewers, cross-device) or cut new action classes (`/delegate`, `goal_set`, etc.). Slice ordering is biased toward AFK-trust caps first — substrate-only slices that don't move principal autonomy forward are deprioritized.

### Cap-level dependency order (substrate availability)

The vertical-slice cuts above constrain *which* substrate must exist before a slice can run. The cap-level dependency order is the load-bearing graph that decides when each cap's MVP becomes available to slices that need it:

1. **C17 substrate** (events table + canonical schema). Required by every slice.
2. **C3 storage shape** (`memory_facts` + `memory_episodes` tables; canonical Postgres function). Required by slice 0. Existing memories migrated via compatibility view during cutover.
3. **C13 cost ledger view + routing rule**. Read-only first — observability before enforcement.
4. **C6 single canonical gate** (PreToolUse on every tool, conservative defaults — see below). Required by slice 0 + 1. Replaces SOUL prose drift.
5. **C16 mechanical reviewers** (diff coherence + test coverage). Required by slice 1. Cheap, deterministic, immediate value.
6. **C5 dispatcher + handlers** (synthesizer + calibrator first; stale-challenger last because it depends on C5's own calibration loop being seeded). Required by slice 0 + 1.
7. **C16 LLM reviewers** (peer-Jarvis logical correctness + goal alignment). Different-provider review **deferred** until first month of cost data validates the budget.
8. **C8 pre/post dispatch gates**. Wraps existing /delegate (slice 3 candidate).
9. **C2 schema cleanup + goal-decision linkage**. Touches existing live data; coordinated cutover.
10. **C4 plan events + skill-template migration**. Per-skill migration; existing pipelines stay functional during.
11. **C15 self-improvement formal loop**. Last because it depends on C16 + C5 + C17 being measurable.
12. **C14 audit-completeness + compromise detection**. Extensions on shipped Sprint 1.

During migration, capabilities operate in **two-mode coexistence**: old path remains until new path passes regression. C17 path-parity test (defined in C3) is the cutover gate.

### Per-capability bootstrap thresholds

Until enough labels accumulate, "calibrated" gates can't fire. Every such gate has a seed configuration:

| Capability | Seeded behavior until labels accumulate | What labels look like | N to mature |
|---|---|---|---|
| **C3 conflict classifier** | Auto-apply confidence threshold raised from 0.85 to 0.95 (conservative). Below queue. | `record_correction` events labeling classifier outcome. | ~30 labels per class |
| **C5 generation arm** | Auto-write threshold 0.95; no super-aggressive synthesis until first 30 days of labels. | Principal override on synthesized memory. | ~50 labels global |
| **C5 stale-challenge** | Triggers only on **principal correction** and **decision failure** (the strong signals); periodic sweep deferred until first quarter. | Principal override on superseded memory. | ~20 labels |
| **C5 judge calibrator** | Computed only on judges that have ≥10 ground-truth labels in last 90 days; below threshold, judge is reported "uncalibrated" and uses its seed threshold. | Outcome labels link back. | ≥10 per judge per 90 days |
| **C6 gate per-class** | Action class with no calibration history: defaults to **escalate-on-uncertainty**. As classifier confidence + outcome data accumulate, threshold relaxes. | Principal override on queue items. | ~30 labels per class |
| **C6 convergence detector** | Counter active from Day 1 (deterministic, no calibration needed). | n/a | Immediate |
| **C16 different-provider** | **Deferred to month 2.** Peer-Jarvis only in month 1; cost data from month 1 informs whether different-provider is affordable at planned cadence. | Cost reconciliation. | 30 days of operation |
| **C13 cap enforcement** | **Warning only in month 1**, no blocking. Cost numbers vs cap inform whether caps are correctly set. Blocking starts month 2. | Principal observation of cost trends. | 30 days |
| **C15 trust ladder** | All M1 classes start locked. Principal manually unlocks first class after first month of M0 success data. | Per-class regression / success counts. | Per-class threshold |
| **C16 cross-device sweep** | Disabled until month 2. Manual cross-device test on principal-flagged config-touching changes only. | Cross-device incident reports. | 30 days |
| **Bootstrap-protection rule (C15)** | On Day 1, **C6/C16/C17 modifications are principal-only** (no "previous version" to review against yet). After 30 days of stable operation, prior-version + different-provider review unlocks for these caps. | Operational stability of C6/C16/C17. | 30 days stable |

### Cold-start month rules

- **Cost cap**: warning thresholds active, no blocking. Principal sees real numbers before policy hardens.
- **Autonomy default**: lower than mature target. Most decisions queue; principal approval rate informs C6 threshold tuning.
- **Mutation arm volume**: capped at ~5 autonomous writes/week from C5 to limit blast radius if calibration is wrong.
- **Principal instrumentation**: weekly "cold-start readout" — cost, override rate, queue depth, per-cap volume — informs which thresholds to relax for month 2.

Cold-start ends when each cap has its label budget met OR after 60 days, whichever comes first.

### Bootstrap caveat

This protocol explicitly accepts **degraded autonomy in month 1** in exchange for safety + calibration data. The trade-off is principal-visible: more queueing, fewer auto-decisions, more manual labeling. By month 2 the system should be operating closer to design intent.

---

## L3 — Technologies & Patterns

L3 marks **candidate options** for each capability, not commitments. Final tech choices made during implementation against real benchmarks (per `Design-to-evaluate` op policy). For caps that inherit established stack (Claude Code native, MCP, Supabase, GitHub), L3 is short.

After scout v2 (2026-04-27) — see [`jarvis-build-vs-buy.md`](jarvis-build-vs-buy.md) — several Lean choices have moved from "options worth investigating" to "specific package / SQL file / plugin URL identified." These remain non-committal until benchmarked, but reduce search surface for implementation sprints.

### C1 — Identity & values
- SOUL.md + CLAUDE.md plain markdown files loaded by session-context hook (current pattern, established).
- Lean: keep as-is; no real alternatives worth investigating.

### C2 — Goals & priorities
- **Storage**: existing `goals` table, schema cleanup per L2 (drop `progress_pct`, split `jarvis_focus`).
- **Stale detection**: pg view + scheduled query OR pg trigger on event insert. Lean: scheduled query (simpler, traceable).
- **`goal_slug` resolution at gate**: pg function consulted by C6 gate hook OR LLM-judged matching. Lean: pg function with semantic-similarity over goal embeddings (cheap, deterministic), LLM fallback only on ambiguity.
- **Goal embeddings**: VoyageAI on `description` at insert/update. Lean: pg trigger fires Voyage call via `mcp-memory/server.py` background worker; cached in `goals.embedding` column.
- **Parent-close cascade**: pg trigger on `goal_status_change` vs scheduled query. Lean: scheduled query — closing a goal is a strategic moment requiring principal judgment, not auto-action; trigger only emits `parent_close_suggested` event.
- **Proactive child goal extraction**: scheduled subagent vs event-triggered handler on `principal_message`/`decision_made`. Lean: event-triggered with Haiku — per `Memory-driven autonomy` op policy, not periodic sweep.

### C3 — Memory
- **Tables**: PostgreSQL with pgvector (current). No real alternative under Cost=9 + Memory=10.
- **Bi-temporal model**: SQL standard `valid_from`/`valid_to` vs `system_versioning` extension vs `pg_bitemporal` `effective`/`asserted` periods. Lean: **adopt [`pg_bitemporal`](https://github.com/scalegenius/pg_bitemporal/tree/master/sql) column naming + SQL functions** (Apache-2.0); copy `ll_create_bitemporal_table.sql`, `ll_bitemporal_insert.sql`, `ll_bitemporal_correction.sql`, `ll_bitemporal_inactivate.sql`, `ll_bitemporal_update.sql` into Supabase migration. Supersession collapses into closing of `asserted` period — no separate `superseded_by` column. GIST exclusion constraint prevents overlapping periods per business key.
- **Canonical recall function**: PL/pgSQL vs SQL function vs Postgres + Python wrapper. Lean: PL/pgSQL for the deterministic part (filter + score + lifecycle); Python wrapper adds optional LLM enhancements (rewriting, judge).
- **Hybrid search recipe**: own math vs canonical SQL. Lean: [pgvector-python `examples/hybrid_search/rrf.py`](https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py) — `RANK() OVER (... embedding <=> %s)` + `RANK() OVER (... ts_rank_cd)` joined via `1.0/(60+rank)`. Drop-in for current Supabase.
- **Vector adapter library**: hand-formatted vector strings (current) vs `pgvector` Python lib. Lean: `pip install pgvector` — SQLAlchemy/psycopg adapter. Aligns with `memory_server_v2_improvements`.
- **Embedding model**: VoyageAI (current, paid Tier 1) vs OpenAI text-embed vs Cohere. Lean: VoyageAI — already integrated, $0 at current volume.
- **Conflict detection LLM verifier**: Haiku 4.5 vs Sonnet 4.6. Lean: Haiku (cheap), Sonnet only on flagged contention.

### C4 — Reasoning & planning
- **Plan events**: structured JSONB payload in canonical events table vs separate `plans` table. Lean: JSONB on events (consistent with C17 substrate; views for step-level queries).
- **Plan checkpointing backend (LangGraph carveout — scope expanded 2026-04-27 per `v2_open_questions_resolved_2026_04_27`)**: bespoke pg layer vs [LangGraph PostgresStore](https://docs.langchain.com/oss/python/langgraph/add-memory) + checkpointer. Lean: LangGraph PostgresStore wrapped behind ONE MCP server, pointing at Supabase. Broader state-machine use cases acceptable beyond plan-only.
- **Plan state ledger shape**: bespoke vs Magentic-One verified-facts / derived-facts / guesses ([MS Agent Framework 1.0](https://github.com/microsoft/agent-framework), MIT). Lean: adopt Magentic ledger structure as plan-event payload — schema reference, no code import.
- **Skill→template migration mechanism**: gradual per-skill rewrite vs introduce a template-runner that wraps existing skills. Lean: template-runner first (compatibility), per-skill rewrite as natural drift.
- **Sequential-thinking trigger**: explicit `engage_sequential_thinking` tool call vs auto-engage on novelty signal. Lean: explicit (cheaper; novelty signal is fuzzy at first).
- **Replan algorithm**: full-replan vs incremental-from-stall-point. Lean: incremental (preserves done steps).

### C5 — Reflection / learning
- **Dispatcher**: pg LISTEN/NOTIFY + Python event subscriber vs scheduled-task polling vs in-session reflection only. Lean: LISTEN/NOTIFY for real-time triggers, scheduled-task polling as fallback.
- **Handlers**: separate Python scripts per handler (synthesizer, calibrator, etc.) vs single dispatcher dispatching internally. Lean: separate scripts (independent calibration, easier per-handler cost bounds).
- **LLM choice per handler**: synthesizer/calibrator/FoK Haiku 4.5; stale-challenger Haiku→Sonnet escalation only. Lean: this default, capped Sonnet escalations per L2.
- **Stale-challenge re-examination**: full LLM re-read vs structured prompt with diff-of-recent-evidence. Lean: structured (lower tokens, more reliable).
- **Class-conditional calibration (closes Q4 sparse-class)**: bespoke vs `crepes` vs MAPIE. Lean: [`crepes`](https://github.com/henrikbostrom/crepes) — `pip install crepes`, Mondrian CP via `class_cond=True`, sklearn-compatible, CPU-only, ~10× faster than MAPIE on small data. Wires into `memory_calibration_summary` MCP tool. [`MAPIE`](https://github.com/scikit-learn-contrib/MAPIE) on watch list (2026 roadmap adds CP for LLM-as-judge).
- **Calibration metrics**: bespoke vs `netcal`. Lean: [`netcal`](https://github.com/EFS-OpenSource/calibration-framework) (`pip install netcal`) — ECE/MCE/ACE/MMCE + reliability diagrams; pair with sklearn `brier_score_loss`.
- **Judge prompt source**: bespoke strings vs published rubric prompts. Lean: [`prometheus-eval`](https://github.com/prometheus-eval/prometheus-eval) (`pip install prometheus-eval`) — `from prometheus_eval.prompts import ABSOLUTE_PROMPT, RELATIVE_PROMPT` for /reflect, /verify, calibrator. Removes bespoke prompt-engineering drift.

### C18 — Proactive challenger
- **Detection engine**: SQL queries over C17 events + `goals` table. No LLM in the hot detection path.
- **Threshold tuning**: per-signal config in `recalibration_thresholds.yaml` (calibration-Brier, goal-stale-days, override-frequency-per-class). Editable as M2-strong (reviewer + smoke required) since wrong thresholds = surfacing storm.
- **Surfacing renderer**: optional Haiku call to phrase the `recalibration_proposed` payload readably. Capped by C13 — max 5 surfacings per batched brief (1 LLM call each).
- **Routing**: emit `recalibration_proposed` event; C12 batched-brief aggregator picks it up alongside other deferred items. No new transport.

### C6 — Decision gating
- **Hook substrate**: Claude Code PreToolUse hook (interactive lane) + similar gate at MCP boundary (cloud lane). Lean: this; the canonical gate function is the same behind both.
- **Gate outcome enum**: ALLOW / LOG_AND_PROCEED / EXPLAIN / QUEUE / DEFER / BLOCK. DEFER per Claude Code v2.1.89 (Apr 1 2026) PreToolUse `defer` decision — pause headless and resume. Lean: support all six outcomes; DEFER for state probes that need async work.
- **Permission-vs-hook precedence**: per Claude Code v2.1.105 (Apr 13 2026), `permissions.deny` correctly overrides hook `ask`. Lean: audit current config — deny-rules belong in `permissions`, not in hook-`ask`. Prior bug let hooks downgrade denies.
- **Classifier engine**: rule-based config (current `safety.py` style) vs LLM classifier vs hybrid. Lean: hybrid — rules for known-class fast path, LLM for ambiguous (with Haiku, cap'd by C13).
- **State probes (git, harness, etc.)**: synchronous calls during gate vs cached state with TTL. Lean: synchronous for fast probes (git status), cached for expensive (memory recall, embedding).
- **Output validation (optional)**: bespoke checks vs [Guardrails AI](https://github.com/guardrails-ai/guardrails) (Apache-2.0). Lean: optional library; called from PostToolUse on tool outputs only, not flow control. Defer until concrete need.
- **Tier 1 queue table**: separate `decision_queue` table — `(action_id PK, classification enum, payload JSONB, gated_at, decided_at, decision enum, reasoning, expires_at)`. Lean: separate table from C17 events (queues are stateful workflow; events are append-only).
- **Tier 1 queue UI**: CLI command vs lightweight web dashboard vs batched-brief in chat. Lean: CLI initially; web dashboard if queue volume grows.

### C7 — Execution
- Claude Code native tools (Read/Edit/Write/Bash/Glob/Grep) + MCP. No alternative under current platform.
- **Tool latency attribution**: PostToolUse hook reads `duration_ms` (Claude Code v2.1.118, Apr 2026) → emits to C17 events with `gen_ai.operation.duration_ms`. Free input for C13 cost-by-action and C16 latency calibration.
- **Tool retry / fallback**: hand-rolled `try/except` in `mcp-memory/server.py` callers; `tenacity` (`pip install tenacity`) only if 3+ tools share the same retry shape.
- **MCP result-size cap**: per Claude Code v2.1.119, `_meta["anthropic/maxResultSizeChars"]` up to 500K. Lean: set explicitly on memory recall responses to avoid silent truncation.

### C8 — Sub-orchestration
- **Subagent runtime**: Claude Code agents with `isolation: worktree` (current). No real alternative on platform.
- **Worktree open-bug compensation**: per Claude Code [#39886](https://github.com/anthropics/claude-code/issues/39886) (silent fall-through) and [#50850](https://github.com/anthropics/claude-code/issues/50850) (HEAD-shift). Lean: post-dispatch HEAD-shift detector + diff-outside-scope regardless of harness fixes; pre-dispatch `git stash --include-untracked` closes `untracked_main_tree_leaks_into_subagent_worktree` class. Recent harness fixes (v2.1.119, .101, .82) shrink this surface but don't remove the need for orchestrator-side gates.
- **Lock store**: Supabase row with TTL+heartbeat (per L2). Alternative: Postgres advisory locks. Lean: explicit row (visible, debuggable).
- **Pre/post gates**: shell scripts (git status / HEAD-shift detector) called from orchestrator. Trivial.
- **Handoff event payload**: JSONB schema validated at MCP boundary. Lean: this; shape from Magentic-One verified-facts / derived-facts / guesses ledger ([MS Agent Framework 1.0](https://github.com/microsoft/agent-framework), MIT) — `claimed_files[]`, `test_results`, `blockers[]`, plus the tri-ledger fields.
- **Subagent lifecycle hook**: `TaskCreated` hook (Claude Code v2.1.84) + scheduled-task `--resume`/`--continue` (v2.1.110) for autonomous-loop crash recovery without re-dispatching.

### C9 — Tool / environment interface
- MCP for external systems; native Claude tools for fs/shell. Established. No alternatives.
- **Heartbeat probes**: scheduled task per service, lightweight HEAD/auth-check call. Lean: this.
- **MCP server scaffold**: hand-rolled vs `mcp-builder` skill. Lean: [`anthropics/skills/mcp-builder`](https://github.com/anthropics/skills/tree/main/skills/mcp-builder) — 4-phase guide; checklist when extending `mcp-memory/server.py` or adding new MCP servers.
- **MCP auth**: must support OAuth 2.1 + Resource Indicators (RFC 8707) — mandatory since 2025-11-25. `.mcp.json` portability across 3 devices depends on this.

### C10 — Research
- WebSearch + WebFetch + context7 (docs) + firecrawl (browser/scrape). Established stack.
- **Multi-step research delegation**: subagent (current pattern) vs [GPT Researcher MCP](https://github.com/assafelovic/gpt-researcher) vs [STORM (Stanford)](https://github.com/stanford-oval/storm). Lean: GPT Researcher as primary engine (Apache-2.0, ships own MCP server, drop-in via `.mcp.json`); STORM for "deep dive" tier (long-form synthesis); subagent pattern remains for ad-hoc / context-heavy.
- **Output**: structured event payload with sources + key findings; promotion to C3 facts via C5 generation arm. No alternative worth marking.

### C11 — Perception
- **External event ingest**: GitHub Actions → Supabase events table (`event_driven_perception_v1` already established).
- **File-watch source**: native fs-watch vs polling vs git hooks. Lean: git hooks for repo-meaningful changes; fs-watch only for narrow needs.
- **Principal-message channel**: Claude Code interactive (primary). Future: desktop notifications, mobile (1.x feature).
- **Noise filter config**: bespoke YAML — pattern `(actor regex, action regex) → drop`. Lean: YAML edited by principal via PR; discovery loop = incident → principal adds entry. Reference: `event_dispatch_spam_fix` (136 CI Issue-Checks events).
- **Dropped-signal audit**: count-only events (no payload, just `signal_dropped` action + matched-pattern attr) for trail of filter hits. Cheap; required for "principal can answer why X didn't trigger" forensics.

### C12 — Communication with principal
- **Interactive**: Claude Code CLI (primary). Future: native desktop app, voice (1.x).
- **Critical alerts**: notification mechanism — desktop notifier vs Telegram message vs CLI banner-on-next-session. Lean: CLI banner-on-next-session as default; Telegram supplementary; desktop later.
- **Batched briefs**: morning/evening summary written to a fixed file principal reads on session start vs sent via Telegram, OR using [`session-report`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/session-report) plugin from `anthropics/claude-plugins-official`. Lean: `session-report` plugin as base, file output for cross-session continuity; Telegram optional.
- **CLAUDE.md / SOUL drift management**: bespoke vs [`claude-md-management`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-md-management) plugin. Lean: install the plugin to detect drift; SOUL.md changes still M3 (principal-only) per C15.

### C13 — Budget / cost governance
- **Cost ledger**: SQL views over C17 events (already decided in L2). No alternative.
- **Token/cost calculation library**: hand-rolled price tables vs `litellm.cost_calculator` vs `tokonomics`. Lean: [`litellm.cost_calculator`](https://github.com/BerriAI/litellm/blob/main/litellm/cost_calculator.py) — `pip install litellm`, `from litellm import completion_cost, cost_per_token`; reads in-memory `litellm.model_cost`, fully offline, **no proxy** (fits `architecture_final`). [`tokonomics`](https://github.com/phil65/tokonomics) (`pip install tokonomics`) as fallback if LiteLLM upgrade pace becomes a problem (auto-cached pricing JSON, refreshes 24h).
- **Pre-call token counting**: `tiktoken` for OpenAI-compatible models; Anthropic SDK exposes its own counter.
- **Heartbeat probes**: scheduled task per service. Established.
- **Reconciliation**: provider billing API where exposed (Anthropic Console, OpenAI usage API, Voyage). Where not exposed (GHA minutes), gh API + parsing.
- **Model router**: declarative config (yaml/json with rules) vs LLM-as-router. Lean: declarative — reproducible, calibrated. [LiteLLM YAML routing config](https://docs.litellm.ai/docs/proxy/users) format as reference shape for our hook-based router (routing rules, not the proxy itself).
- **Different-provider for high-leverage review**: OpenAI GPT-5.4 vs Gemini 2.5 Pro vs both alternating. Lean: GPT-5.4 — current pricing favors it; revisit if Gemini's fall.

### C14 — Security & privacy
- **Existing Sprint 1 components** (secret scanner, credential registry, protected-files hook, action gate, gitleaks): kept.
- **Hook-event audit (close forensic gap)**: hooks emit events to C17 substrate. Lean: this; just engineering work.
- **HIBP probe**: scheduled task → haveibeenpwned API. Established API, free for low volume.
- **Compromise heuristics**: pg query over events + threshold rules. Lean: SQL-based (deterministic, debuggable); LLM only for "this looks weird, unsure" surfacing.
- **Supabase RLS**: pg RLS — standard. Coordination with redrobot required (cross-project class).
- **Principal verification**: bash check at session bootstrap reading process tree + env. Trivial.

### C15 — Self-improvement
- **Modification dispatch**: same /self-improve skill as entry; pipeline rebuilt per L2 to use C5/C6/C16/C17.
- **Trust-ladder state**: stored in C3 facts (`self_improve_class_<name>` memories with maturity counters) or in a small `m1_unlocks` table. Lean: facts — they're the natural home, no schema overhead.
- **Misimprovement detection**: SQL queries over C17 events comparing claimed-benefit metrics vs observed outcomes. Lean: this.
- **Bootstrap-protection enforcement**: PR check workflow (GitHub Action) reading event substrate to confirm prior-version review. Lean: GHA — already established for CI.
- **Safeguard layers (prior-version-review is novel — add parallel)**: per [Darwin-Gödel Machine (arXiv:2505.22954)](https://arxiv.org/abs/2505.22954) and [SiriuS](https://openreview.net/forum?id=Mz2JYufbg4g) — empirical benchmark + sandboxing + human oversight, not prior-version alone. Lean: stack all four for C6/C16/C17 modifications (prior-version + benchmark + sandbox + principal sign-off). DGM concedes "proving most changes net beneficial is impossible" — defense in depth required.
- **Tier-graduation alignment check**: per [biosecurity-agent lifecycle (bioRxiv 2025.09.17)](https://www.biorxiv.org/content/10.1101/2025.09.17.676717v1.full.pdf) — formal alignment check before each M-tier promotion. Lean: structured checklist (regression metrics + reviewer sign-off + SOUL alignment), not metric thresholds alone.

### C16 — Verification / QA
- **Reviewer base**: bespoke build vs fork [Anthropic Code Review plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review). Lean: **fork the plugin** — Apache-2.0, source-available; ships 5 parallel reviewers (CLAUDE.md compliance / shallow bugs / git-blame context / prior PR comments / code-comment compliance) + Haiku 0–100 confidence with 80 threshold + verifier second-pass. Substantive PR comments rose 16% → 54%, <1% wrong-finding rate. ~70-80% of C16 covered by fork. **First sprint task per `v2_open_questions_resolved_2026_04_27`.**
- **Reviewers added on top of fork**: diff-coherence (claimed-edits vs `git diff` — subagent fabrication detection, NOT in plugin), cross-device integrity, smoke-test. Implemented as additional parallel agents in the plugin's command Markdown file.
- **Mechanical reviewers**: Python scripts (diff coherence via git, test coverage via AST) running as pre-merge GHA. Lean: this.
- **LLM reviewers**: Claude Code subagent (peer-Jarvis, via plugin fork) for routine; OpenAI GPT-5.4 / Gemini 2.5 for different-provider on high-leverage. Lean: GPT-5.4 first (cost), Gemini as A/B comparison later.
- **Smoke test reviewer**: subprocess + temp dir for scripts; ephemeral docker for services if needed. Lean: subprocess minimal (heavy infra deferred).
- **Cross-device integrity**: device-profile YAML files (3 devices) consulted by reviewer to simulate paths. Lean: YAML profiles, simulation as Python script not LLM where possible.
- **Aggregator**: PR-comment posting via gh API summarizing reviewer outputs. Lean: this; Anthropic plugin already implements aggregator pattern.
- **Other useful plugins to install**: [`pr-review-toolkit`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/pr-review-toolkit), [`hookify`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/hookify), [`mcp-server-dev`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev).

### C17 — Observability & audit
- **Substrate**: PostgreSQL events table (single canonical) per L2. No alternative.
- **Event column naming**: bespoke schema vs [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Lean: **OTel GenAI verbatim** — `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.provider.name`, `gen_ai.agent.id`, `gen_ai.tool.name`, `gen_ai.conversation.id`, `gen_ai.operation.name`. No canonical `gen_ai.cost` exists yet — invent `gen_ai.usage.cost_usd` consistent with prefix.
- **Real-time event delivery**: pg LISTEN/NOTIFY for handlers needing real-time (C5 dispatcher, alerts). Lean: this.
- **Trace propagation**: stdlib `contextvars.ContextVar[str]` + `uuid.uuid4().hex` + parent-event linkage. Lean: this — no library improves on this at our scale.
- **Instrumentation library (optional)**: bespoke `events_insert()` calls vs OTel-shaped instrumentation. Lean: optional — `traceloop-sdk` ([OpenLLMetry](https://www.traceloop.com/docs/openllmetry/configuration)) with custom Supabase `SpanExporter` subclass (~30 LOC); `Traceloop.init(exporter=YourSupabaseExporter())` bypasses their SaaS, routes OTel spans into our events table. Defer until volume justifies; bespoke `events_insert()` is fine for cold-start.
- **View materialization (closes Q1 reframe)**: simple SQL views vs materialized views with `pg_cron` refresh. Lean: **materialized views from day one** for hot read paths (cost-by-day, decisions-by-trace, last-run-by-actor) — `pg_cron` already enabled on Supabase; `cron.schedule('0 * * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY events_cost_by_day_mv')`. Per Honeycomb / Greptime "Observability 2.0" guidance — single-canonical-events table needs read-side projections to avoid read-amplification.
- **Dashboards**: SQL views queried via /status skill output OR Grafana/Metabase later. Lean: SQL views via CLI initially; visual dashboard is 1.x.
- **Cost-event reconciliation**: scheduled job pulling provider billing APIs and writing reconciliation events. Lean: this.

---

L3 marked. Final tech decisions deferred to implementation against real benchmarks (per `Design-to-evaluate` op policy).

---

## Final architecture (C4)

Three views — Context, Container, Component — in [jarvis-architecture-c4.md](jarvis-architecture-c4.md).

The single load-bearing edge across the architecture is **everything → C17**: events as canonical substrate means audit, reflection, calibration, cost tracking, and review all share the same data. Capabilities compose by reading from / emitting to this substrate, not by direct cap-to-cap calls.

---

## v1 exit criteria

Before shifting effort to the new architecture's 1.x feature roll-out, the current Jarvis (v1) needs to reach a stable baseline. Stability here = "the daily-pain list is empty enough that the system isn't competing with itself for attention."

Required for v1 stable:
- Memory contradictions visible to current recall do not actively mislead in daily ops (current pain class — bi-temporal + supersession in C3 fixes this, but interim is acceptable if it doesn't cause incidents).
- Subagent fabrication rate ≤ 1 per 30 dispatches via existing manual-review discipline (until C16 ships).
- Cost visibility — principal can answer "how much did Jarvis cost me last month" within 1 minute (precondition for any A/B routing experiment).
- No active blocker on cross-device usage (keys, MCP portability, principal env).
- Critical security defenses (Sprint 1) operational on all 3 devices.

Once met, 1.x feature roll-out begins per the migration order above. v1 stays "current" and gets patched until each 1.x capability replaces its predecessor.

## Pillar → capability mapping

Pillars are long-lived capability groups (per `pillar_is_not_one_task` — they don't close after one sprint; they accumulate maturity). Capabilities are the planning unit; pillars provide a human-friendly grouping.

| Pillar (long-lived) | Capabilities (planning units) |
|---|---|
| Memory | C3 (Memory store), C5 (Reflection / learning), C17 (Observability — feeds episodic) |
| Identity & Strategy | C1 (Identity & values), C2 (Goals & priorities) |
| Cognition | C4 (Reasoning & planning), C6 (Decision gating), C18 (Proactive challenger) |
| Action | C7 (Execution), C8 (Sub-orchestration), C9 (Tool / env interface), C10 (Research) |
| Interface | C11 (Perception), C12 (Communication with principal) |
| Stewardship | C13 (Budget), C14 (Security & privacy), C15 (Self-improvement), C16 (Verification) |

Pillars are how progress gets summarized; capabilities are how work gets sliced.

## Sanity check vs prior vision

Independent re-derivation compared to `jarvis_v2_vision` + `jarvis_v2_hybrid_agile`:

- **Convergent on core** (10 themes): memory primacy, cloud-trust threat model, outcome-driven learning, autonomous loop, goals separate from memory, identity separate from memory, sub-orchestration, decisions outside memory, research-gated, security cloud-trust. Strong signal that the core direction is right.
- **Scope divergence (deliberate per L0)**: prior framing was "universal personal AI agent" with personal life included; this architecture is principal-only, work-only. Personal life, TTS/STT, broader data ingestion, open-source split → 1.x feature backlog, not separate version.
- **Structural additions in this design** (formalization, not scope creep): C6 single canonical gate, C13 budget governance, C15 modification tiers + bootstrap protection, C16 reviewer independence, C17 events as canonical substrate, "design-to-evaluate" op policy, bootstrap protocol.

No findings forced re-decision. Prior vision's broader scope captured in 1.x backlog rather than in this architecture.
