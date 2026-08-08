# Jarvis Architecture — C4 Views (revised 2026-04-29)

Four views: Context (system in environment), Container (runtime + storage), Component (18 capabilities in 5 layers + cross-cutting), Event-substrate dataflow (the load-bearing edge: `everything → C17`). Companion to [`jarvis-v2-redesign.md`](jarvis-v2-redesign.md). C18 (Proactive challenger) added in Phase C (2026-04-29) per `audit_3_main_changes_lock_2026_04_28`; mermaid sources below include c18, SVG outputs need re-render.

**Status (2026-05-17):** the diagrams below mix target architecture and current deployment. Specific known mismatches: (a) Container view shows **Plan MCP (LangGraph carveout)** and **Research MCP (GPT Researcher + others)** as deployed MCPs — both are *planned*, not in `.mcp.json` yet; (b) Level 4 sequence diagram lists `decisions_by_trace_mv` among refreshed projections — only `events_cost_by_day_mv` and `events_last_run_by_actor_mv` exist in the canonical migration (`supabase/migrations/20260429145000_create_events_canonical.sql`); `decisions_by_trace_mv` is a planned projection. Treat the diagrams as the design target; cross-check `mcp-memory/schema.sql` + `.mcp.json` for actual deployment. Reflects L3 concrete adoptions folded in 2026-04-27/28 — see [`jarvis-build-vs-buy.md`](jarvis-build-vs-buy.md).

**Update (2026-07-08, #1137):** the **Plan MCP** in the Container view was a planned **LangGraph carveout**; LangGraph has since been retired project-wide (#743/#744), and the reactive-core (M44 — `wake_driver` + `executor` + `orchestrator` + a Supabase `task_queue`) replaced the retired dispatcher. The mermaid source below now drops the retired "LangGraph" token (Plan MCP shown as *planned*, backing tech TBD). This Container view predates M44 and does **not** yet render the reactive-core containers — a full M44 Container-view revision is design work, tracked separately, not part of this regeneration.

Rendered SVGs alongside each block: [c4-1.svg](c4-1.svg) Context · [c4-2.svg](c4-2.svg) Container · [c4-3.svg](c4-3.svg) Component · [c4-4.svg](c4-4.svg) Event dataflow. Re-render: `npx -p @mermaid-js/mermaid-cli mmdc -i jarvis-architecture-c4.md -o c4.svg` (writes `c4-1.svg`…`c4-4.svg`).

## C4 Level 1 — Context

![Context diagram](c4-1.svg)

```mermaid
flowchart LR
    principal((Principal))
    jarvis[Jarvis<br/>Personal AI agent<br/>Claude Code-native + Supabase]

    principal -->|interactive + queue approvals| jarvis

    jarvis -->|inference Max sub| claude_api[Anthropic Claude]
    jarvis -->|different-provider review| other_llm[OpenAI / Gemini]
    jarvis -->|memory + events + queues| supabase[(Supabase Postgres<br/>pgvector + pg_cron)]
    jarvis -->|embeddings| voyage[VoyageAI]
    jarvis -->|repos + Actions| github[GitHub]
    principal -->|issues + comments + corrections| github
    jarvis -->|credential probes| hibp[HaveIBeenPwned]
    jarvis -->|fork base + install| plugin_eco[Anthropic plugins<br/>claude-plugins-official + skills]
```

## C4 Level 2 — Container

![Container diagram](c4-2.svg)

```mermaid
flowchart LR
    principal((Principal))

    subgraph SURFACE[Claude Code surface]
        direction TB
        claude_code[Interactive session<br/>native + MCP + plugins]
        scheduled[Routines<br/>scheduled tasks]
        hooks[Hooks<br/>6-outcome gate]
        subagents[Subagents<br/>worktree + post-gates]
    end

    subgraph PLUGINS[Plugins]
        direction TB
        plugin_review[Code Review<br/>fork base]
        plugin_misc[session-report<br/>hookify + others]
    end

    subgraph MCPS[MCP servers]
        direction TB
        mcp_memory[mcp-memory<br/>pg_bitemporal + libs]
        mcp_plan[Plan MCP<br/>planned]
        mcp_research[Research<br/>GPT Researcher + others]
    end

    subgraph STORAGE[Supabase]
        direction TB
        pg[(Postgres<br/>pgvector + pg_cron)]
        cloud_tasks[Scheduled SQL]
    end

    subgraph EXT[External]
        direction TB
        claude_api[Claude API]
        other_llm[OpenAI / Gemini]
        voyage[VoyageAI]
        github[GitHub]
        hibp[HIBP]
    end

    principal --> claude_code
    claude_code --> hooks
    claude_code --> subagents
    claude_code --> PLUGINS
    claude_code --> MCPS
    claude_code --> claude_api

    scheduled --> MCPS
    scheduled --> claude_api
    scheduled --> other_llm
    scheduled --> hibp

    MCPS --> pg
    mcp_memory --> voyage
    mcp_research --> voyage
    PLUGINS --> pg
    hooks --> pg
    subagents --> pg
    cloud_tasks --> pg
    github --> pg
```

## C4 Level 3 — Component

![Component diagram](c4-3.svg)

Detailed L3 tech adoptions per cap live in [`jarvis-v2-redesign.md` §L3](jarvis-v2-redesign.md#l3--technologies--patterns). Here we draw only the load-bearing dataflow edges so the structure stays readable.

```mermaid
flowchart TB
    subgraph IDENT[Identity]
        direction LR
        c1[C1 Identity]
        c2[C2 Goals]
    end

    subgraph COG[Cognition]
        direction LR
        c3[C3 Memory]
        c4[C4 Reasoning]
        c5[C5 Reflection]
        c6[C6 Decision gating]
        c18[C18 Proactive challenger]
    end

    subgraph ACT[Action]
        direction LR
        c7[C7 Execution]
        c8[C8 Sub-orchestration]
        c9[C9 Tool / env]
        c10[C10 Research]
    end

    subgraph IFACE[Interface]
        direction LR
        c11[C11 Perception]
        c12[C12 Communication]
    end

    subgraph CROSS[Cross-cutting]
        direction LR
        c13[C13 Budget]
        c14[C14 Security]
        c15[C15 Self-improve]
        c16[C16 Verification]
        c17[(C17 Observability<br/>canonical events substrate)]
    end

    c1 --> c6
    c2 --> c5
    c2 --> c6
    c5 --> c2
    c11 --> c17
    c11 --> c18
    c6 --> c17
    c6 --> c3
    c5 --> c17
    c5 --> c3
    c4 --> c10
    c4 --> c17
    c8 --> c17
    c9 --> c17
    c16 --> c17
    c16 --> c5
    c15 --> c16
    c13 --> c17
    c12 --> c6
    c18 --> c12
```

## C4 Level 4 — Event-substrate dataflow

The single load-bearing edge of the architecture: every cap reads from / writes to C17 events. This view shows ordering, projection refresh, and the reflection mutation arm.

![Event dataflow](c4-4.svg)

```mermaid
sequenceDiagram
    autonumber
    participant CC as Claude Code session
    participant Hook as PreToolUse hook / C6 gate
    participant Tool as Tool exec / C7
    participant SA as Subagent / C8
    participant Pg as Supabase events / C17
    participant MV as Materialized projections / pg_cron
    participant C5h as Reflection handlers / C5
    participant C3w as Memory write fn / C3
    participant C13r as Budget views / C13
    participant C16r as Reviewer / C16

    CC->>Hook: tool_call action target payload
    Hook->>Hook: classify with state probes - git, convergence, harness
    Hook->>Pg: emit decision_made event - auto, no manual record_decision

    alt outcome ALLOW or LOG_AND_PROCEED or EXPLAIN
        Hook-->>CC: PROCEED
        CC->>Tool: invoke
        Tool->>Pg: emit tool_call event - OTel GenAI semconv with duration_ms, cost_usd, model
    else outcome DEFER per v2.1.89
        Hook-->>CC: pause then resume after async state probe
    else outcome QUEUE or BLOCK
        Hook->>Pg: emit gated event - queue_id or block_reason
        Hook-->>CC: hold
    end

    par Subagent activity
        SA->>Pg: emit events with trace_id - parent-event linkage, Magentic ledger handoff
    and Projection refresh
        Note over Pg, MV: pg_cron hourly REFRESH MATERIALIZED VIEW CONCURRENTLY
        Pg->>MV: events_cost_by_day_mv
        Pg->>MV: decisions_by_trace_mv
        Pg->>MV: last_run_by_actor_mv
    end

    Note over Pg, C5h: pg LISTEN/NOTIFY on outcome_recorded, principal_correction, anomaly_flagged, recall_failed
    Pg-->>C5h: trigger handler - synthesizer or stale-challenge or calibrator
    C5h->>C5h: pattern extraction with crepes Mondrian CP for sparse classes
    C5h->>C3w: candidate fact - auto-write if confidence above class threshold, else queue
    C5h->>Pg: emit judgment_made or mutation_proposed or stale_challenge_fired

    MV-->>C13r: cap-proximity reads
    C13r-->>Hook: enrich state probe with budget context

    Note over Pg, C16r: PR opened - reviewer pipeline reads events by trace_id
    C16r->>Pg: read claimed_files vs git diff - subagent fabrication detection
    C16r->>Pg: emit reviewer FP/FN labels feeding C5 calibrator

    Note over Pg: Single canonical events table. OTel GenAI columns verbatim. trace_id via contextvars + uuid. Best-effort write semantics - NOTIFY plus row insert. Recall and write never block on event-row failure.
```

**Deployment status for the projection refresh sequence (Level 4):** as of 2026-05-17, `events_cost_by_day_mv` and `events_last_run_by_actor_mv` are deployed with `pg_cron` schedules; `decisions_by_trace_mv` shown in the sequence above is planned (no migration yet).

## Reading guide

- **Identity layer** is principal-authored axioms — the alignment substrate. Never auto-mutated (M3). Drift detection delegated to `claude-md-management` plugin (C12).
- **Cognition layer** is what Jarvis *thinks with*. C3 is the durable substrate (bi-temporal facts + episodic events); C4 sequences work on a plan store; C5 is the active loop that mutates C3 from C17 events with class-conditional calibration; C6 is the act/ask classifier consulted before every tool call.
- **Action layer** is what Jarvis *does*. C7/C8/C9 are the runtime; C10 is the external info-gathering arm with GPT Researcher as primary engine.
- **Interface layer** is the boundary with the principal — C11 ingests with a YAML noise filter; C12 communicates out via CLI + Anthropic plugins.
- **Cross-cutting** layer wraps everything: C17 is the substrate every event passes through; C13/C14/C16/C15 are governance/safety/quality/evolution functions that consume and gate.

The single most load-bearing edge is **everything → C17** (visualized in Level 4): substrate-as-source-of-truth means audit, reflection, calibration, cost, and review all share the same data. Materialized projections per hot read path (per Q1 reframe — Honeycomb / Greptime "Observability 2.0" guidance) prevent read-amplification on long streams.

## What changed vs prior C4 (2026-04-27)

- **Level 1**: added Anthropic plugins ecosystem as an external system (fork-source + install-target).
- **Level 2**: switched from `C4Container` DSL to `flowchart TB` with subgraph zoning (cleaner auto-layout, less arrow overlap). Split MCP layer into 3 distinct MCPs (memory, plan/LangGraph carveout, research bundle) + plugin layer (forked code-review + installed plugins). pg now explicitly shows pg_bitemporal column shape, OTel GenAI semconv columns, materialized projections, decision_queue.
- **Level 3**: switched from `C4Component` DSL to `flowchart TB` with layer subgraphs. Every component description now carries the concrete L3 adoption (lib name / SQL file / plugin URL) instead of generic "current pattern" language. C6 gate enum expanded to 6 outcomes; C8 worktree gates explicit; C15 stacks 4 safeguard layers; C16 fork-base named; C17 names projection set.
- **Level 4 (new)**: event-substrate dataflow sequence — visualizes the load-bearing edge, including DEFER outcome, parallel projection refresh, LISTEN/NOTIFY trigger to C5, and best-effort write semantics from C3-Q2.
