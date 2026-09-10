# Sandcastle integration — design

**Purpose.** This document is the queryable architectural narrative for the Sandcastle subsystem. It points at the load-bearing decisions; rationale lives in the `decision_made` episodes referenced by UUID. When a future session asks "why does sandcastle work this way?" — `memory_get` the cited episode.

**Scope.** Docker-isolated AFK coding-agent loop that picks `sandcastle`-labelled issues, works one at a time on local Ollama inside a sterile container, and opens PRs without ever merging. Production target is the Workshop PC (always-on physical host; AFK loop runs inside safe-hours window — no persistent software daemon).

**Read order.** `CONTEXT.md` glossary (Sandcastle / Watchdog / Safe-hours window / Threat-model duality) → this doc → cited decision episodes for rationale.

---

## Architectural commitments

These are the non-negotiable invariants that shape every other slice. Each commitment maps to one or more decision episodes.

1. **No autonomous merge — orchestrator is the only gate to `main`.** The sandcastle agent opens a PR, records an outcome, stops. A live orchestrator session reviews the diff and decides merge.
   - Decision: `436f9549-3acf-4ee0-85e5-c7259735d62e` (Orchestrator boundary).

2. **Sterile container — no host `~/.claude` mount.** The image bakes in skills, MCP config, and the memory bridge. No outbound mount from the host, so a compromised iteration cannot exfiltrate or mutate host credentials or skill definitions.
   - Decision: `894ac658-67da-4f32-a0a2-5b5ebefac8ee` (Runtime: Claude Code + Ollama).
   - Decision: `228a2d9b-b57a-4d0f-8771-662482386b8a` (Memory bridge inside).

3. **Memory bridge inside, table-level provenance gate.** The container talks to the memory MCP with anon credentials; RLS demands `source_provenance LIKE 'sandcastle:%'` for every anon INSERT/UPDATE/DELETE on memories/outcomes/episodes/events. The host MCP uses the service key for non-sandcastle writes.
   - Decision: `228a2d9b-b57a-4d0f-8771-662482386b8a` (Memory bridge).
   - Cross-link: CONTEXT.md invariant "Sandcastle provenance gate is table-level + op-level, not agent-level".

4. **Protected-file enforcement is host-side.** The container runs as `JARVIS_PRINCIPAL=subagent`; protected-file edits and protected-file mirrors are blocked by hooks. Inside the worktree, copy-on-write semantics keep any runtime overwrite of tracked files from leaking to the host.
   - Decision: `6ce7902c-5697-4b89-b642-9a84c4b9c459` (Protected-file enforcement).

5. **Parallelism cap of one iteration per host.** A single iteration runs at a time per host. The infrastructure does not multiplex containers — each Watchdog invocation runs one container, waits, parses result, exits. The decision documents future capacity but pins current behaviour to N=1.
   - Decision: `b8f7de1d-1214-4e36-a566-516fe1dc26bc` (Parallelism cap).

6. **Failure modes are observable, not silenced.** Every iteration writes an `outcome_record` row (success / partial / failure). Telegram fires only when infrastructure cannot come up — not for agent failures, which are signal, not noise.
   - Decision: `0c3017c6-01ec-4392-86a1-6a1522e4c5ef` (Failure modes).

7. **Workshop PC is the production target; Main PC is the dev/test bench.** The Workshop host (always-on physical machine; no persistent software daemon) runs the safe-hours-bound AFK loop; the developer bench reproduces the runtime for iteration without touching prod data.
   - Decision: `4890aa35-07ae-4e4c-8c4b-ec37e749d751` (Deployment shape).

8. **Model escalation chain, Ollama-first.** Default model is local Ollama; the escalation chain (per-iteration retry on a stronger model on parse/hard-failure, with API/Sonnet as the final escape hatch) preserves the cost profile of the AFK loop while keeping a path through hard issues.
   - Decision: `f8e27d53-db5c-4aac-9dee-c3290a53c49a` (Model escalation chain, Q5 addendum).

9. **Supervisor spawn is the default execution path, with an explicit opt-in kill-switch back to bare spawn (#1121).** `task_queue` rows carry a `substrate` column (`c5e2e14a-4750-4e9d-bb26-9d44b9ef5b02`, defaulted from `config/sandcastle.yaml`'s `operator_default_substrate` for pre-slice rows); a `"worktree"` value routes the drain loop onto `agents/sandcastle_supervisor.py`'s `supervisor_spawn` instead of `agents/executor.py`'s bare `claude -p`. The supervisor path re-derives the sterile-container and provenance-gate guarantees above (commitments 2-3) at the process-spawn boundary: it validates the forwarded `SUPABASE_KEY` by role (anon only) and strips billing-override env vars (`config/sandcastle.yaml`'s `billing_key_denylist`, decision `70f25333-b4f4-454e-903b-ab2d32b125c8`) before launch. The bare path is not deleted — it stays reachable behind `JARVIS_DISABLE_BARE_SPAWN` (unset by default; `#1121` plan step 9), a rollback switch flipped only once step 23's live Workshop-host verification confirms the supervisor path works end-to-end. A per-row `substrate` value always wins over the operator default, so this is a routing default, not a hardcoded path.
   - The container substrate additionally attaches to a dedicated docker network, `sandcastle-jarvis` (`config/sandcastle.yaml`'s `network` key; `#1121` plan step 13) — segmentation only (no reachability to other containers/services on the default bridge), explicitly not egress filtering. Created once via `docker network create sandcastle-jarvis` on the host before first run.
   - Quota gate: a per-row tier-aware throttle (decision `cc5f9c2c-4371-4cec-bd54-79b7163c398c`) skips a throttled row at drain time rather than halting the drain.

---

## Component shape

```
host (Workshop PC, safe-hours window)
└── Watchdog (scripts/sandcastle/Run-Sandcastle.ps1)
    ├── Pre-flight — Docker up, Ollama up, runtime-dir sweep
    ├── drain_tasks — row.substrate == "worktree"? ──► supervisor_spawn (#1121)
    │                                                   ├── SUPABASE_KEY role check (anon only)
    │                                                   ├── billing-key-denylist strip
    │                                                   └── npm run sandcastle ─►  sandcastle:jarvis
    │                                                        container, network=sandcastle-jarvis
    │                                                        ├── Claude Code CLI (sterile)
    │                                                        ├── Skills + .mcp.json (baked)
    │                                                        ├── Memory MCP (anon → Supabase)
    │                                                        └── Local Ollama (host loopback)
    │                        else (or JARVIS_DISABLE_BARE_SPAWN unset) ──► bare `claude -p`
    │                                                   (agents/executor.py:spawn, legacy path)
    ├── Parse result.json
    ├── Write-OutcomeRecord ────────► task_outcomes (PostgREST + service key)
    └── Telegram on infra-down ONLY
```

**Watchdog** is a deep module: single command interface (`Run-Sandcastle -Repo <name> -WindowEnd HH:MM`), large hidden implementation (Docker/Ollama bring-up, env propagation, result parsing, outcome write, retention sweep). See CONTEXT.md glossary for the canonical definition.

**The container** is sterile: no host secrets mount, no `~/.claude` mount, no developer tooling beyond what the image bakes in. The agent talks to the memory MCP with anon credentials and writes are gated by RLS provenance.

**The orchestrator** is the live Claude Code session that the owner drives. It reads PRs, reviews diffs in the main repo tree (never trusts the worktree), and decides merges. Subagent dispatch (`/delegate`) shares the same boundary — sandcastle is the AFK variant of the same orchestrator-as-gate pattern.

---

## Iteration lifecycle

One sandcastle iteration = one container run = one PR (or one recorded failure).

1. Pick the highest-priority open `sandcastle` issue not labelled `status:in-progress` (or honour `SANDCASTLE_TARGET_ISSUE` for escalation retries).
2. Recall first — mandatory memory bridge call before any other MCP call.
3. Claim — `status:in-progress` label + comment with branch name.
4. Implement — TDD when tests are non-trivial; preserve existing values unless the issue mandates change.
5. Commit + PR — `Closes #<N>` in the body.
6. Record outcome — one `outcome_record` per iteration, success or failure.
7. Stop. The orchestrator merges in a separate session.

The `prompt.md` baked into the container is the source of truth for this lifecycle; this section paraphrases.

---

## Safe-hours window

AFK iterations run only within a clock-bound interval (e.g. 22:00–08:00) configured per host. The window is enforced by **soft-stop**: no kill mid-iteration, only refusal to start a new one once the window closes. This optimises for "no loss of WIP" over strict scheduling — an iteration that started in-window completes even if it crosses the boundary.

Window enforcement is the Watchdog's responsibility, not the container's. The container has no clock contract; it runs until done or until the host kills it.

---

## Threat model

Two threat-model halves (CONTEXT.md invariant: Threat-model duality).

- **Inside the container.** Untrusted-ish: the agent may write bad code, hit prompt-injection content in issues, or produce a malformed PR. Mitigation = sterile image (no host secrets reachable) + orchestrator-as-merge-gate. Host-side defences are redundant here.
- **From the container outward.** Anon memory writes are gated by table-level RLS on `source_provenance LIKE 'sandcastle:%'`. Non-sandcastle writes require the service key, which never enters the container.

Stacking host-grade defences inside the container adds friction without adding security. See memory `enforcement_layer_matches_threat_model`.

---

## Non-goals

Explicitly out of scope for this subsystem.

- **No autonomous merging.** The agent never has the credentials or the protocol path to merge. Adding it is a change to the orchestrator boundary (decision `436f9549`), not a Watchdog feature flag.
- **No host `~/.claude` mount.** Sharing skills via volume mount is the failure mode the sterile image was designed to prevent. Skills change → image rebuild.
- **No multi-iteration concurrency on a single host.** Parallelism cap of one is pinned (decision `b8f7de1d`). Two iterations require two hosts.
- **No Telegram for agent failures.** Agent success/failure is recorded in `outcome_record`; the orchestrator review is the surface that reads it. Telegram is reserved for "infra cannot come up" (Docker dead, Ollama unreachable, Supabase unreachable).
- **No cross-repo iteration in one container run.** One container = one repo. The Watchdog dispatches `-Repo jarvis` or `-Repo redrobot` separately.
- **No agent-driven memory promotion past `requires_review`.** Sandcastle-written memories enter the review queue like any other Deriver candidate; the owner accepts before they influence recall.
- **No model selection by the agent at runtime.** The model is pinned by the Watchdog invocation; the escalation chain (decision `f8e27d53`) is orchestrator-driven, not agent-driven.

---

## Decision index

| UUID | Topic |
|---|---|
| `436f9549-3acf-4ee0-85e5-c7259735d62e` | Orchestrator boundary (no autonomous merge) |
| `6ce7902c-5697-4b89-b642-9a84c4b9c459` | Protected-file enforcement (host-side) |
| `b8f7de1d-1214-4e36-a566-516fe1dc26bc` | Parallelism cap (N=1 per host, future capacity documented) |
| `894ac658-67da-4f32-a0a2-5b5ebefac8ee` | Runtime: Claude Code CLI + local Ollama |
| `0c3017c6-01ec-4392-86a1-6a1522e4c5ef` | Failure modes (outcome_record always; Telegram on infra only) |
| `4890aa35-07ae-4e4c-8c4b-ec37e749d751` | Deployment shape (Workshop = prod, Main = bench) |
| `228a2d9b-b57a-4d0f-8771-662482386b8a` | Memory bridge inside container |
| `f8e27d53-db5c-4aac-9dee-c3290a53c49a` | Model escalation chain (Ollama-first, API escape hatch) |
| `c5e2e14a-4750-4e9d-bb26-9d44b9ef5b02` | `substrate` queue-row column + operator-default fallback (#1119/#1121) |
| `70f25333-b4f4-454e-903b-ab2d32b125c8` | Shared billing-key denylist (TS guard + Python spawn-env guard, #1121) |
| `cc5f9c2c-4371-4cec-bd54-79b7163c398c` | Per-row tier-aware quota gate (#1121) |

`memory_get(name=<slug>)` or `memory_get` by UUID retrieves the full rationale, alternatives, and confidence for each.

---

## See also

- `CONTEXT.md` — glossary (Sandcastle, Watchdog, Safe-hours window) + Threat-model duality invariant
- `.sandcastle/prompt.md` — operational source of truth for the iteration lifecycle
- `.sandcastle/Dockerfile` — sterile image build
- `scripts/sandcastle/Run-Sandcastle.ps1` — Watchdog implementation
- `.claude/skills/delegate/SKILL.md` — orchestrator-as-merge-gate, applied to live subagent dispatch
- Memory `enforcement_layer_matches_threat_model` — Threat-model duality rationale
