# Architecture review — pass 2 (2026-04-27)

Critical-review companion to [`jarvis-v2-redesign.md`](jarvis-v2-redesign.md) and [`jarvis-architecture-c4.md`](jarvis-architecture-c4.md). Principal-invited two-pass critique (vacuum first, then memory-informed). Principal reframed mid-review: pass-1/2 findings are mostly realism/implementation, not architecture; pass-3 below collects the genuinely design-level concerns.

External SOTA validation in flight (2 parallel research agents, 2026-04-27) on memory architectures, event-substrate contracts, reviewer calibration, sparse-N calibration, self-modification recursion, Claude Code 2026 changes. This file gets updated when they return.

**Status (2026-05-17):** this doc is the review snapshot; Q7 already carries an inline `Resolved 2026-04-29 (Phase C)` marker. The §"External SOTA findings" `→ Action:` items (materialized projections discipline, hierarchical CP calibration, per-reviewer FP/FN sequencing, self-modification safeguards, Letta sleep-time consideration, alignment-check-per-promotion, harness-design framing) were folded into `jarvis-v2-redesign.md` during Phase B/C (2026-04-28/29) without individual resolution markers being back-stamped here. Verify each Action's adoption status via `git log docs/design/jarvis-v2-redesign.md` or grep for the keyword (e.g. "materialized projection", "Hierarchical CP", "harness coupling") in the redesign doc. The Net-effect table near the end is the most reliable summary; the inline `Action:` lines should be read as proposals, not open work.

---

## Pass-3 — design-level open questions (architecture-altering)

### Q1. C17 single-table contradicts C3-Q1 reasoning

C3 explicitly rejected single-table-with-`kind`-flag: *"different write rules, indices, scoring; forces every query to filter, every write rule to branch — exactly the current pain."*

C17 prescribes the diametric opposite: ONE events table with `action` field; specialized tables (decisions, outcomes, audit_log, episodes, known_unknowns, last_run) become views.

Observability lifecycle-разлёт is **wider** than memory's:
- tool-call telemetry — write-once, read-rare, short TTL
- decision ledger — long retention, audit-grade
- cost ledger — heavy aggregation
- hook fires — real-time fan-out

Same logic that split C3 should split C17. The doc applies opposite principles in adjacent chapters without arguing the asymmetry. Either C3-Q1 reasoning is wrong (then revisit), or it applies to C17 too (then split).

### Q2. Event-driven substrate without declared contract

Almost every cap "emits to C17"/"reads from C17". This IS event-driven architecture by another name. The doc doesn't name it as such and doesn't answer its contract questions:

- **Consistency.** Write semantics is "NOTIFY + best-effort row insert" (C3-Q2). Window between "decision committed" and "reader sees event" unspecified. C5 reading "events of last 24h" — does it see a just-emitted C6 decision?
- **Replay.** If C5 reprocesses old events (new handler / bug fix), are mutations to C3 idempotent? Not stated.
- **Ordering.** Hooks emit locally, MCP via server, cloud-tasks via `execute_sql`. Ordering contract — per-`trace_id`? per-actor monotonic? wall-clock `ts`? Each cap will assume its own → drift.

These are substrate contracts that every cap depends on. Not implementation detail.

### Q3. Capability cut by role-axis ignores coupling

Boundaries are role-based (what does it do?), but coupling diagram suggests a different cut:

- **C3 ↔ C5.** C5's only effect is mutating C3; they share the canonical write function and the calibration loop. Two arms of one subsystem, not two caps.
- **C6 ↔ C16.** Both classify the same artifact (high-leverage in C6 → different-provider in C16). Conceptually one rule store, pre/post phases.
- **C13 model-router.** Functionally a tool-call wrapper; lives in C9 logic, not budget cap.

Alternative cut: "Memory subsystem (C3+C5)", "Quality gate (C6+C16)", "Tool layer (C7+C9+C13.router)". Fewer caps, lower cross-cap dataflow through C17. Doc commits to current cut without arguing role > coupling.

### Q4. Calibration math doesn't survive N=1 for rare classes

Thresholds: 30 labels per class (C3, C6), 50 global (C5), 10 per judge (C5 calibrator). For frequent classes — fine. For rare classes ("force-push on stack root" = 2× in 2 months; "cross-project schema migration" ≈ quarterly) — threshold never crossed, judge stays uncalibrated, gate stays on seed config indefinitely. Not a bootstrap issue; structural for N=1.

Architectural answer must be one of:

1. Some classes don't need calibration (deterministic + universal threshold) — explicitly list which.
2. Rare classes pool labels with semantically-near classes (transfer-shape).
3. Rare classes are principal-only by design, no calibration loop attempted.

Doc applies the same calibration shape to all classes. Silent option 3 de-facto, but never written as architectural choice.

### Q5. C1 ↔ C3 boundary is syntactic, not semantic

C1 = "principal-authored axioms"; C5 stale-challenge does NOT touch C1.

But C3 holds feedback memories (`quality_over_speed`, `step_back_after_3_failed_iterations`, `record_decision_when_what`) — axioms by content, in C3 only because the principal didn't put them in SOUL.md by hand. Same content can live either side. Boundary is storage location, not semantics.

If C5 doesn't touch identity but feedback-memories ARE de-facto identity → either C5 touches identity (via memory) or all feedback memories migrate to SOUL. Doc does neither, offers no semantic test "is this an axiom or a fact?".

### Q6. Cross-project synchronization has no architectural component

C8 explicitly rejects cross-repo parallel dispatch: *"isolation primitive cannot enforce isolation across repo roots."*

But `mcp-memory/server.py` + schema + `.mcp.json` shared with redrobot, M3 + cross-project coordination required. Where lives the **protocol** for cross-project sync?
- Not C8 (parallel rejected).
- Not C15 (only classification).
- Not C14 (one line on RLS).

Architectural hole — no component owns sync of two projects on a shared substrate.

### Q7. Harness lock-in not argued as deliberate trade-off

Every L3 decision rests on Claude Code: PreToolUse, `isolation: worktree`, MCP, scheduled tasks via Max.

- Anthropic changes hook semantics → C6 dies.
- MCP auth changes → C9 dies.
- `isolation: worktree` deprecates → C8 dies.

Principal mentions "v2 reserved for paradigm shift (framework swap)" — implicit trade-off acceptance. Should be **explicit at L0**: *"harness lock-in accepted because cost of harness independence > benefit at this scale."* Otherwise future-self asks "why isn't this cap harness-independent?" and the answer stays implicit.

**Resolved 2026-04-29 (Phase C):** explicit L0 §«Harness coupling — deliberate trade-off» added in `jarvis-v2-redesign.md`. Names what's locked (skills/hooks/auto-load/subagent isolation), what's portable (cap layer / schema / MCP / events substrate), the swap cost (~1–2 sprints rewrite of skills+hooks), and the reconsideration trigger.

---

## Pass-1/2 (implementation/realism — v1.x roadmap input, NOT architecture)

### Realism

- **12-step migration order without time-box** → wishlist, not roadmap.
- **Bootstrap-protection chicken-and-egg during build itself**: C6/C16/C17 have no "prior version" to review against during initial implementation.
- **Cost rehearsal numbers ($13/mo Mode A, $50–80/mo Mode B) unfalsifiable until C17 ships** — defer Mode A vs B to M+1 after 30d real data, don't pre-decide.
- **"Benchmarks before implementation" is logically circular** — replace with "evaluation harness before; benchmarks bootstrap from operating data".
- **C6 PreToolUse hook is execute-gate, not decision-gate** — LLM has already decided what to do when the hook fires. Breaks claim "no manual `record_decision` needed — gate auto-emits".
- **Different-provider review during build phase**: ~70–90% of PRs hit high-leverage triggers (schema / cross-project / C6/C16/C17 / security / API contract — exactly the build surface), not the stated 25%.

### Memory-informed gaps

- `untracked_main_tree_leaks_into_subagent_worktree` requires `git stash --include-untracked` explicitly, not generic stash.
- C11 noise-pattern config update mechanism unspecified (`event_dispatch_spam_fix` class — 136 spam events).
- Pillar 5/6/8 (Integrations / Data Intelligence / Identity-Interface) demoted to "1.x backlog" without explicit rationale (bandwidth or mission redefinition?).
- Filename `jarvis-v2-redesign.md` contradicts own semver reframe ("v2 reserved").
- mermaid-cli command in C4 doc references a runtime not on this device → diagrams suffered.
- L0 should link to an RFC/Discussion thread or acknowledge solo design exercise (per CLAUDE.md design RFC process).

### Operational

- `topic_hash` 3/5-attempt convergence threshold → false positives on legitimate refactor sessions; "no progress = no `outcome_recorded(success=true)`" — granularity mismatch (outcomes per-task, edits per-call).
- Stale-challenge sweep on 90+d cold-tail memories → judge never calibrated (no labels for rarely-used).
- Cross-device "simulation" mechanism unspecified.
- M3 list class-creep risk (every new safety-touching addition → M3).
- Skills→templates migration trigger soft ("gradual; pipeline mode kept meanwhile") — same `no_deterministic_pipelines` tension survives v2.
- C7 Execution as Tier-C may be too shallow (tool-fallback chains, rate-limit semantics, observability granularity).

---

## Confirmed by memory (don't change)

- **C5 stale-challenge as missing piece** — `reflection_driven_sprint_2026_04_23` proves the principal hand-mined outcomes; no autonomous mutation arm exists.
- **Memory facts/episodes split** — `metacognition_sprint_plan_*`, `consolidation_plan_*` are real C3 abuse class.
- **Subagent fabrication first-class** — `subagent_fabrication_commit_message_vs_diff` + worktree incidents validate.
- **Single canonical recall API** — closes real cloud-bypass pain.
- **Routing protection** — grounded in $1800-incident class.

---

## Recommended next steps

1. For each Q1–Q7: decide *accept-as-is* / *redesign* / *defer-with-reasoning*. These are real design decisions, not implementation.
2. Pass-1/2 items become v1.x implementation roadmap inputs.
3. External SOTA findings (research agents in flight) appended below when they return.

---

## External SOTA findings (2026-04-27)

Two parallel research agents returned. Below: what shifts in the design after external validation. Full source list at end.

### Strongly validates the design (don't change)

- **Single canonical events table (C17)** — field consensus, "Observability 2.0" / wide-events pattern. Honeycomb, Greptime, OpenTelemetry GenAI semantic conventions all converge on the same shape. **My Q1 critique partially reverses**: the field doesn't agree with C3-Q1's "split or branch on every read" reasoning when applied to observability. Wide-event substrate IS the production answer. → Q1 reframed below.
- **Bi-temporal memory (C3-F)** — direct prior art: **Zep / Graphiti** ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)) implements the same `t_valid`/`t_invalid` + transaction-time model. Stated motivation matches: corrections, supersession, "what was true on date X." This is published best practice as of late 2025.
- **Facts + episodes split (C3-F / C3-E)** — LangGraph standardized this in 2026 (`(users, user_id, "facts")` vs `..., "episodes"`). Mainstream now.
- **Bi-temporal over Mem0-style overwrite** — Mem0's ADD/UPDATE/DELETE/NOOP loses validity windows. The design's choice (bi-temporal) is the right one for retroactive correction.
- **Specialized reviewers (C16)** — Anthropic's own *Code Review for Claude Code* (March 2026): 5 specialist agents + verifier second-pass. Substantive PR comments rose 16% → 54%, <1% wrong-finding rate. The 6-reviewer architecture is on the right side of practice.
- **Subagent fabrication detection via diff (C16)** — now a documented pattern (evanflow framework's "Five Failure Modes"); the `git diff` rule is field practice, not folklore.
- **Hierarchical orchestration over peer federation (C8)** — 2026 orchestration literature converged on hub-coordinator when observability matters. Validates the federated-rejected decision.
- **MAST (arXiv:2503.13657)** remains the canonical multi-agent failure taxonomy — no successor. The design's mitigations (subagent fabrication first-class, structured handoff) sit in the right MAST categories.

### Challenges that should reshape the design

**C-Q1 (revised, replaces my original Q1).** Single-canonical-events is *correct*, but ScienceDirect on event-sourcing observability + Greptime's 100K events/sec writeup flag a concrete failure class: **read-amplification on long streams**. The design's "specialized tables become views" is generic; the field-tested form is **materialized read-side projections from day one** (per hot read path: cost-by-day, decisions-by-trace, last-run-by-actor). Don't build replay logic that re-reads the entire events table. Phoenix, Langfuse, OpenLLMetry all do this. → **Action: promote "views" to "materialized projections" with a per-cap projection list in C17 design.**

**C-Q4 (calibration).** My critique (rare-class never calibrates) is real AND has published solutions the design doesn't use:
- **Hierarchical Conformal Classification** ([arXiv:2508.13288](https://arxiv.org/abs/2508.13288), Aug 2025) — calibrates leaf classes against parent classes in a DAG. "force-push on stack root" → calibrate via parent class "destructive-git" (force-push, hard-reset, branch-delete). Falls back to parent prediction set when leaf has no labels. **This is exactly the partial-pooling pattern Q4 hand-waved.**
- **LLM-informed Bayesian priors** ([arXiv:2509.04250](https://arxiv.org/html/2509.04250) + [Nature SR](https://www.nature.com/articles/s41598-025-18425-9)) — elicit FP/FN priors from the LLM itself, treat as ~5 pseudo-observations to inflate effective sample size for cold-start.
- **Class-similarity CP** ([arXiv:2511.19359](https://arxiv.org/html/2511.19359v1)) — softer relative.

→ **Action: rewrite C6 + C5 calibration sections to specify class hierarchy + LLM-prior bootstrap. Q4 has solutions, ship with them.**

**Per-reviewer FP/FN tracking (C16) is novel.** Anthropic publishes only aggregate <1% wrong-finding rate, no per-reviewer metric. Refute-or-Promote tracks system-level. Combined with sparse-data problem (Q4), per-reviewer calibration is **brittle without prior art to copy.** → **Action: start with aggregate calibration, promote to per-reviewer only after volume justifies. Document the sequencing.**

**Self-modification "prior-version review" (C15) is novel.** Darwin-Gödel Machine ([arXiv:2505.22954](https://arxiv.org/abs/2505.22954)) and SiriuS rely on **empirical benchmarks + sandboxing + human oversight**, NOT prior-version cross-review. DGM concedes "proving most changes are net beneficial is impossible." → **Action: don't rely solely on prior-version review for C6/C16/C17 modifications. Add empirical benchmark + sandbox + human as parallel safeguards. Acknowledge the pattern is novel.**

### New options to consider

- **Letta sleep-time agents** ([Nov 2025](https://docs.letta.com/guides/agents/architectures/sleeptime), v1 Nov 2025) — async secondary agent rewrites memory blocks during idle, every N steps of primary. Maps cleanly onto the design: C3-E episodic stays append-only; sleep-time process produces new C3-F fact heads with provenance. Worth incorporating as a write path (alternative or addition to C5 generation arm).
- **Tier-graduation by formal alignment checks** ([biosecurity-agent paper](https://www.biorxiv.org/content/10.1101/2025.09.17.676717v1.full.pdf), Sep 2025) — closer to M0–M3 ladder than DGM is. Adopt the alignment-check-per-promotion pattern.
- **Calibrated-trust frameworks** (Kai Waehner, [Apr 2026](https://www.kai-waehner.de/blog/2026/04/06/enterprise-agentic-ai-landscape-2026-trust-flexibility-and-vendor-lock-in/)) cap autonomy by stakes/reversibility — same principle as C6, worth cross-referencing.
- **Anthropic harness design for long-running apps** ([engineering blog](https://www.anthropic.com/engineering/harness-design-long-running-apps)) — directly addresses Q7 (harness lock-in). The blog argues for harness-coupled design with explicit acknowledgement; matches the "deliberate trade-off" framing Q7 asked for.

### L3 ecosystem deltas (March–April 2026, affect implementation directly)

The doc was written 2026-04-27; relevant Claude Code / MCP changes since early 2026:

- **PreToolUse `defer` decision** (v2.1.89, Apr 1) — hooks can pause headless and resume. **Directly affects C6 gate design** — `defer` is a new outcome alongside ALLOW/QUEUE/BLOCK.
- **PreToolUse `permissions.deny` correctly overrides hook `ask`** (v2.1.105, Apr 13) — fixed a bug where hooks could downgrade denies. **Audit C6 deny-rules** against this fix.
- **PreToolUse absolute paths** (v2.1.98, Apr 9) — script consumers need to handle.
- **`isolation: worktree` fixes** (v2.1.119, .101, .82) — stale-worktree reuse, subagent Read/Edit denial, cwd leak. **C8 design assumed advisory isolation; these fixes change the baseline.** Re-read C8 against current behaviour.
- **MCP OAuth 2.1 + Resource Indicators (RFC 8707) mandatory** (since 2025-11-25) — affects C9 and `.mcp.json` portability across devices. Auth model changed; design must reflect.
- **MCP `_meta["anthropic/maxResultSizeChars"]` up to 500K** (v2.1.119) — affects MCP recall payload bounds.
- **`TaskCreated` hook + scheduled-task `--resume`/`--continue`** (v2.1.84, .110) — directly relevant for autonomous-loop recovery and C5 dispatcher.
- **`PostToolUse.duration_ms`** (v2.1.118) — free input for C13 cost-by-action attribution and C16 calibration latency feature.

### Net effect on Q1–Q7

| Item | After SOTA | Action |
|---|---|---|
| Q1 (C17 vs C3 single-table inconsistency) | **Reframed, not invalidated.** Wide-event substrate is right; missing piece is materialized projections discipline. | Promote "views" → "materialized projections" with per-cap list. |
| Q2 (event substrate contract) | **Stands.** OTel GenAI conventions still experimental; consistency/replay/ordering not standardized. | Define contract explicitly. |
| Q3 (role-axis vs coupling cut) | Stands as opinion; no SOTA opinion either way. | Principal judgment call. |
| Q4 (sparse-class calibration) | **Strongly validated + concrete fix available.** | Adopt Hierarchical CP + LLM-prior bootstrap. |
| Q5 (C1↔C3 boundary) | **Stands** (ICLR 2026 MemAgents workshop names this as unsolved). | Open problem. |
| Q6 (cross-project sync) | Stands. No SOTA prior art. | Principal judgment call. |
| Q7 (harness lock-in) | **Stands; resource exists.** | Reference Anthropic harness-design blog at L0; make trade-off explicit. |

### Sources

Cognitive substrate (Agent 1): [Zep arXiv:2501.13956](https://arxiv.org/abs/2501.13956) · [Graphiti / Neo4j](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/) · [MAST arXiv:2503.13657](https://arxiv.org/abs/2503.13657) · [Mem0 arXiv:2504.19413](https://arxiv.org/html/2504.19413v1) · [LangGraph memory docs](https://docs.langchain.com/oss/python/langgraph/memory) · [Letta sleep-time](https://docs.letta.com/guides/agents/architectures/sleeptime) · [Honeycomb Observability 2.0](https://www.honeycomb.io/blog/time-to-version-observability-signs-point-to-yes) · [Greptime agent observability](https://www.greptime.com/blogs/2025-12-11-agent-observability) · [OTel GenAI agent spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) · [ICLR 2026 MemAgents workshop](https://iclr.cc/virtual/2026/workshop/10000792)

Quality / calibration (Agent 2): [Hierarchical CP arXiv:2508.13288](https://arxiv.org/abs/2508.13288) · [Class-similarity CP arXiv:2511.19359](https://arxiv.org/html/2511.19359v1) · [LLM-informed priors arXiv:2509.04250](https://arxiv.org/html/2509.04250) · [Code Review for Claude Code](https://claude.com/blog/code-review) · [Refute-or-Promote arXiv:2604.19049](https://arxiv.org/html/2604.19049v1) · [Trust or Escalate ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/08dabd5345b37fffcbe335bd578b15a0-Paper-Conference.pdf) · [DGM arXiv:2505.22954](https://arxiv.org/abs/2505.22954) · [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) · [Claude Code changelog](https://code.claude.com/docs/en/changelog) · [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)
